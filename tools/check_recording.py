#!/usr/bin/env python3
"""Read-only streaming integrity and sample-gap audit for rspro recordings."""
import argparse
import csv
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rspro_features import CSV_FIELDS, parse_reading

MAC = re.compile(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\Z')


def audit_recording(path, max_gap=5.0, allow_gaps=False, expected_meters=None):
    if not math.isfinite(max_gap) or max_gap <= 0:
        raise ValueError('max_gap must be a positive finite number')
    if expected_meters is not None and expected_meters < 1:
        raise ValueError('expected_meters must be positive')
    path = Path(path)
    report = dict(ok=False, rows=0, meters=[], version=None, largest_gap_seconds=0.0,
                  gap_count=0, errors=[])
    errors = report['errors']
    counts, previous = {}, {}
    def fail(message):
        if len(errors) < 20 and message not in errors:
            errors.append(message)
    try:
        with path.open(newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file, strict=True)
            if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                fail('CSV header does not match the recording schema')
            else:
                for number, row in enumerate(reader, 2):
                    report['rows'] += 1
                    if None in row or any(value is None for value in row.values()):
                        fail(f'Row {number}: incomplete or extra CSV fields')
                        continue
                    address = row['meter_address'].upper()
                    if not MAC.fullmatch(address):
                        fail(f'Row {number}: invalid meter address')
                        continue
                    counts.setdefault(address, 0)
                    if row['channel'] not in ('measurement', 'additional'):
                        fail(f'Row {number}: invalid channel')
                        continue
                    parsed = parse_reading(row['reading'])
                    try:
                        value = float(row['value']) if row['value'] else None
                        consistent = value is None if parsed.value is None else (
                            value is not None and math.isfinite(value) and
                            math.isclose(value, parsed.value, rel_tol=1e-12, abs_tol=1e-15))
                        if not consistent or (row['unit'], row['mode']) != (parsed.unit, parsed.mode):
                            fail(f'Row {number}: normalized value/unit/mode does not match reading')
                    except ValueError:
                        fail(f'Row {number}: invalid numeric value')
                    try:
                        timestamp = datetime.fromisoformat(row['timestamp_utc'])
                        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
                            raise ValueError('UTC timestamp required')
                    except ValueError:
                        fail(f'Row {number}: invalid UTC timestamp')
                        continue
                    if row['channel'] == 'measurement':
                        counts[address] += 1
                        if address in previous:
                            gap = (timestamp - previous[address]).total_seconds()
                            if gap < 0:
                                fail(f'Row {number}: measurement time moved backwards')
                            report['largest_gap_seconds'] = max(report['largest_gap_seconds'], gap)
                            if gap > max_gap:
                                report['gap_count'] += 1
                        previous[address] = timestamp
    except (OSError, UnicodeError, csv.Error) as exc:
        fail(f'Cannot read CSV: {exc}')
    report['meters'] = sorted(counts)
    if not counts or any(count == 0 for count in counts.values()):
        fail('Recording contains a meter with no Measurement samples, or no meters')
    if expected_meters is not None and len(counts) != expected_meters:
        fail(f'Expected {expected_meters} meters, found {len(counts)}')
    if report['gap_count'] and not allow_gaps:
        fail(f'{report["gap_count"]} measurement gaps exceed {max_gap:g} seconds')
    try:
        metadata = json.loads(Path(str(path) + '.meta.json').read_text())
        if not isinstance(metadata, dict):
            raise ValueError('metadata must be an object')
        report['version'] = metadata.get('version')
        if metadata.get('schema') != 1 or not report['version']:
            fail('Missing or unsupported integrity metadata/version')
        if metadata.get('completed') is not True or not metadata.get('ended_utc') or metadata.get('error'):
            fail('Recording was not confirmed complete')
        for key in ('accepted', 'written', 'durable_written'):
            value = metadata.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value != report['rows']:
                fail(f'{key} count does not match CSV row count')
        if metadata.get('rejected') != 0:
            fail('Recording rejected samples')
        try:
            start, end = (datetime.fromisoformat(metadata[key]) for key in ('started_utc', 'ended_utc'))
            if any(t.tzinfo is None or t.utcoffset() != timedelta(0) for t in (start, end)) or end < start:
                raise ValueError('invalid UTC session interval')
        except (KeyError, TypeError, ValueError):
            fail('Missing or invalid UTC session start/end times')
        if metadata.get('meters') != report['meters']:
            fail('Meter identities do not match integrity metadata')
    except (OSError, UnicodeError, ValueError) as exc:
        fail(f'Cannot verify integrity metadata: {exc}')
    report['ok'] = not errors
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--max-gap', type=float, default=5.0, metavar='SECONDS')
    parser.add_argument('--allow-gaps', action='store_true', help='allow planned outages while retaining integrity checks')
    parser.add_argument('--expect-meters', type=int, help='required number of meters with measurement data')
    parser.add_argument('--json', action='store_true', help='output a machine-readable report')
    args = parser.parse_args()
    try:
        report = audit_recording(args.csv, args.max_gap, args.allow_gaps, args.expect_meters)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f'{"PASS" if report["ok"] else "FAIL"}: {report["rows"]} channel updates, '
              f'{len(report["meters"])} meters; largest Measurement gap {report["largest_gap_seconds"]:.3f}s')
        for error in report['errors']:
            print('  ' + error)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
