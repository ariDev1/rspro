import csv
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class NumericFeatures(unittest.TestCase):
    def test_normalizes_prefixes_and_retains_measurement_modes(self):
        from rspro_features import parse_reading
        for raw, value, unit, mode in [
            ('27.57 V DC', 27.57, 'V', 'DC'), ('250 mV DC', .25, 'V', 'DC'),
            ('-2.5 µA DC', -2.5e-6, 'A', 'DC'), ('1.2 kOhm', 1200, 'Ω', ''),
            ('5.0e3 Hz', 5000, 'Hz', ''), ('22.3 °C', 22.3, '°C', '')]:
            with self.subTest(raw=raw):
                result = parse_reading(raw)
                self.assertAlmostEqual(result.value, value)
                self.assertEqual((result.unit, result.mode), (unit, mode))
        self.assertIsNone(parse_reading('inf').value)
        self.assertIsNone(parse_reading('OL').value)
        self.assertIsNone(parse_reading('1e999 V DC').value)

    def test_limits_compare_normalized_values_without_crossing_unit_or_mode(self):
        from rspro_features import Limits, parse_reading
        limits = Limits(below=24, above=30, unit='V', mode='DC')
        self.assertTrue(limits.evaluate(parse_reading('23 V DC'))[0])
        self.assertIn('LOW', limits.evaluate(parse_reading('23000 mV DC'))[1])
        self.assertIn('HIGH', limits.evaluate(parse_reading('31 V DC'))[1])
        for reading in ['24 V DC', '30 V DC', '27 V DC', '3 A DC', '23 V AC', 'inf']:
            with self.subTest(reading=reading):
                self.assertFalse(limits.evaluate(parse_reading(reading))[0])
        scaled = Limits(below=250, unit='mV')
        self.assertTrue(scaled.evaluate(parse_reading('.2 V'))[0])
        for kwargs in [dict(below=30, above=24, unit='V'), dict(below=math.nan, unit='V'),
                       dict(below=24), dict(unit='V'), dict(below=True, unit='V')]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Limits(**kwargs)

    def test_sparklines_show_direction_flatness_and_recent_samples(self):
        from rspro_features import sparkline
        self.assertEqual(sparkline([1, 2, 3], 3), '▁▄█')
        self.assertEqual(sparkline([3, 2, 1], 3), '█▄▁')
        self.assertEqual(sparkline([7, 7, 7], 3), '▄▄▄')
        self.assertEqual(sparkline([100, 1, 2, 3], 3), '▁▄█')
        self.assertEqual(sparkline([], 10), '')
        self.assertEqual(sparkline([1], 0), '')

    def test_chart_handles_extreme_finite_values_without_overflow(self):
        from rspro_features import sparkline
        self.assertEqual(sparkline([-1e308, 0, 1e308], 3), '▁▄█')


class Recording(unittest.TestCase):
    def sample(self, i=0, channel='measurement', reading='27.57 V DC'):
        from rspro_features import make_sample
        return make_sample('AA:BB:CC:DD:EE:01', 'Meter, one', channel, reading)

    def test_records_real_channel_updates_and_never_overwrites_existing_files(self):
        from rspro_features import CSVRecorder
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.csv'
            recorder = CSVRecorder(path)
            recorder.record(self.sample())
            recorder.record(self.sample(channel='additional', reading='inf'))
            recorder.close()
            self.assertFalse(recorder.is_alive())
            with path.open(newline='') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['meter_name'], 'Meter, one')
            self.assertEqual(rows[0]['channel'], 'measurement')
            self.assertEqual(rows[0]['value'], '27.57')
            self.assertEqual((rows[0]['unit'], rows[0]['mode']), ('V', 'DC'))
            self.assertTrue(rows[0]['timestamp_utc'].endswith('+00:00'))
            self.assertEqual(rows[1]['reading'], 'inf')
            self.assertEqual(rows[1]['value'], '')
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                CSVRecorder(path)
            self.assertEqual(path.read_bytes(), before)

    def test_concurrent_updates_are_drained_on_close(self):
        from rspro_features import CSVRecorder
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.csv'
            recorder = CSVRecorder(path)
            accepted = []
            def write():
                for i in range(30):
                    accepted.append(recorder.record(self.sample(i)))
            workers = [threading.Thread(target=write) for _ in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            recorder.close()
            self.assertTrue(all(accepted))
            with path.open(newline='') as file:
                self.assertEqual(len(list(csv.DictReader(file))), 120)

    def test_stop_drains_sample_accepted_after_an_empty_queue_observation(self):
        from rspro_features import CSVRecorder
        from unittest.mock import patch
        import queue
        import time
        observed_empty, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'race.csv'
            recorder = CSVRecorder(path)
            original = recorder.pending.get
            def stale_empty(*args, **kwargs):
                try:
                    return original(*args, **kwargs)
                except queue.Empty:
                    if not observed_empty.is_set():
                        observed_empty.set()
                        release.wait(2)
                    raise
            closer = threading.Thread(target=recorder.close)
            try:
                with patch.object(recorder.pending, 'get', side_effect=stale_empty):
                    self.assertTrue(observed_empty.wait(1))
                    self.assertTrue(recorder.record(self.sample()))
                    closer.start()
                    deadline = time.monotonic() + 1
                    while recorder.snapshot()['active'] and time.monotonic() < deadline:
                        time.sleep(.01)
                    release.set()
                    closer.join(timeout=2)
                with path.open(newline='') as file:
                    self.assertEqual(len(list(csv.DictReader(file))), 1)
            finally:
                release.set()
                if closer.ident:
                    closer.join(timeout=2)
                recorder.close()

    def test_clean_recording_has_durable_matching_integrity_metadata(self):
        from rspro_features import CSVRecorder
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.csv'
            recorder = CSVRecorder(path)
            initial = json.loads(Path(str(path) + '.meta.json').read_text())
            self.assertFalse(initial['completed'])
            self.assertTrue(recorder.record(self.sample()))
            recorder.close()
            meta = json.loads(Path(str(path) + '.meta.json').read_text())
            self.assertTrue(meta['completed'])
            self.assertEqual((meta['accepted'], meta['written'], meta['durable_written'], meta['rejected']), (1, 1, 1, 0))
            self.assertTrue(meta['version'])
            self.assertTrue(meta['ended_utc'])

    def test_sync_failure_never_marks_recording_complete(self):
        from rspro_features import CSVRecorder
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.csv'
            recorder = CSVRecorder(path)
            recorder.record(self.sample())
            with patch('rspro_features.os.fsync', side_effect=OSError('sync failed')):
                recorder.close()
            self.assertIn('sync failed', recorder.error)
            meta = json.loads(Path(str(path) + '.meta.json').read_text())
            self.assertFalse(meta['completed'])

    def test_final_directory_sync_failure_cannot_leave_success_marker(self):
        from rspro_features import CSVRecorder
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.csv'
            recorder = CSVRecorder(path)
            recorder.record(self.sample())
            original = recorder.write_metadata
            def fail_completion(completed=False, ended=False):
                if not completed:
                    raise OSError('no space for recovery temporary file')
                return original(completed, ended)
            with patch.object(recorder, 'sync_directory', side_effect=OSError('directory sync failed')), \
                 patch.object(recorder, 'write_metadata', side_effect=fail_completion):
                recorder.close()
            self.assertIn('directory sync failed', recorder.error)
            meta = json.loads(Path(str(path) + '.meta.json').read_text())
            self.assertFalse(meta['completed'])

    def test_full_queue_stops_recording_visibly_and_drains_accepted_samples(self):
        from rspro_features import CSVRecorder
        from unittest.mock import patch
        entered, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.csv'
            recorder = CSVRecorder(path, capacity=1)
            original = recorder.write_sample
            def slow_write(sample):
                entered.set()
                release.wait(2)
                original(sample)
            try:
                with patch.object(recorder, 'write_sample', side_effect=slow_write):
                    self.assertTrue(recorder.record(self.sample()))
                    self.assertTrue(entered.wait(1))
                    self.assertTrue(recorder.record(self.sample()))
                    self.assertFalse(recorder.record(self.sample()))
                    self.assertIn('incomplete', recorder.snapshot()['error'])
                    self.assertFalse(recorder.snapshot()['active'])
                    release.set()
                    recorder.close()
                with path.open(newline='') as file:
                    self.assertEqual(len(list(csv.DictReader(file))), 2)
            finally:
                release.set()
                recorder.close()

    def test_disk_failure_is_visible_and_stops_accepting_samples(self):
        from rspro_features import CSVRecorder
        from unittest.mock import patch
        import time
        with tempfile.TemporaryDirectory() as tmp:
            recorder = CSVRecorder(Path(tmp) / 'session.csv')
            try:
                with patch.object(recorder, 'write_sample', side_effect=OSError('disk full')):
                    recorder.record(self.sample())
                    deadline = time.monotonic() + 1
                    while not recorder.error and time.monotonic() < deadline:
                        time.sleep(.01)
                self.assertIn('disk full', recorder.error)
                self.assertFalse(recorder.record(self.sample()))
            finally:
                recorder.close()

class MeterIntegration(unittest.TestCase):
    def test_history_is_bounded_and_resets_for_units_modes_and_reconnect(self):
        from rspro_dashboard import MeterState
        state = MeterState('A', 'AA:BB:CC:DD:EE:01')
        for i in range(130):
            state.update_line(f'Primary: {i} V DC', 100 + i)
        self.assertEqual(len(state.snapshot(230)['history']), 120)
        self.assertEqual(state.snapshot(230)['history'][0], 10)
        state.update_line('Primary: 130000 mV DC', 231)
        self.assertEqual(state.snapshot(231)['history'][-1], 130)
        state.update_line('Primary: 2 A DC', 232)
        self.assertEqual(state.snapshot(232)['history'], [2])
        state.update_line('Primary: 3 A AC', 233)
        self.assertEqual(state.snapshot(233)['history'], [3])
        state.update_line('Primary: inf', 234)
        self.assertEqual(state.snapshot(234)['history'], [])
        state.update_line('Primary: 4 A AC', 235)
        state.set_status('Reconnecting')
        self.assertEqual(state.snapshot(236)['history'], [])

    def test_alert_is_not_fresh_when_readings_are_stale_or_disconnected(self):
        from rspro_dashboard import MeterState
        from rspro_features import Limits
        state = MeterState('A', 'AA:BB:CC:DD:EE:01', limits=Limits(below=24, unit='V', mode='DC'))
        state.update_line('Primary: 23 V DC', 100)
        self.assertTrue(state.snapshot(101)['alert'])
        self.assertIn('LOW', state.snapshot(101)['alert_text'])
        self.assertFalse(state.snapshot(106)['alert'])
        state.set_status('Reconnecting')
        self.assertFalse(state.snapshot(101)['alert'])

    def test_auxiliary_update_cannot_reactivate_an_alert_from_previous_connection(self):
        from rspro_dashboard import MeterState
        from rspro_features import Limits
        state = MeterState('A', 'AA:BB:CC:DD:EE:01', limits=Limits(below=24, unit='V'))
        state.update_line('Primary: 23 V', 100)
        self.assertTrue(state.snapshot(100)['alert'])
        state.set_status('Reconnecting')
        state.update_line('Secondary: 50 Hz', 101)
        self.assertFalse(state.snapshot(101)['alert'])
        self.assertEqual(state.snapshot(101)['primary'], '23 V')
        state.update_line('Primary: 23 V', 102)
        self.assertTrue(state.snapshot(102)['alert'])

    def test_toggle_records_only_actual_updates_while_enabled(self):
        from rspro_dashboard import MeterState
        from rspro_features import RecordingSession
        with tempfile.TemporaryDirectory() as tmp:
            session = RecordingSession(tmp)
            state = MeterState('A', 'AA:BB:CC:DD:EE:01', recording=session)
            state.update_line('Primary: 1 V DC')
            path = Path(tmp) / 'session.csv'
            self.assertTrue(session.start(path))
            state.update_line('Secondary: inf')
            state.update_line('Primary: 2 V DC')
            session.stop()
            state.update_line('Primary: 3 V DC')
            with path.open(newline='') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual([r['channel'] for r in rows], ['additional', 'measurement'])
            self.assertEqual([r['reading'] for r in rows], ['inf', '2 V DC'])

    def test_configured_meter_limits_override_default_limits(self):
        from rspro_dashboard import load_meters
        from rspro_features import Limits
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'meters.json'
            path.write_text(json.dumps({'meters': [{'name': 'A', 'address': 'AA:BB:CC:DD:EE:01',
                                                  'limits': {'above': 10, 'unit': 'V'}}]}))
            state = load_meters(path, default_limits=Limits(below=24, unit='V'))[0]
            state.update_line('Primary: 12 V', 100)
            self.assertIn('HIGH', state.snapshot(101)['alert_text'])

class MeterSelection(unittest.TestCase):
    def test_recording_filters_selected_meter_but_all_mode_includes_new_meters(self):
        from rspro_features import RecordingSession, make_sample
        with tempfile.TemporaryDirectory() as tmp:
            session = RecordingSession(tmp)
            selected = Path(tmp) / 'selected.csv'
            self.assertTrue(session.start(selected, addresses={'AA:BB:CC:DD:EE:02'}))
            for address in ['AA:BB:CC:DD:EE:01', 'AA:BB:CC:DD:EE:02', 'AA:BB:CC:DD:EE:03']:
                session.record(make_sample(address, 'Meter', 'measurement', '23 V DC'))
            session.stop()
            with selected.open(newline='') as file:
                self.assertEqual([r['meter_address'] for r in csv.DictReader(file)], ['AA:BB:CC:DD:EE:02'])
            all_path = Path(tmp) / 'all.csv'
            self.assertTrue(session.start(all_path))
            session.record(make_sample('AA:BB:CC:DD:EE:03', 'New meter', 'measurement', '24 V DC'))
            session.stop()
            with all_path.open(newline='') as file:
                self.assertEqual([r['meter_address'] for r in csv.DictReader(file)], ['AA:BB:CC:DD:EE:03'])

    def test_picker_selects_one_multiple_all_and_can_cancel(self):
        from rspro_dashboard import RecordingPicker, MeterState
        meters = [MeterState('First', 'AA:BB:CC:DD:EE:01'), MeterState('Second', 'AA:BB:CC:DD:EE:02')]
        picker = RecordingPicker(meters)
        picker.handle(ord('j'), meters)
        self.assertEqual(picker.handle(10, meters), ('start', {'AA:BB:CC:DD:EE:02'}))
        picker = RecordingPicker(meters)
        picker.handle(ord(' '), meters)
        picker.handle(ord('j'), meters)
        picker.handle(ord(' '), meters)
        self.assertEqual(picker.handle(10, meters), ('start', {m.address for m in meters}))
        picker = RecordingPicker(meters)
        picker.handle(ord('a'), meters)
        self.assertEqual(picker.handle(10, meters), ('start', None))
        self.assertEqual(picker.handle(27, meters), ('cancel', None))
        picker = RecordingPicker([])
        self.assertEqual(picker.handle(10, []), ('start', None))


if __name__ == '__main__':
    unittest.main()
