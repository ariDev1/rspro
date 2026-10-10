# Development checks

GitHub Actions runs on Ubuntu 24.04 with Python 3.11 (the minimum supported
version) and Python 3.14. Checks cover Bash syntax, Python compilation, Ruff's
functional `F`/`E9` lint rules, and the complete unittest suite. The suite includes
a CLI-only installation into a temporary home directory and checks the installed
dashboard's help output. It does not require a connected meter or build libsigrok.

Install Python 3.11 or newer and the Linux `bluetoothctl` utility first: the
package is `bluez` on Ubuntu/Debian or `bluez-utils` on Arch. The tests use Linux
PTYs and standard Linux utilities (`bash`, `stdbuf`, and `install`). No Bluetooth
service or hardware is required for these checks.

Set up an isolated development environment from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

Run the same checks locally:

```bash
bash -n rspro install_rspro_cli.sh &&
.venv/bin/python -m compileall -q rspro_*.py tests tools &&
.venv/bin/python -m ruff check . &&
.venv/bin/python -m unittest discover -s tests -v
```

GitHub actions use checkout v6.1.0 and setup-python v6.3.0, pinned to verified
release commit SHAs. Both use the Node 24 runtime. Ruff is pinned in
`requirements-dev.txt` and is a development dependency only. CI does not exercise
the full libsigrok build or real meter capture; those require separate hardware
and build validation.
