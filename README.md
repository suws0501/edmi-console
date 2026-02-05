# EDMI Console

A clean, interactive console app for reading EDMI meter registers and load profiles over serial. Built on Typer + Rich for a polished CLI experience.

## Features
- Guided prompts for serial settings and meter configuration
- Read multiple registers by name
- Read profile data with a live progress bar
- Friendly tables for register output

## Requirements
- Python 3.9+
- Serial access to the meter

Install dependencies:

```bash
pip install -r requirements.txt
```

## Quick Start
Run the app:

```bash
python app.py
```

You will be prompted for:
- Serial settings (port, baud, timeouts)
- Meter config string in the format: `username,password,serial_number`
- Operation: `registers` or `profile`

## Register Read
When choosing `registers`, enter a comma-separated list of register names. You can use either the enum name (e.g. `PHASE_A_VOLTAGE`) or the friendly name (e.g. `Phase A Voltage Register`).

If a name is unknown, the app will show a catalog of available registers.

## Profile Read
When choosing `profile`, provide:
- Survey name (e.g. `LS01`, `LS03`)
- From/To datetimes in ISO format (e.g. `2025-01-31 12:30` or `2025-01-31T12:30`)

A progress bar tracks records read in real time.

## Defaults
If available, defaults are pulled from:
- `driver/serial_settings.py` for serial settings
- `driver/meters_config.py` for meter config

## File Layout
- `app.py` — console application
- `driver/` — EDMI driver code (sync)
- `requirements.txt` — dependencies

## Notes
- Make sure your user account has permission to access the serial device.
- If the meter is slow, increase timeouts when prompted.

---

If you want this to support additional operations or output formats (CSV/JSON), tell me what you want and I’ll add it.
