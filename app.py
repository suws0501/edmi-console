from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.theme import Theme

from driver.interface.edmi_structs import EDMISurvey
from driver.interface.media import Media
from driver.interface.meter import Meter
from driver.serial_settings import BAUD, PORT, TIMEOUT_S
from driver.transport.serial_transport import SerialConfig, SerialTransport

app = typer.Typer(add_completion=False, help="EDMI console driver")
console = Console(
    theme=Theme(
        {
            "title": "bold cyan",
            "label": "bold",
            "value": "bold green",
            "warn": "bold yellow",
            "error": "bold red",
        }
    )
)


@dataclass(frozen=True)
class MeterConfig:
    username: str
    password: str
    serial_number: int


def _normalize_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _parse_meter_config(raw: str) -> MeterConfig:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) != 3:
        raise typer.BadParameter("Expected format: username,password,serial_number")
    username, password, serial_str = parts
    try:
        serial_number = int(serial_str)
    except ValueError as exc:
        raise typer.BadParameter("Serial number must be an integer") from exc
    return MeterConfig(username=username, password=password, serial_number=serial_number)


def _default_meter_config() -> str:
    try:
        from driver import meters_config as meter_defaults

        serial_number = getattr(meter_defaults, "SERIAL_NUMBER", "")
        username = getattr(meter_defaults, "USERNAME", "")
        password = getattr(meter_defaults, "PASWORD", "")
        if all([serial_number, username, password]):
            return f"{username},{password},{serial_number}"
    except Exception:
        pass
    return ""


def _prompt_serial_config() -> SerialConfig:
    console.print("Serial settings", style="title")
    port = Prompt.ask("Port", default=str(PORT))
    baudrate = IntPrompt.ask("Baudrate", default=int(BAUD))
    timeout_s = float(Prompt.ask("Read timeout (s)", default=str(TIMEOUT_S)))
    write_timeout_s = float(Prompt.ask("Write timeout (s)", default=str(TIMEOUT_S)))
    return SerialConfig(
        port=port,
        baudrate=baudrate,
        timeout_s=timeout_s,
        write_timeout_s=write_timeout_s,
    )


def _prompt_meter_config() -> MeterConfig:
    console.print("Meter config", style="title")
    default_config = _default_meter_config()
    raw = Prompt.ask(
        "Meter config string (username,password,serial_number)",
        default=default_config or None,
    )
    return _parse_meter_config(raw)


def _build_register_map() -> dict[str, object]:
    meter = Meter(username="", password="", serial_number=0, media=None)
    meter.init_all_registers()

    register_map: dict[str, object] = {}
    for reg in meter.regs or []:
        register_map[_normalize_key(reg.Name)] = reg

    try:
        from driver.edmi_enums import EDMI_REGISTER

        for reg in meter.regs or []:
            for enum in EDMI_REGISTER:
                if int(enum) == int(reg.Address):
                    register_map.setdefault(_normalize_key(enum.name), reg)
    except Exception:
        pass

    return register_map


def _print_register_catalog() -> None:
    meter = Meter(username="", password="", serial_number=0, media=None)
    meter.init_all_registers()
    table = Table(title="Available Registers")
    table.add_column("Enum Name", style="label")
    table.add_column("Friendly Name", style="value")
    table.add_column("Address", style="value")

    try:
        from driver.edmi_enums import EDMI_REGISTER

        enum_by_address = {int(enum): enum.name for enum in EDMI_REGISTER}
    except Exception:
        enum_by_address = {}

    for reg in meter.regs or []:
        enum_name = enum_by_address.get(int(reg.Address), "")
        table.add_row(enum_name, reg.Name, hex(int(reg.Address)))

    console.print(table)


def _prompt_registers() -> list[object]:
    register_map = _build_register_map()
    raw = Prompt.ask("Register names (comma-separated)")
    names = [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        raise typer.BadParameter("Provide at least one register name")

    selected = []
    missing = []
    for name in names:
        key = _normalize_key(name)
        reg = register_map.get(key)
        if reg is None:
            missing.append(name)
        else:
            selected.append(reg)

    if missing:
        console.print(
            f"Unknown registers: {', '.join(missing)}",
            style="warn",
        )
        _print_register_catalog()
        raise typer.Exit(code=1)

    return selected


def _prompt_survey() -> EDMISurvey:
    choices = [survey.name for survey in EDMISurvey]
    raw = Prompt.ask("Survey", choices=choices, default=choices[0])
    return EDMISurvey[raw]


def _prompt_datetime(label: str, default_dt: datetime) -> datetime:
    while True:
        raw = Prompt.ask(label, default=default_dt.isoformat(timespec="minutes"))
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            console.print("Use ISO format like 2025-01-31 12:30 or 2025-01-31T12:30", style="warn")


def _render_registers(regs: Iterable[object]) -> None:
    table = Table(title="Register Values")
    table.add_column("Name", style="label")
    table.add_column("Address", style="value")
    table.add_column("Value", style="value")
    table.add_column("Error", style="warn")

    for reg in regs:
        name = getattr(reg, "Name", "")
        address = hex(int(getattr(reg, "Address", 0)))
        value = str(getattr(reg, "Value", ""))
        error = str(getattr(reg, "ErrorCode", ""))
        table.add_row(name, address, value, error)

    console.print(table)


@app.command()
def run() -> None:
    """Interactive console driver."""
    console.print("EDMI Console", style="title")

    serial_cfg = _prompt_serial_config()
    meter_cfg = _prompt_meter_config()

    operation = Prompt.ask("Operation", choices=["registers", "profile"], default="registers")

    transport = SerialTransport(serial_cfg)
    media = Media(transport)

    try:
        transport.connect()
        console.print("Serial connected", style="value")

        if operation == "registers":
            regs = _prompt_registers()
            regs, err = media.edmi_read_registers(
                username=meter_cfg.username,
                password=meter_cfg.password,
                serial_number=meter_cfg.serial_number,
                regs=regs,
                keep_open=False,
            )
            if err is not None:
                console.print(f"Read registers error: {err}", style="warn")
            _render_registers(regs)
            return

        survey = _prompt_survey()
        now = datetime.now()
        default_from = now - timedelta(days=1)
        from_dt = _prompt_datetime("From datetime", default_from)
        to_dt = _prompt_datetime("To datetime", now)

        progress = Progress(
            TextColumn("[label]Reading profile"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        with progress:
            task_id = progress.add_task("profile", total=1)

            def _progress_cb(read: int, total: int) -> None:
                if total <= 0:
                    return
                if progress.tasks[task_id].total != total:
                    progress.update(task_id, total=total)
                progress.update(task_id, completed=read)

            profile_spec, fields, err = media.edmi_read_profile(
                username=meter_cfg.username,
                password=meter_cfg.password,
                serial_number=meter_cfg.serial_number,
                survey=int(survey),
                from_datetime=from_dt,
                to_datetime=to_dt,
                keep_open=False,
                progress_cb=_progress_cb,
            )

        if err is not None:
            console.print(f"Read profile error: {err}", style="warn")

        console.print(
            f"Survey {survey.name} records: {profile_spec.RecordsCount}",
            style="value",
        )
        console.print(f"Fields returned: {len(fields)}", style="value")

    except Exception as exc:
        console.print(f"Error: {exc}", style="error")
        raise
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        app()
    except KeyboardInterrupt:
        console.print("Interrupted", style="warn")
        sys.exit(130)
