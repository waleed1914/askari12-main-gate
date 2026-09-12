# Askari VMS continuation handoff

Updated: 2026-09-12

This is the safe continuation record for moving development to another PC. Read
`AGENTS.md` first; it contains the requirements, architecture, hardware assignments,
UI rules, controller protocol and backlog. Raw Codex chat/session files are deliberately
not committed because they include passwords and personal CNIC images. Do not add those
files, runtime databases or captured images to Git.

## Current verified state

- Entry ANPR autofill is connected through the Dahua event subscription adapter.
- Entry driver snapshot preview and evidence saving are integrated.
- USB CNIC-camera preview, reconnect watchdog, capture and offline OCR are integrated.
- Holding Ctrl+D records from `Microphone (USB Microphone)` and performs offline English
  destination dictation. Spoken numbers are converted to digits.
- A vehicle-category F1-F12 key starts the final Entry capture/OCR/submit/print workflow.
- SONIC SNC-830 printing was physically verified through direct USB ESC/POS using VID
  `0416`, PID `5011`; no unsigned manufacturer driver is required.
- Random eight-character CODE39 receipt tokens were printed and read successfully.
- Physical gate opening is still simulated.
- Test baseline at this handoff: `287 passed`.

## Set up another Windows PC

```powershell
git clone https://github.com/waleed1914/askari12-main-gate.git
cd askari12-main-gate
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
python -m askari_vms
```

Copy or download the offline Vosk model `vosk-model-small-en-us-0.15` and put its
extracted directory at:

```text
C:\AskariVMS\models\vosk-model-small-en-us-0.15
```

Camera/controller passwords are not in Git. Provision them separately in Windows
Credential Manager. Runtime data is under `C:\AskariVMS`; copy it separately only when
migrating the real Entry server, and protect it as production personal data.

## Immediate next work

1. Finish the Exit portal: ANPR, barcode/manual fallback, driver decision and evidence.
2. Implement Visitor Entry/Exit controller commands and live e-tag event polling.
3. Persist Settings and store controller secrets in Windows Credential Manager.
4. Add the operational Dashboard and camera-monitoring page.

Keep hardware behind adapters, keep flows non-blocking, and audit every decision and
hardware command. Use pages instead of modal popups and preserve the light green theme.
