"""Background discovery of APPA/RS PRO S3 Bluetooth advertisements."""
import os
import re
import selectors
import shutil
import subprocess
import threading
import time

from rspro_capture import clean_text, stop_process


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
