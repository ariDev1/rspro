# rspro

Read live measurements from an RS PRO S3 / APPA S3 Bluetooth Low Energy multimeter on Omarchy (Arch Linux), using the `appa-dmm` sigrok driver.

The installer builds a pinned libsigrok fork with the [S3 timing patch](patches/libsigrok-appa-s3-timing.patch). The CLI loads that library from a user-local prefix. The source changes needed to reproduce the setup are in this repository.

## Install on Omarchy

Install the build tools, Bluetooth support, and sigrok CLI:

```bash
sudo pacman -S --needed base-devel git autoconf automake libtool pkgconf glib2 bluez bluez-utils libserialport libusb hidapi libzip sigrok-cli python
sudo systemctl enable --now bluetooth

git clone https://github.com/ariDev1/rspro.git
cd rspro
./install_rspro_cli.sh
```

The installer does not install system packages or use sudo. It fetches [Cymaphore/libsigrok](https://github.com/Cymaphore/libsigrok) at commit `8aa4dcd2973ae2a0f961afa61e0ecbcb4e3a92dc`, applies the repository patch, and builds into `~/.local/share/rspro-sigrok/prefix`. It installs `rspro` to `~/.local/bin`. Ensure that directory is on PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Each installation uses a fresh checkout under `~/.local/share/rspro-sigrok/builds`; existing source checkouts and debug logs are preserved. Re-running rebuilds the pinned library and replaces the CLI. Runtime version and supported-driver reports remain in the build directory. The installed Arch `sigrok-cli` is used; this is source reproducibility, not a bit-for-bit build or a pinned Arch package set.

## Read measurements

Turn on the meter and enable its Bluetooth function. Find its address using `bluetoothctl` (`power on`, `scan on`, then `scan off`). Disconnect any phone app using the meter before starting.

```bash
RSPRO_DEVICE=AA:BB:CC:DD:EE:FF rspro
```

Output includes the primary measurement, for example `27.57 V DC`. Use `--raw` for all sigrok output, including the secondary channel. Press Ctrl+C to stop.

```bash
RSPRO_DEVICE=AA:BB:CC:DD:EE:FF rspro --raw
rspro --help
```

For compatibility with the original local setup, the default address is `18:7A:93:E4:4D:E3`. Set `RSPRO_DEVICE` to your own meter address, optionally by exporting it in your shell configuration.

| Variable | Default | Purpose |
| --- | --- | --- |
| `RSPRO_DEVICE` | `18:7A:93:E4:4D:E3` | Colon-separated BLE address |
| `RSPRO_USE_SUDO` | `auto` | `auto`, `yes`, or `no`; auto probes discovery without sudo and retries execution with sudo if discovery fails |
| `RSPRO_ROOT` | `~/.local/share/rspro-sigrok` | Build root for installer and CLI |
| `RSPRO_SIGROK_BIN` | `sigrok-cli` | CLI executable, optionally an absolute path |
| `RSPRO_BIN_DIR` | `~/.local/bin` | Installer CLI destination |
| `RSPRO_JOBS` | `2` | Installer parallel build jobs |
| `RSPRO_SOURCE_URL` | Cymaphore GitHub repository | Installer source mirror containing the pinned commit |

When installing to a custom root, use the same `RSPRO_ROOT` when running the CLI. The CLI accepts paths containing spaces.

## Lab terminal dashboard

Switch on your S3 meters, enable their Bluetooth function, and start:

```bash
rspro dashboard
```

**No address configuration is required.** The dashboard uses BlueZ's known S3 devices and automatically scans for `RS PRO S3` and `APPA S3` advertisements. It repeats eight-second discovery rounds with two-second pauses, and adds meters as soon as their advertisement arrives. You can switch on more meters while the dashboard is running; they appear and begin capture without a restart. Other Bluetooth devices are ignored. Each automatically added meter gets its advertised name and address suffix as a label, for example `RS PRO S3 · 4D:E3`.

Update an existing installation without rebuilding the library:

```bash
./install_rspro_cli.sh --cli-only
```

Each meter has a stable row showing **Measurement** (the main meter reading), **Additional reading** (the auxiliary display value), connection state, and the age of the measurement. An `inf` reading is displayed as `∞`. Its address and latest diagnostic appear below. Disconnected meters remain visible with their last readings and reconnect independently. Primary readings older than five seconds are marked stale while capture is active. Capture retries every two seconds after exiting and restarts after fifteen seconds without any measurements.

Use ↑/↓ (or j/k), Page Up/Down, Home/End to scroll, and q or Ctrl+C to exit. Widen the terminal to see all columns. The footer shows discovery activity or errors. There is no configured meter-count limit, but simultaneous BLE capacity depends on the adapter and meters. Collection is independent, not synchronized sampling. Disconnect phone apps that are using these meters.

### Optional friendly names

Existing `~/.config/rspro/meters.json` files are preserved. They can supply lab names and previously known addresses; automatic discovery still adds other meters:

```json
{
  "meters": [
    {"name": "Power supply", "address": "18:7A:93:E4:4D:E3"}
  ]
}
```

You do not need to create this file. If you want friendly names, the dashboard shows each discovered meter's address. Configured or cached meters may be listed while switched off, with connection status indicating their availability. Addresses in a naming file must be unique. `RSPRO_DEVICE` continues to apply only to single-meter mode.

```bash
rspro dashboard --config ./my-lab.json
# Optional troubleshooting mode: capture only the explicitly configured meters.
rspro dashboard --configured-only
rspro dashboard --help
```

The optional default file respects `XDG_CONFIG_HOME`; `RSPRO_CONFIG` can override it. An explicitly supplied `--config` path must exist and be valid. `RSPRO_BLUETOOTH_BIN` can override the `bluetoothctl` executable for diagnostics. Running `./rspro dashboard` directly from the repository works before installation.

Python 3.11 or newer with standard `curses`, coreutils `stdbuf`, and `bluetoothctl` from `bluez-utils` are required. No third-party Python packages are needed. Discovery uses your user's BlueZ access. In `RSPRO_USE_SUDO=auto`, startup probes the first already-known meter for up to six seconds and authenticates sudo before curses if necessary. With no known meters it begins discovery and capture as the normal user. If your machine requires privileged capture, use `RSPRO_USE_SUDO=yes rspro dashboard`; authentication happens once before the table opens. `no` forces user-only capture. Collectors never prompt inside curses. If sudo credentials expire during reconnect, exit and restart to authenticate again.

## Troubleshooting

- If the local library is missing, run the installer. A system libsigrok alone does not provide this patched setup.
- If discovery fails, check the address, meter Bluetooth mode, adapter state, and whether another client is connected. Auto sudo fallback can also occur for these failures; it does not prove a permissions problem. Use `RSPRO_USE_SUDO=no` to diagnose without escalation, or `yes` where raw Bluetooth access requires root.
- If the meter is detected as `APPA N/A` or capture immediately stops, see the [bug investigation](docs/s3-timing-bug.md).
- If the original single-meter command works but an older dashboard repeatedly reconnects without readings, update with `./install_rspro_cli.sh --cli-only`. The [dashboard stdin fix](docs/dashboard-stdin-bug.md) keeps capture input open so sigrok does not stop on EOF.
- For verbose diagnostics, use the installed library explicitly:

  ```bash
  sudo env LD_LIBRARY_PATH="$HOME/.local/share/rspro-sigrok/prefix/lib" \
    sigrok-cli -l 5 -d 'appa-dmm:conn=bt/appa-dmm/AA-BB-CC-DD-EE-FF' --continuous
  ```

  Use `prefix/lib64` instead if your build installs there. Logs can include device addresses and measurement data.
- An Arch package upgrade may change compatibility between `sigrok-cli` and the fork. Inspect the installer's `runtime-version.txt` and `supported.txt`, and rerun the installer after upgrades.

## Development and verification

```bash
python3 -m unittest discover -s tests -v
bash -n rspro install_rspro_cli.sh
```

Tests exercise argument validation, output filtering, raw output, error propagation, configuration validation, automatic discovery and adding meters during capture, concurrent collection/retry/cleanup, curses scrolling and resizing through a pseudo-terminal, and isolated CLI-only installation. CLI tests use a stand-in for sigrok, so they do not require Bluetooth hardware. A real installation verifies library loading and the `appa-dmm` driver without connecting to a meter. Hardware capture must be checked separately on the target machine.

The wrapper and installer use the repository MIT license. The patch modifies GPL-3.0-or-later libsigrok code and is provided under GPL-3.0-or-later; libsigrok retains its upstream license.
