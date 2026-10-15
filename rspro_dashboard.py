#!/usr/bin/env python3
"""Concurrent RS PRO / APPA S3 terminal dashboard (standard library only)."""
import argparse
from collections import deque
import curses
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from rspro_capture import Collector as Collector, clean_text, stop_process as stop_process
from rspro_discovery import adapter_supports_le as adapter_supports_le
from rspro_discovery import Discovery as Discovery, cached_meters, parse_discovery_line as parse_discovery_line
from rspro_features import Limits, RecordingSession, app_version, make_sample, parse_reading, sparkline

STALE_AFTER = 5.0
MAC = re.compile(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z')
BLE_REQUIRED_MESSAGE = (
    'The Bluetooth adapter on this computer does not support Bluetooth Low Energy (BLE), '
    'which RS PRO S3 and APPA S3 meters require to be discovered. Use a Bluetooth 4.0 or '
    'newer adapter, and make sure it is the one selected by BlueZ.')


class MeterState:
    def __init__(self, name, address, limits=None, recording=None):
        self.name = clean_text(name, 80)
        self.address = address
        self.primary = '—'
        self.secondary = '—'
        self.updated = None
        self.primary_current = False
        self.last_sample = None
        self.status = 'Connecting'
        self.error = ''
        self.numeric = parse_reading('—')
        self.history = deque(maxlen=120)
        self.history_key = ('', '')
        self.limits = limits
        self.recording = recording
        self.lock = threading.Lock()

    def update_line(self, line, now=None):
        now = time.monotonic() if now is None else now
        for channel in ('Primary', 'Secondary'):
            if line.startswith(channel + ':'):
                with self.lock:
                    text = clean_text(line.split(':', 1)[1].strip())
                    setattr(self, channel.lower(), text)
                    if channel == 'Primary':
                        self.updated = now
                        self.primary_current = True
                        self.numeric = parse_reading(text)
                        key = (self.numeric.unit, self.numeric.mode)
                        if key != self.history_key or self.numeric.value is None:
                            self.history.clear()
                        self.history_key = key
                        if self.numeric.value is not None:
                            self.history.append(self.numeric.value)
                    self.last_sample = now
                    self.status = 'Connected'
                    self.error = ''
                if self.recording:
                    kind = 'measurement' if channel == 'Primary' else 'additional'
                    self.recording.record(make_sample(self.address, self.name, kind, text))
                return True
        return False

    def set_status(self, status, error=None):
        with self.lock:
            self.status = status
            if status in ('Connecting', 'Reconnecting', 'Stopped'):
                self.primary_current = False
                self.history.clear()
            if error is not None:
                self.error = clean_text(error)

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            age = None if self.updated is None else max(0, now - self.updated)
            status = self.status
            if status == 'Connected' and age is not None and age >= STALE_AFTER:
                status = 'Stale'
            alert, alert_text = False, ''
            if self.limits:
                if status == 'Connected' and age is not None and self.primary_current:
                    alert, alert_text = self.limits.evaluate(self.numeric)
                else:
                    alert_text = 'Limits: no fresh measurement'
            return dict(name=self.name, address=self.address, primary=self.primary,
                        secondary=self.secondary, age=age, status=status, error=self.error,
                        history=list(self.history), numeric=self.numeric,
                        alert=alert, alert_text=alert_text)


def load_meters(path, default_limits=None, recording=None):
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError as exc:
        raise ValueError(f'No meter configuration at {path}. Copy meters.example.json '
                         'there and enter your meter names and addresses.') from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot read meter configuration {path}: {exc}') from exc
    if not isinstance(data, dict) or not isinstance(data.get('meters'), list) or not data['meters']:
        raise ValueError('Configuration must contain a nonempty "meters" list.')
    meters, seen = [], set()
    for index, item in enumerate(data['meters'], 1):
        if not isinstance(item, dict):
            raise ValueError(f'Meter {index} must be an object with name and address.')
        name, address = item.get('name'), item.get('address')
        if not isinstance(name, str) or not name.strip() or not clean_text(name).strip():
            raise ValueError(f'Meter {index} requires a nonempty name.')
        if not isinstance(address, str) or not MAC.fullmatch(address):
            raise ValueError(f'Meter {index} requires a colon-separated BLE address.')
        address = address.upper()
        if address in seen:
            raise ValueError(f'Duplicate meter address: {address}')
        seen.add(address)
        limits = default_limits
        if 'limits' in item:
            definition = item['limits']
            if definition is None:
                limits = None
            else:
                if not isinstance(definition, dict):
                    raise ValueError(f'Meter {index}: limits must be an object or null.')
                try:
                    limits = Limits(**definition)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f'Meter {index}: invalid limits: {exc}') from exc
        meters.append(MeterState(name.strip(), address, limits=limits, recording=recording))
    return meters



def capture_environment():
    root = Path(os.environ.get('RSPRO_ROOT', str(Path.home() / '.local/share/rspro-sigrok')))
    libdir = root / 'prefix/lib64'
    if not libdir.is_dir():
        libdir = root / 'prefix/lib'
    if not libdir.is_dir():
        raise ValueError(f'Patched libsigrok not found at {libdir}; run the installer.')
    binary = shutil.which(os.environ.get('RSPRO_SIGROK_BIN', 'sigrok-cli'))
    stdbuf = shutil.which('stdbuf')
    if not binary or not stdbuf:
        raise ValueError('sigrok-cli and coreutils stdbuf must be installed.')
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = str(libdir) + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    return binary, stdbuf, env


def needs_sudo(binary, env, meter=None):
    mode = os.environ.get('RSPRO_USE_SUDO', 'auto')
    if mode not in ('auto', 'yes', 'no'):
        raise ValueError('RSPRO_USE_SUDO must be auto, yes, or no.')
    if mode == 'no' or (mode == 'auto' and meter is None):
        # Start discovery as the user when no address is known yet. Machines
        # requiring privileged capture can explicitly select mode=yes.
        return False
    if mode == 'auto' and meter is not None:
        try:
            result = subprocess.run([binary, '-d',
                f'appa-dmm:conn=bt/appa-dmm/{meter.address.replace(":", "-")}', '--scan'],
                env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=6)
            if result.returncode == 0 and re.search(rb'^appa-dmm\s+-', result.stdout, re.MULTILINE):
                return False
        except subprocess.TimeoutExpired:
            pass
    if not shutil.which('sudo'):
        raise ValueError('sudo is required for this capture mode but is not installed.')
    print('Validating Bluetooth capture permissions before opening the dashboard…', flush=True)
    if subprocess.run(['sudo', '-v']).returncode:
        raise ValueError('sudo authentication failed. Use RSPRO_USE_SUDO=no for user-only capture.')
    return True


def capture_command(binary, stdbuf, env, meter, privileged):
    command = [stdbuf, '-oL', '-eL', binary, '-d',
               f'appa-dmm:conn=bt/appa-dmm/{meter.address.replace(":", "-")}', '--continuous']
    if privileged:
        command = ['sudo', '-n', 'env', f'LD_LIBRARY_PATH={env["LD_LIBRARY_PATH"]}', *command]
    return command


def draw_line(screen, y, text, attribute=0, x=0):
    height, width = screen.getmaxyx()
    if 0 <= y < height and 0 <= x < width - 1:
        try:
            screen.addnstr(y, x, clean_text(text, 2000), width - x - 1, attribute)
        except curses.error:
            # Wide characters and resize events can reach the terminal edge.
            pass



class RecordingPicker:
    def __init__(self, meters):
        self.cursor = 1 if meters else 0
        self.selected = set()
        self.all_meters = False

    def handle(self, key, meters):
        self.cursor = max(0, min(self.cursor, len(meters)))
        if key in (27, ord('q'), ord('Q')):
            return 'cancel', None
        if key in (curses.KEY_DOWN, ord('j')):
            self.cursor = min(len(meters), self.cursor + 1)
        elif key in (curses.KEY_UP, ord('k')):
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_HOME:
            self.cursor = 0
        elif key == curses.KEY_END:
            self.cursor = len(meters)
        elif key == curses.KEY_NPAGE:
            self.cursor = min(len(meters), self.cursor + 5)
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 5)
        elif key == ord('a'):
            self.all_meters = True
            self.selected.clear()
            self.cursor = 0
        elif key == ord(' '):
            if self.cursor == 0:
                self.all_meters = not self.all_meters
                self.selected.clear()
            else:
                address = meters[self.cursor - 1].address
                if self.all_meters:
                    self.selected = {m.address for m in meters}
                    self.all_meters = False
                if address in self.selected:
                    self.selected.remove(address)
                else:
                    self.selected.add(address)
        elif key in (10, 13, curses.KEY_ENTER):
            if self.all_meters:
                return 'start', None
            if self.selected:
                return 'start', set(self.selected)
            return 'start', None if self.cursor == 0 else {meters[self.cursor - 1].address}
        return None, None

    def draw(self, screen, meters):
        screen.erase()
        height, _ = screen.getmaxyx()
        self.cursor = min(self.cursor, len(meters))
        draw_line(screen, 0, 'Choose meters to record', curses.A_BOLD)
        draw_line(screen, 1, '↑/↓ choose | Enter record | Space select several | a all | Esc cancel')
        count = max(1, height - 4)
        offset = max(0, min(self.cursor - count + 1, len(meters) + 1 - count))
        entries = ['All meters (including meters discovered later)'] + [f'{m.name}  ({m.address})' for m in meters]
        for row, index in enumerate(range(offset, min(len(entries), offset + count))):
            checked = self.all_meters if index == 0 else self.all_meters or meters[index - 1].address in self.selected
            marker = '[x]' if checked else '[ ]'
            draw_line(screen, row + 2, f'{marker} {entries[index]}', curses.A_REVERSE if index == self.cursor else 0)
        if self.all_meters:
            hint = 'Enter: record all meters'
        elif self.selected:
            hint = f'Enter: record {len(self.selected)} selected meters'
        else:
            hint = 'Enter: record highlighted meter (or All)'
        draw_line(screen, height - 1, hint, curses.A_DIM)
        screen.refresh()

def dashboard(screen, meters, collectors, discovery=None, make_collector=None,
              recording=None, default_limits=None):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(200)
    curses.set_escdelay(100)
    alert_attribute = curses.A_BOLD
    trend_attribute = 0
    try:
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_RED, -1)
            alert_attribute |= curses.color_pair(1)
            # Use the terminal theme's yellow accent and default background.
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            trend_attribute = curses.color_pair(2)
    except curses.error:
        pass
    show_trends = True
    picker = None
    offset = 0
    started = []
    seen = {meter.address for meter in meters}
    try:
        if discovery:
            discovery.start()
        for collector in collectors:
            collector.start()
            started.append(collector)
        while True:
            discovery_status = 'Configured meters only'
            if discovery:
                found, discovery_status = discovery.snapshot()
                for address, name in found.items():
                    if address not in seen:
                        meter = MeterState(f'{name} · {address[-5:]}', address,
                                           limits=default_limits, recording=recording)
                        collector = make_collector(meter)
                        collector.start()
                        started.append(collector)
                        meters.append(meter)
                        seen.add(address)
            if picker is not None:
                picker.draw(screen, meters)
                action, addresses = picker.handle(screen.getch(), meters)
                if action == 'start':
                    recording.start(addresses=addresses)
                    picker = None
                elif action == 'cancel':
                    picker = None
                continue
            height, width = screen.getmaxyx()
            row_height = 3 if show_trends else 2
            page = max(1, (height - 4) // row_height)
            offset = min(max(0, offset), max(0, len(meters) - page))
            screen.erase()
            draw_line(screen, 0, f'RS PRO lab | {len(meters)} meters | q quit | r record | t trends | ↑/↓ scroll', curses.A_BOLD)
            draw_line(screen, 1, f'{"Meter":24} {"Measurement":20} {"Additional reading":18} {"Status":13} Age', curses.A_BOLD)
            now = time.monotonic()
            for row, meter in enumerate(meters[offset:offset + page]):
                snap = meter.snapshot(now)
                age = '—' if snap['age'] is None else f'{snap["age"]:.1f}s'
                primary, secondary = ('∞' if value == 'inf' else value
                                      for value in (snap['primary'], snap['secondary']))
                y = 2 + row * row_height
                draw_line(screen, y,
                          f'{snap["name"][:24]:24} {primary[:20]:20} {secondary[:18]:18} '
                          f'{snap["status"]:13} {age}', alert_attribute if snap['alert'] else 0)
                detail = snap['address']
                if snap['alert_text']:
                    detail += ' | ' + snap['alert_text']
                if snap['error']:
                    detail += ' | ' + snap['error']
                draw_line(screen, y + 1, detail, alert_attribute if snap['alert'] else curses.A_DIM)
                if show_trends:
                    history, numeric = snap['history'], snap['numeric']
                    if history:
                        units = numeric.unit + (' ' + numeric.mode if numeric.mode else '')
                        graph = sparkline(history, max(1, width - 45))
                        trend = f'Trend {graph} | {min(history):g}…{max(history):g} {units}'
                    else:
                        trend = 'Trend — waiting for numeric measurements'
                    draw_line(screen, y + 2, trend, curses.A_DIM)
                    if history:
                        draw_line(screen, y + 2, graph, trend_attribute, x=6)
            if not meters:
                draw_line(screen, 3, 'Looking for S3 meters… Switch on your meters and enable Bluetooth.')
            footer = f'Meters {offset + 1 if meters else 0}–{min(len(meters), offset + page)} of {len(meters)} | {discovery_status}'
            if width < 70:
                footer = 'Widen terminal for all columns | q quit'
            if recording:
                info = recording.snapshot()
                if info['error']:
                    record_status = info['error']
                elif info['active']:
                    scope = 'all meters' if info['addresses'] is None else f'{len(info["addresses"])} selected'
                    record_status = f'REC {scope} | {Path(info["path"]).name} | {info["written"]} updates | r stop'
                elif info['path']:
                    record_status = f'Saved {Path(info["path"]).name} | {info["written"]} channel updates | r record'
                else:
                    record_status = 'Recording off | r start recording'
                draw_line(screen, height - 2, record_status,
                          alert_attribute if info['error'] else curses.A_DIM)
            draw_line(screen, height - 1, footer, curses.A_DIM)
            screen.refresh()
            key = screen.getch()
            if key in (ord('q'), ord('Q'), 3):
                break
            if key in (ord('r'), ord('R')) and recording:
                if recording.snapshot()['active']:
                    recording.stop()
                else:
                    picker = RecordingPicker(meters)
            elif key in (ord('t'), ord('T')):
                show_trends = not show_trends
            elif key in (curses.KEY_DOWN, ord('j')):
                offset += 1
            elif key in (curses.KEY_UP, ord('k')):
                offset -= 1
            elif key == curses.KEY_NPAGE:
                offset += page
            elif key == curses.KEY_PPAGE:
                offset -= page
            elif key == curses.KEY_HOME:
                offset = 0
            elif key == curses.KEY_END:
                offset = len(meters) - page
    finally:
        if discovery:
            discovery.stop()
        for collector in started:
            collector.stop()
        if discovery and discovery.is_alive():
            discovery.join(timeout=4)
        for collector in started:
            collector.join(timeout=4)
        if recording:
            recording.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Automatically discover S3 meters and display their live measurements.')
    default = os.environ.get('RSPRO_CONFIG', str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'rspro/meters.json'))
    parser.add_argument('--config', default=None, metavar='PATH', help=f'optional meter names/configuration (default: {default})')
    parser.add_argument('--configured-only', action='store_true', help='disable automatic discovery and use only the configuration')
    parser.add_argument('--version', action='version', version=f'rspro {app_version()}')
    parser.add_argument('--record', metavar='PATH', help='start CSV recording to a new file (r toggles recording in the dashboard)')
    parser.add_argument('--below', type=float, metavar='VALUE', help='highlight measurements below this limit')
    parser.add_argument('--above', type=float, metavar='VALUE', help='highlight measurements above this limit')
    parser.add_argument('--unit', help='required unit for limits, for example V or mV')
    parser.add_argument('--mode', help='optional measurement mode for limits, for example DC or AC')
    args = parser.parse_args(argv)
    recording = RecordingSession()
    result = 0
    try:
        limits = None
        if any(value is not None for value in (args.below, args.above, args.unit, args.mode)):
            limits = Limits(below=args.below, above=args.above, unit=args.unit or '', mode=args.mode)
        config = args.config or default
        meters = load_meters(config, default_limits=limits, recording=recording) if args.config or Path(config).exists() else []
        if args.configured_only and not meters:
            raise ValueError('--configured-only requires a meter configuration.')
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError('The dashboard requires an interactive terminal.')
        btmgmt = shutil.which(os.environ.get('RSPRO_BTMGMT_BIN', 'btmgmt'))
        if adapter_supports_le(btmgmt, os.environ.copy()) is False:
            raise ValueError(BLE_REQUIRED_MESSAGE)
        binary, stdbuf, env = capture_environment()
        discovery = None
        if not args.configured_only:
            bluetooth = shutil.which(os.environ.get('RSPRO_BLUETOOTH_BIN', 'bluetoothctl'))
            if not bluetooth:
                raise ValueError('bluetoothctl is required for discovery; install bluez-utils.')
            seen = {meter.address for meter in meters}
            for address, name in cached_meters(bluetooth, env).items():
                if address not in seen:
                    meters.append(MeterState(f'{name} · {address[-5:]}', address,
                                             limits=limits, recording=recording))
            discovery = Discovery(bluetooth, env)
        privileged = needs_sudo(binary, env, meters[0] if meters else None)
        if args.record and not recording.start(args.record):
            raise ValueError(recording.snapshot()['error'])
        def make_collector(meter):
            return Collector(meter, capture_command(binary, stdbuf, env, meter, privileged), env)
        collectors = [make_collector(meter) for meter in meters]
        previous = {}
        def interrupt(signum, frame):
            raise KeyboardInterrupt
        try:
            for signum in (signal.SIGTERM, signal.SIGHUP):
                previous[signum] = signal.signal(signum, interrupt)
            curses.wrapper(dashboard, meters, collectors, discovery, make_collector, recording, limits)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, curses.error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        result = 1
    finally:
        recording.stop()
    info = recording.snapshot()
    if info['error']:
        if result == 0:
            print(f'ERROR: {info["error"]}', file=sys.stderr)
        result = 1
    elif info['path']:
        print(f'Recording saved to {info["path"]} ({info["written"]} channel updates).')
    return result


if __name__ == '__main__':
    sys.exit(main())
