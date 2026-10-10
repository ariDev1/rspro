#!/usr/bin/env python3
"""Concurrent RS PRO / APPA S3 terminal dashboard (standard library only)."""
import argparse
import curses
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time

STALE_AFTER = 5.0
RETRY_DELAY = 2.0
IDLE_TIMEOUT = 15.0
MAC = re.compile(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z')


def clean_text(value, limit=160):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))[:limit]


class MeterState:
    def __init__(self, name, address):
        self.name = clean_text(name, 80)
        self.address = address
        self.primary = '—'
        self.secondary = '—'
        self.updated = None
        self.last_sample = None
        self.status = 'Connecting'
        self.error = ''
        self.lock = threading.Lock()

    def update_line(self, line, now=None):
        now = time.monotonic() if now is None else now
        for channel in ('Primary', 'Secondary'):
            if line.startswith(channel + ':'):
                with self.lock:
                    setattr(self, channel.lower(), clean_text(line.split(':', 1)[1].strip()))
                    if channel == 'Primary':
                        self.updated = now
                    self.last_sample = now
                    self.status = 'Connected'
                    self.error = ''
                return True
        return False

    def set_status(self, status, error=None):
        with self.lock:
            self.status = status
            if error is not None:
                self.error = clean_text(error)

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            age = None if self.updated is None else max(0, now - self.updated)
            status = self.status
            if status == 'Connected' and age is not None and age >= STALE_AFTER:
                status = 'Stale'
            return dict(name=self.name, address=self.address, primary=self.primary,
                        secondary=self.secondary, age=age, status=status, error=self.error)


def load_meters(path):
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
        meters.append(MeterState(name.strip(), address))
    return meters


def stop_process(process):
    """Terminate the capture group, including helpers, then reap its leader."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    # A helper can outlive an already-reaped leader and ignore SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=1)


class Collector(threading.Thread):
    def __init__(self, state, command, env, retry_delay=RETRY_DELAY,
                 idle_timeout=IDLE_TIMEOUT):
        super().__init__(name=f'meter-{state.address}', daemon=True)
        self.state, self.command, self.env = state, command, env
        self.retry_delay, self.idle_timeout = retry_delay, idle_timeout
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()

    def run(self):
        while not self.stopping.is_set():
            self.state.set_status('Connecting')
            try:
                self.capture()
            except (OSError, subprocess.SubprocessError) as exc:
                self.state.set_status('Reconnecting', str(exc))
            if not self.stopping.is_set():
                self.state.set_status('Reconnecting')
                self.stopping.wait(self.retry_delay)
        self.state.set_status('Stopped')

    def capture(self):
        # sigrok treats EOF on stdin as a stop request. Keep a private pipe open
        # so only the dashboard reads terminal keys and captures keep running.
        process = subprocess.Popen(self.command, env=self.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   process_group=0)
        last_sample = time.monotonic()
        buffers = {'out': b'', 'err': b''}
        try:
            with selectors.DefaultSelector() as poller:
                poller.register(process.stdout, selectors.EVENT_READ, 'out')
                poller.register(process.stderr, selectors.EVENT_READ, 'err')
                while not self.stopping.is_set():
                    for key, _ in poller.select(timeout=.2):
                        data = os.read(key.fileobj.fileno(), 4096)
                        channel = key.data
                        if not data:
                            poller.unregister(key.fileobj)
                            # sigrok normally emits complete lines, but preserve a final fragment.
                            data, buffers[channel] = buffers[channel] + b'\n', b''
                        else:
                            data = buffers[channel] + data
                        lines = data.split(b'\n')
                        buffers[channel] = lines.pop()[-4096:]
                        for raw in lines:
                            line = raw.decode('utf-8', errors='replace').strip()
                            if channel == 'out' and self.state.update_line(line):
                                last_sample = time.monotonic()
                            elif channel == 'err' and line:
                                with self.state.lock:
                                    self.state.error = clean_text(line)
                        if channel == 'err' and buffers[channel]:
                            with self.state.lock:
                                self.state.error = clean_text(buffers[channel].decode('utf-8', 'replace'))
                    if process.poll() is not None and not poller.get_map():
                        with self.state.lock:
                            error = self.state.error or f'Capture exited ({process.returncode})'
                        self.state.set_status('Reconnecting', error)
                        break
                    if time.monotonic() - last_sample >= self.idle_timeout:
                        self.state.set_status('Reconnecting', 'No measurements; restarting capture')
                        break
        finally:
            stop_process(process)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()



def parse_discovery_line(line):
    line = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', line)
    match = re.search(r'(?:^|\]\s+)Device\s+((?:[\da-fA-F]{2}:){5}[\da-fA-F]{2})\s+(.+)$', line)
    if not match:
        return None
    address, name = match.groups()
    name = re.sub(r'^(?:Name|Alias):\s*', '', name).strip()
    if name.upper() not in ('RS PRO S3', 'APPA S3'):
        return None
    return address.upper(), name


def cached_meters(binary, env):
    try:
        result = subprocess.run([binary, 'devices'], env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    found = {}
    for line in result.stdout.decode('utf-8', 'replace').splitlines():
        match = parse_discovery_line(line)
        if match:
            found[match[0]] = match[1]
    return found


class Discovery(threading.Thread):
    """Own a BlueZ discovery client and publish matching advertisements live."""
    def __init__(self, binary, env, scan_duration=8, retry_delay=2):
        super().__init__(name='s3-discovery', daemon=True)
        self.binary, self.env = binary, env
        self.scan_duration, self.retry_delay = scan_duration, retry_delay
        self.found = {}
        self.status = 'Starting discovery'
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()

    def snapshot(self):
        with self.lock:
            return dict(self.found), self.status

    def run(self):
        cached = cached_meters(self.binary, self.env)
        with self.lock:
            self.found.update(cached)
        while not self.stopping.is_set():
            try:
                self.scan()
            except (OSError, subprocess.SubprocessError) as exc:
                with self.lock:
                    self.status = 'Discovery error: ' + clean_text(exc)
            self.stopping.wait(self.retry_delay)

    def scan(self):
        with self.lock:
            self.status = 'Scanning for S3 meters'
        command = [self.binary, '--timeout', str(self.scan_duration), 'scan', 'on']
        stdbuf = shutil.which('stdbuf', path=self.env.get('PATH'))
        if stdbuf:
            command = [stdbuf, '-oL', '-eL', *command]
        process = subprocess.Popen(command, env=self.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, process_group=0)
        buffers = {'out': b'', 'err': b''}
        deadline = time.monotonic() + self.scan_duration + 3
        try:
            with selectors.DefaultSelector() as poller:
                poller.register(process.stdout, selectors.EVENT_READ, 'out')
                poller.register(process.stderr, selectors.EVENT_READ, 'err')
                while not self.stopping.is_set() and time.monotonic() < deadline:
                    for key, _ in poller.select(timeout=.2):
                        data = os.read(key.fileobj.fileno(), 4096)
                        channel = key.data
                        if not data:
                            poller.unregister(key.fileobj)
                            data = b'\n'
                        lines = (buffers[channel] + data).split(b'\n')
                        buffers[channel] = lines.pop()[-4096:]
                        for raw in lines:
                            line = raw.decode('utf-8', 'replace').strip()
                            match = parse_discovery_line(line)
                            with self.lock:
                                if match:
                                    self.found[match[0]] = match[1]
                                elif line and (channel == 'err' or 'Failed' in line or 'No default controller' in line):
                                    self.status = 'Discovery: ' + clean_text(line)
                    if process.poll() is not None and not poller.get_map():
                        break
        finally:
            stop_process(process)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()


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


def draw_line(screen, y, text, attribute=0):
    height, width = screen.getmaxyx()
    if 0 <= y < height and width > 1:
        try:
            screen.addnstr(y, 0, clean_text(text, 2000), width - 1, attribute)
        except curses.error:
            # Wide characters and resize events can reach the terminal edge.
            pass


def dashboard(screen, meters, collectors, discovery=None, make_collector=None):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(200)
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
                        meter = MeterState(f'{name} {address[-5:]}', address)
                        collector = make_collector(meter)
                        collector.start()
                        started.append(collector)
                        meters.append(meter)
                        seen.add(address)
            height, width = screen.getmaxyx()
            page = max(1, (height - 5) // 2)
            offset = min(max(0, offset), max(0, len(meters) - page))
            screen.erase()
            draw_line(screen, 0, f'RS PRO lab | {len(meters)} meters | q quit | ↑/↓ scroll | PgUp/PgDn', curses.A_BOLD)
            draw_line(screen, 1, 'Meter                    Primary              Secondary        Status          Age', curses.A_BOLD)
            now = time.monotonic()
            for row, meter in enumerate(meters[offset:offset + page]):
                snap = meter.snapshot(now)
                age = '—' if snap['age'] is None else f'{snap["age"]:.1f}s'
                primary, secondary = snap['primary'], snap['secondary']
                draw_line(screen, 2 + row * 2,
                          f'{snap["name"][:24]:24} {primary[:20]:20} {secondary[:16]:16} '
                          f'{snap["status"]:13} {age}')
                detail = snap['address']
                if snap['error']:
                    detail += ' | ' + snap['error']
                draw_line(screen, 3 + row * 2, detail, curses.A_DIM)
            if not meters:
                draw_line(screen, 3, 'Looking for S3 meters… Switch on your meters and enable Bluetooth.')
            footer = f'Meters {offset + 1 if meters else 0}–{min(len(meters), offset + page)} of {len(meters)} | {discovery_status}'
            if width < 70:
                footer = 'Widen terminal for all columns | q quit'
            draw_line(screen, height - 1, footer, curses.A_DIM)
            screen.refresh()
            key = screen.getch()
            if key in (ord('q'), ord('Q'), 3):
                break
            if key in (curses.KEY_DOWN, ord('j')):
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


def main(argv=None):
    parser = argparse.ArgumentParser(description='Live terminal dashboard for all configured RS PRO S3 meters.')
    default = os.environ.get('RSPRO_CONFIG', str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'rspro/meters.json'))
    parser.add_argument('--config', default=None, metavar='PATH', help=f'optional meter names/configuration (default: {default})')
    parser.add_argument('--configured-only', action='store_true', help='disable automatic discovery and use only the configuration')
    args = parser.parse_args(argv)
    try:
        config = args.config or default
        meters = load_meters(config) if args.config or Path(config).exists() else []
        if args.configured_only and not meters:
            raise ValueError('--configured-only requires a meter configuration.')
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError('The dashboard requires an interactive terminal.')
        binary, stdbuf, env = capture_environment()
        discovery = None
        if not args.configured_only:
            bluetooth = shutil.which(os.environ.get('RSPRO_BLUETOOTH_BIN', 'bluetoothctl'))
            if not bluetooth:
                raise ValueError('bluetoothctl is required for discovery; install bluez-utils.')
            seen = {meter.address for meter in meters}
            for address, name in cached_meters(bluetooth, env).items():
                if address not in seen:
                    meters.append(MeterState(f'{name} {address[-5:]}', address))
            discovery = Discovery(bluetooth, env)
        privileged = needs_sudo(binary, env, meters[0] if meters else None)
        def make_collector(meter):
            return Collector(meter, capture_command(binary, stdbuf, env, meter, privileged), env)
        collectors = [make_collector(meter) for meter in meters]
        previous = {}
        def interrupt(signum, frame):
            raise KeyboardInterrupt
        try:
            for signum in (signal.SIGTERM, signal.SIGHUP):
                previous[signum] = signal.signal(signum, interrupt)
            curses.wrapper(dashboard, meters, collectors, discovery, make_collector)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, curses.error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
