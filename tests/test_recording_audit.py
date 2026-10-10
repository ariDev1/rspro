import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RecordingAudit(unittest.TestCase):
    def make_recording(self, root, times=('2026-10-10T10:00:00+00:00', '2026-10-10T10:00:01+00:00')):
        from rspro_features import CSVRecorder, make_sample
        path = root / 'audit.csv'
        recorder = CSVRecorder(path)
        for timestamp in times:
            sample = make_sample('AA:BB:CC:DD:EE:01', 'Meter', 'measurement', '23 V DC')
            sample['timestamp_utc'] = timestamp
            recorder.record(sample)
        recorder.close()
        return path

    def test_checker_accepts_clean_recording_and_reports_meter_count(self):
        from tools.check_recording import audit_recording
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_recording(Path(tmp))
            report = audit_recording(path, expected_meters=1)
            self.assertTrue(report['ok'], report)
            self.assertEqual(report['rows'], 2)
            self.assertEqual(report['meters'], ['AA:BB:CC:DD:EE:01'])

    def test_checker_rejects_incomplete_metadata_and_removed_rows(self):
        from tools.check_recording import audit_recording
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_recording(Path(tmp))
            metadata = Path(str(path) + '.meta.json')
            original = json.loads(metadata.read_text())
            incomplete = {**original, 'completed': False}
            metadata.write_text(json.dumps(incomplete))
            self.assertFalse(audit_recording(path)['ok'])
            metadata.write_text(json.dumps(original))
            path.write_text('\n'.join(path.read_text().splitlines()[:-1]) + '\n')
            report = audit_recording(path)
            self.assertFalse(report['ok'])
            self.assertTrue(any('count' in e for e in report['errors']))

    def test_checker_reports_large_gaps_and_expected_meter_mismatch(self):
        from tools.check_recording import audit_recording
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_recording(Path(tmp), ('2026-10-10T10:00:00+00:00', '2026-10-10T10:00:10+00:00'))
            report = audit_recording(path)
            self.assertFalse(report['ok'])
            self.assertEqual(report['largest_gap_seconds'], 10)
            self.assertTrue(audit_recording(path, allow_gaps=True)['ok'])
            self.assertFalse(audit_recording(path, allow_gaps=True, expected_meters=2)['ok'])

    def test_checker_requires_session_times_and_meter_identities(self):
        from tools.check_recording import audit_recording
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_recording(Path(tmp))
            sidecar = Path(str(path) + '.meta.json')
            original = json.loads(sidecar.read_text())
            for changes in ({'started_utc': None}, {'ended_utc': 'corrupt'},
                            {'ended_utc': '2000-01-01T00:00:00+00:00'}, {'meters': None}):
                with self.subTest(changes=changes):
                    sidecar.write_text(json.dumps({**original, **changes}))
                    self.assertFalse(audit_recording(path)['ok'])

    def test_cli_returns_nonzero_for_bad_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_recording(Path(tmp))
            Path(str(path) + '.meta.json').unlink()
            result = subprocess.run([sys.executable, 'tools/check_recording.py', str(path), '--json'],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
