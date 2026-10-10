import fcntl
import json
import os
from pathlib import Path
import pty
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class Shutdown(unittest.TestCase):
    def test_signals_flush_recording_and_reap_meter_capture(self):
        from tools.check_recording import audit_recording
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'prefix/lib').mkdir(parents=True)
            config = root / 'meters.json'
            config.write_text(json.dumps({'meters': [{'name': 'Meter', 'address': 'AA:BB:CC:DD:EE:01'}]}))
            fixture = root / 'sigrok-cli'
            fixture.write_text("#!/usr/bin/env python3\nimport os,sys,time\nfrom pathlib import Path\n"
                               "with Path(sys.argv[0]).with_name('pids').open('a') as f: f.write(str(os.getpid())+'\\n')\n"
                               "while True:\n    print('Primary: 23 V DC', flush=True)\n    time.sleep(.03)\n")
            fixture.chmod(0o755)
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                with self.subTest(signal=signum):
                    path = root / f'{signum}.csv'
                    master, slave = pty.openpty()
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 120, 0, 0))
                    process = subprocess.Popen([str(REPO / 'rspro'), 'dashboard', '--configured-only',
                                                '--config', str(config), '--record', str(path)],
                                               stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                               env={**os.environ, 'TERM': 'xterm-256color', 'RSPRO_USE_SUDO': 'no',
                                                    'RSPRO_ROOT': tmp, 'RSPRO_SIGROK_BIN': str(fixture)})
                    try:
                        deadline = time.monotonic() + 4
                        while time.monotonic() < deadline:
                            if path.exists() and len(path.read_text().splitlines()) > 1:
                                break
                            time.sleep(.02)
                        self.assertTrue(path.exists())
                        process.send_signal(signum)
                        self.assertEqual(process.wait(timeout=5), 0)
                        report = audit_recording(path, expected_meters=1)
                        self.assertTrue(report['ok'], report)
                        for pid in (root / 'pids').read_text().splitlines():
                            with self.assertRaises(ProcessLookupError):
                                os.kill(int(pid), 0)
                    finally:
                        if process.poll() is None:
                            process.send_signal(signal.SIGTERM)
                            process.wait(timeout=5)
                        os.close(master)
                        os.close(slave)

    def test_abrupt_kill_leaves_readable_data_marked_incomplete(self):
        from tools.check_recording import audit_recording
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'abrupt.csv'
            fixture = root / 'writer.py'
            fixture.write_text(f"import sys,time,json\nfrom pathlib import Path\nsys.path.insert(0,{str(REPO)!r})\n"
                               "from rspro_features import CSVRecorder,make_sample\n"
                               "path=Path(sys.argv[1]); r=CSVRecorder(path)\n"
                               "r.record(make_sample('AA:BB:CC:DD:EE:01','Meter','measurement','23 V DC'))\n"
                               "while True: time.sleep(.1)\n")
            process = subprocess.Popen([sys.executable, str(fixture), str(path)])
            try:
                deadline = time.monotonic() + 4
                metadata = Path(str(path) + '.meta.json')
                while time.monotonic() < deadline:
                    if metadata.exists() and json.loads(metadata.read_text())['durable_written']:
                        break
                    time.sleep(.02)
                process.kill()
                process.wait(timeout=2)
                self.assertGreater(len(path.read_text().splitlines()), 1)
                report = audit_recording(path)
                self.assertFalse(report['ok'])
                self.assertTrue(any('complete' in error for error in report['errors']))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()
