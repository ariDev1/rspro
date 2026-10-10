"""Numeric trends, measurement limits and buffered CSV recording."""
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import queue
import re
import threading
import time

BASE_UNITS = {'V', 'A', 'F', 'Hz', 'Ω', 'W', 'S', 'H', 's', '°C', '°F', '%', 'dB'}
PREFIXES = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6,
            'm': 1e-3, 'k': 1e3, 'M': 1e6, 'G': 1e9}
NUMBER = re.compile(r'^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+(\S+)(?:\s+(.*))?$')
CSV_FIELDS = ('timestamp_utc', 'meter_address', 'meter_name', 'channel', 'reading', 'value', 'unit', 'mode')


def normalize_unit(unit):
    unit = unit.replace('Ohm', 'Ω').replace('ohm', 'Ω').replace('Ω', 'Ω')
    if unit in BASE_UNITS:
        return unit, 1.0
    if len(unit) > 1 and unit[0] in PREFIXES and unit[1:] in BASE_UNITS:
        return unit[1:], PREFIXES[unit[0]]
    return unit, 1.0


@dataclass(frozen=True)
class NumericReading:
    value: float | None = None
    unit: str = ''
    mode: str = ''


def parse_reading(text):
    match = NUMBER.fullmatch(text.strip())
    if not match:
        return NumericReading()
    number, token, mode = match.groups()
    unit, factor = normalize_unit(token)
    value = float(number) * factor
    return NumericReading(value if math.isfinite(value) else None, unit, (mode or '').upper())


@dataclass(frozen=True)
class Limits:
    below: float | None = None
    above: float | None = None
    unit: str = ''
    mode: str | None = None

    def __post_init__(self):
        if self.below is None and self.above is None:
            raise ValueError('Limits need at least one below or above bound.')
        if not isinstance(self.unit, str) or not self.unit or any(c.isspace() for c in self.unit):
            raise ValueError('Limits require a unit, such as V or mV.')
        unit, factor = normalize_unit(self.unit)
        for name in ('below', 'above'):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('Limit values must be finite numbers.')
                value = float(value) * factor
                if not math.isfinite(value):
                    raise ValueError('Normalized limit values must be finite.')
                object.__setattr__(self, name, value)
        if self.below is not None and self.above is not None and self.below > self.above:
            raise ValueError('The below limit must not exceed the above limit.')
        if self.mode is not None and (not isinstance(self.mode, str) or not self.mode.strip()):
            raise ValueError('Limit mode must be nonempty, for example DC or AC.')
        object.__setattr__(self, 'unit', unit)
        object.__setattr__(self, 'mode', self.mode.upper().strip() if self.mode else None)

    def evaluate(self, reading):
        label = self.unit + (' ' + self.mode if self.mode else '')
        if reading.value is None:
            return False, 'Limits: no numeric measurement'
        if reading.unit != self.unit or (self.mode and reading.mode != self.mode):
            return False, f'Limits apply to {label}; different unit/mode'
        if self.below is not None and reading.value < self.below:
            return True, f'LOW < {self.below:g} {label}'
        if self.above is not None and reading.value > self.above:
            return True, f'HIGH > {self.above:g} {label}'
        return False, 'Within limits'


def sparkline(values, width=40):
    if width <= 0:
        return ''
    values = [v for v in values if math.isfinite(v)][-width:]
    if not values:
        return ''
    low, high = min(values), max(values)
    if low == high:
        return '▄' * len(values)
    glyphs = '▁▂▃▄▅▆▇█'
    return ''.join(glyphs[min(7, int((value - low) / (high - low) * 7))] for value in values)


def make_sample(address, name, channel, text):
    reading = parse_reading(text)
    return dict(timestamp_utc=datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                meter_address=address, meter_name=name, channel=channel, reading=text,
                value='' if reading.value is None else reading.value,
                unit=reading.unit, mode=reading.mode)


class CSVRecorder(threading.Thread):
    """One writer per session. Collectors enqueue without waiting for disk I/O."""
    def __init__(self, path, capacity=4096):
        super().__init__(name='csv-recorder', daemon=True)
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('x', newline='', encoding='utf-8')
        self.writer = csv.DictWriter(self.file, fieldnames=CSV_FIELDS)
        try:
            self.writer.writeheader()
            self.file.flush()
        except BaseException:
            self.file.close()
            raise
        self.pending = queue.Queue(maxsize=capacity)
        self.lock = threading.Lock()
        self.closing = False
        self.error = ''
        self.written = 0
        self.start()

    def record(self, sample):
        with self.lock:
            if self.closing or self.error:
                return False
            try:
                self.pending.put_nowait(sample)
            except queue.Full:
                self.error = 'Recording stopped: write queue is full; recording is incomplete'
                self.closing = True
                return False
        return True

    def write_sample(self, sample):
        self.writer.writerow(sample)

    def run(self):
        last_flush = time.monotonic()
        try:
            while True:
                try:
                    sample = self.pending.get(timeout=.1)
                except queue.Empty:
                    with self.lock:
                        if self.closing:
                            break
                else:
                    self.write_sample(sample)
                    with self.lock:
                        self.written += 1
                if time.monotonic() - last_flush >= .5:
                    self.file.flush()
                    last_flush = time.monotonic()
            self.file.flush()
        except (OSError, csv.Error, ValueError) as exc:
            with self.lock:
                self.error = f'Recording stopped: {exc}; recording is incomplete'
                self.closing = True
        finally:
            try:
                self.file.close()
            except OSError as exc:
                with self.lock:
                    self.error = self.error or f'Recording stopped: {exc}'

    def close(self):
        with self.lock:
            self.closing = True
        self.join(timeout=4)
        if self.is_alive():
            with self.lock:
                self.error = 'Recording writer did not finish; recording may be incomplete'

    def snapshot(self):
        with self.lock:
            return dict(active=not self.closing and not self.error,
                        error=self.error, path=str(self.path), written=self.written)


class RecordingSession:
    """Recording session with optional per-meter filtering."""
    def __init__(self, directory=None):
        data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
        self.directory = Path(directory) if directory else data / 'rspro/recordings'
        self.recorder = None
        self.addresses = None
        self.lock = threading.Lock()
        self.last = dict(active=False, error='', path='', written=0)

    def record(self, sample):
        with self.lock:
            if self.recorder and (self.addresses is None or sample['meter_address'].upper() in self.addresses):
                return self.recorder.record(sample)
        return False

    def start(self, path=None, addresses=None):
        self.stop()
        if path is None:
            path = self.directory / datetime.now(timezone.utc).strftime('rspro-%Y%m%dT%H%M%S.%fZ.csv')
        try:
            recorder = CSVRecorder(path)
        except (OSError, ValueError) as exc:
            with self.lock:
                self.last = dict(active=False, error=f'Recording error: {exc}', path=str(path), written=0)
            return False
        with self.lock:
            self.addresses = None if addresses is None else frozenset(address.upper() for address in addresses)
            self.recorder = recorder
        return True

    def stop(self):
        with self.lock:
            recorder, self.recorder = self.recorder, None
        if recorder:
            recorder.close()
            with self.lock:
                self.last = recorder.snapshot()

    def snapshot(self):
        with self.lock:
            info = self.recorder.snapshot() if self.recorder else dict(self.last)
            info['addresses'] = None if self.addresses is None else sorted(self.addresses)
            return info

    def toggle(self):
        if self.snapshot()['active']:
            self.stop()
        else:
            self.start()
