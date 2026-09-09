# Askari VMS

Local-first visitor management, e-tag, camera, and gate-controller software.

## Current milestone

The first milestone contains the shared desktop foundation and the Admin portal
shell. Hardware calls are deliberately kept behind adapters so the UI can be
completed and tested before physical devices are integrated.

## Run locally

```powershell
python -m pip install -e ".[dev]"
python -m askari_vms
```

Run tests with:

```powershell
pytest
```

