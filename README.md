# rspro

See the readings from several Bluetooth multimeters together in one terminal window.

rspro finds your meters automatically and shows their live readings, connection status, and a small chart of recent changes. You can save readings from one meter, several meters, or all of them to a file you can open in a spreadsheet.

## What you need

- An **RS PRO S3** or **APPA S3** multimeter with Bluetooth enabled. Other models are not currently supported by automatic discovery.
- A computer running **Omarchy (Arch Linux)** with Bluetooth.
- An internet connection for installation.

The number of meters you can use at once depends on your computer's Bluetooth adapter. You do not need to enter meter addresses to use the dashboard.

## Install

Open a terminal and run these commands. The first commands install the required software and start Bluetooth; they may ask for your password.

```bash
sudo pacman -S --needed base-devel git autoconf automake libtool pkgconf glib2 bluez bluez-utils libserialport libusb hidapi libzip sigrok-cli python
sudo systemctl enable --now bluetooth
```

Then download and install rspro:

```bash
git clone https://github.com/ariDev1/rspro.git
cd rspro
./install_rspro_cli.sh
```

Installation builds the software needed to communicate with the meters, so it can take a few minutes.

If the terminal later says `rspro: command not found`, run this in that terminal:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Show your meters

1. Turn on each meter and enable its Bluetooth function.
2. Disconnect any phone app that is using those meters.
3. Start the dashboard:

```bash
rspro dashboard
```

Allow a little time for the meters to appear and connect. You can turn on another meter while the dashboard is running; it will be added automatically.

Each meter has its own row:

| Column | What it tells you |
| --- | --- |
| Meter | The meter model and the last part of its Bluetooth address, so you can tell meters apart |
| Measurement | The main reading, such as `27.57 V DC` |
| Additional reading | A second value sent by the meter; its meaning depends on the measurement mode |
| Status | Whether the meter is connecting, connected, or disconnected |
| Age | How long ago the main reading arrived |

`∞` means the meter sent an infinite or out-of-range value. A **stale** reading has not updated recently. If a meter disconnects, its last reading stays visible while rspro tries to reconnect.

The chart below each meter shows recent changes and the lowest and highest values in that history. Each chart has its own scale, so compare the numbers as well as the shapes.

## Keyboard controls

| Key | Action |
| --- | --- |
| ↑ / ↓ | Scroll through the meters |
| Page Up / Page Down | Scroll a page at a time |
| t | Hide or show the charts |
| r | Start or stop recording |
| q or Ctrl+C | Quit and finish any recording |

Widen your terminal window if the columns do not fit.

## Save readings

Press **r** in the dashboard. A list of meters opens:

- For one meter, highlight it with ↑ / ↓ and press **Enter**.
- For several meters, press **Space** on each one, then **Enter**.
- For every meter, press **a**, then **Enter**. This also records meters that appear later.
- Press **Esc** to cancel.

Press **r** again to stop recording. The other meters keep updating even when you only record one of them.

Recordings are saved in:

```text
~/.local/share/rspro/recordings/
```

On exit, rspro prints the full saved path. Open the `.csv` file in LibreOffice Calc or another spreadsheet program. It contains the time, meter identity, reading, and unit for each update. A meter's main and additional readings are saved as separate rows.

Each recording also has a `.meta.json` file that tracks whether it finished successfully. Keep it with the CSV when moving or sharing the recording. If rspro reports a recording error, do not assume every reading was saved.

To choose a filename and record all meters immediately:

```bash
rspro dashboard --record ./lab-session.csv
```

Existing files are never overwritten. Use a new filename for each session.

## Highlight readings outside a range

For example, to highlight DC voltage below 24 V or above 30 V:

```bash
rspro dashboard --below 24 --above 30 --unit V --mode DC
```

The dashboard shows **LOW** or **HIGH** beside readings outside that range. These are visual indicators; they do not make a sound or control your equipment.

## Update

Quit rspro, open a terminal in the `rspro` folder you downloaded, and run:

```bash
git pull --ff-only
./install_rspro_cli.sh --cli-only
```

Then start `rspro dashboard` again. Your saved meter names are preserved.

## If something does not work

**No meters appear:** Check that Bluetooth is on for both the computer and the meters. Close any phone app connected to them, wait for discovery, and check the message at the bottom of the dashboard.

**A meter appears but has no readings:** Give it time to connect. If your computer requires administrator access to read the meters, quit and try:

```bash
RSPRO_USE_SUDO=yes rspro dashboard
```

This asks for your password before opening the dashboard.

**rspro says its library is missing:** In the downloaded `rspro` folder, run `./install_rspro_cli.sh` again, without `--cli-only`.

**Recording stops with an error:** Check the message at the bottom of the dashboard and make sure you have free disk space and permission to write to the destination. After fixing the problem, press **r** to start a new recording.

## Project status and further help

The current version, **0.2.0-rc.1**, is still being tested. Automated tests cover connection failures and recording shutdown, but the planned eight-hour test with real meters has not yet been completed. See the [hardware test guide](docs/reliability.md) for the procedure.

For optional meter names, single-meter commands, recording checks, and advanced settings, see the [technical reference](docs/reference.md). Developer checks are explained in [the development guide](docs/ci.md). The [S3 communication bug investigation](docs/s3-timing-bug.md) explains why the installer includes a patched communication library.

The rspro scripts use the MIT license. The libsigrok patch uses GPL-3.0-or-later; libsigrok retains its upstream license.
