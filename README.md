# Askari VMS

Local-first visitor management, e-tag, camera and gate-controller software for the
Askari 12 / Askari Greens main gate, Lahore.

**[AGENTS.md](AGENTS.md) is the spec of record.** Read it before changing anything —
it carries the hardware inventory, the controller HTTP protocol, the entry and exit
flows, and a list of traps already hit.

## Set up on a new machine

Needs **Python 3.12** ([python.org](https://www.python.org/downloads/), tick *Add
python.exe to PATH* during install).

```powershell
git clone https://github.com/waleed1914/askari12-main-gate.git
cd askari12-main-gate
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

The first install pulls PySide6, OpenCV and the ONNX runtime — roughly 600 MB, so do
it before you leave for the site, not on site.

Check it works:

```powershell
pytest          # 247 tests, all should pass
python -m askari_vms
```

## Signing in

Demo accounts all share the password `change-me-123`. **Change this before the system
goes live.**

| Username     | Role     | Lands on           |
|--------------|----------|--------------------|
| `admin`      | Admin    | Admin portal       |
| `supervisor` | Admin    | Admin portal       |
| `operator01` | Operator | Entry or Exit portal, per this PC's lane |
| `operator02` | Operator | Entry or Exit portal, per this PC's lane |
| `reporter`   | Reporter | Reports only       |

Which portal an operator lands on is set by **Settings → Workstation lane**, not by the
account. An admin always gets the admin portal and can open either portal from there.

A brand-new database seeds these accounts automatically. To add demonstration visits
and e-tag traffic as well:

```powershell
python -m askari_vms.seed
```

## Where the data lives

`C:\AskariVMS\data` — the SQLite database and captured CNIC images. It is outside the
repo on purpose and is never committed: it holds real cardholder data. Moving the app
to another machine does not move the data, and the folder is configurable in Settings.

## Hardware

Every device sits behind an adapter and the app runs fully in simulation until each one
is proven on site. The portals show **HARDWARE DISABLED — SIMULATION MODE** while that
is the case.

| Device | State |
|---|---|
| Door controllers (`192.168.0.90`) | Protocol documented in AGENTS.md, adapter not built |
| Dahua ANPR + driver cameras | Not built |
| Honeywell MK7120 barcode scanner | Works — it is a keyboard wedge, no driver needed |
| Black Copper BC-85AC receipt printer | ESC/POS path written; the unit on hand prints blank and is being replaced |
| Speech dictation (Ctrl+D) | Stub |
