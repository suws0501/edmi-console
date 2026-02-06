# EDMI Console

A polished, interactive console app for reading EDMI meter registers and load profiles over serial. The driver is written entirely in Python and connects to the meter using PySerial. Built with Typer + Rich for a modern terminal UX.

## Highlights
- Guided prompts for serial settings and meter configuration
- Read multiple registers by name
- Read profile data with a live progress bar
- Clean tabular output for register values

## Requirements
- Python 3.9+
- Serial access to the meter device

Install dependencies:

```bash
pip install -r requirements.txt
```

## Quick Start
Run the app:

```bash
python app.py
```

You’ll be prompted for:
- Serial settings (port, baudrate, timeouts)
- Meter config string in the format: `username,password,serial_number`
- Operation: `registers` or `profile`

## Register Reads
When choosing `registers`, enter a comma-separated list of register names. You can use either:
- Enum names like `PHASE_A_VOLTAGE`
- Friendly names like `Phase A Voltage Register`

If a name is unknown, the app prints a catalog of available registers.

## Profile Reads
When choosing `profile`, provide:
- Survey name (e.g. `LS01`, `LS03`)
- From/To datetimes in ISO format
  - `2025-01-31 12:30`
  - `2025-01-31T12:30`

A progress bar tracks records read in real time.

## Defaults
If present, defaults are loaded from:
- `driver/serial_settings.py`
- `driver/meters_config.py`

## Project Layout
- `app.py` — console application
- `driver/` — EDMI driver code (sync)
- `requirements.txt` — dependencies

## Tips
- Ensure your user has permission to access the serial device.
- If the meter is slow or busy, increase timeouts when prompted.

---

Feel free to fork this repo to add your own features. If you spot any bugs, please open an issue.

