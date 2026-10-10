import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ConfigAndState(unittest.TestCase):
    def test_configuration_normalizes_addresses_and_rejects_duplicates(self):
        from rspro_dashboard import load_meters
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'meters.json'
            path.write_text(json.dumps({'meters': [
                {'name': 'Supply', 'address': 'aa:bb:cc:dd:ee:ff'}]}))
            meters = load_meters(path)
            self.assertEqual(meters[0].address, 'AA:BB:CC:DD:EE:FF')
            self.assertEqual(meters[0].name, 'Supply')
            path.write_text(json.dumps({'meters': [
                {'name': 'One', 'address': 'aa:bb:cc:dd:ee:ff'},
                {'name': 'Two', 'address': 'AA:BB:CC:DD:EE:FF'}]}))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                load_meters(path)

    def test_bad_configs_fail_before_capture(self):
        from rspro_dashboard import load_meters
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'meters.json'
            for data in [[], {}, {'meters': []}, {'meters': [None]},
                         {'meters': [{'name': '', 'address': 'AA:BB:CC:DD:EE:FF'}]},
                         {'meters': [{'name': 'A', 'address': 'bogus'}]}]:
                path.write_text(json.dumps(data))
                with self.subTest(data=data), self.assertRaises(ValueError):
                    load_meters(path)

    def test_readings_are_separate_and_staleness_does_not_hide_disconnect(self):
        from rspro_dashboard import MeterState
        meter = MeterState('Supply', 'AA:BB:CC:DD:EE:FF')
        meter.update_line('Primary: 27.57 V DC', 100)
        meter.update_line('Secondary: inf', 101)
        snap = meter.snapshot(102)
        self.assertEqual((snap['primary'], snap['secondary']), ('27.57 V DC', 'inf'))
        self.assertEqual(snap['status'], 'Connected')
        self.assertEqual(meter.snapshot(106)['status'], 'Stale')
        meter.set_status('Reconnecting', 'adapter\x1b[31m failed\n')
        snap = meter.snapshot(107)
        self.assertEqual(snap['status'], 'Reconnecting')
        self.assertEqual(snap['primary'], '27.57 V DC')
        self.assertNotIn('\x1b', snap['error'])


class Collection(unittest.TestCase):
    def test_parallel_streams_retry_and_cleanup(self):
        from rspro_dashboard import Collector, MeterState
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / 'meter.py'
            fixture.write_text('''import os, sys, time
from pathlib import Path
marker = Path(sys.argv[1])
with marker.open('a') as f: f.write(str(os.getpid()) + '\\n')
if sys.argv[2] == 'retry' and len(marker.read_text().splitlines()) == 1:
    print('connection failed', file=sys.stderr, flush=True)
    sys.exit(3)
while True:
    print('Primary: 27.57 V DC', flush=True)
    print('Secondary: 50 Hz', flush=True)
    print('diagnostic', file=sys.stderr, flush=True)
    time.sleep(.03)
''')
            states = [MeterState('One', 'AA:BB:CC:DD:EE:01'),
                      MeterState('Two', 'AA:BB:CC:DD:EE:02')]
            collectors = [Collector(state, [sys.executable, str(fixture),
                          str(root / str(i)), mode], os.environ.copy(),
                          retry_delay=.03, idle_timeout=1)
                          for i, (state, mode) in enumerate(zip(states, ['retry', 'normal']))]
            try:
                for collector in collectors:
                    collector.start()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if all(s.snapshot()['secondary'] == '50 Hz' for s in states):
                        break
                    time.sleep(.02)
                self.assertTrue(all(s.snapshot()['primary'] == '27.57 V DC' for s in states))
                self.assertGreaterEqual(len((root / '0').read_text().splitlines()), 2)
            finally:
                for collector in collectors:
                    collector.stop()
                for collector in collectors:
                    collector.join(timeout=3)
            self.assertTrue(all(not c.is_alive() for c in collectors))
            for i in range(2):
                for pid in (root / str(i)).read_text().splitlines():
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(pid), 0)

    def test_capture_keeps_stdin_open_so_sigrok_does_not_stop_on_eof(self):
        from rspro_dashboard import Collector, MeterState
        # sigrok-cli watches stdin and stops acquisition when it reaches EOF.
        code = """import select, sys, time
if select.select([sys.stdin], [], [], .1)[0] and not sys.stdin.readline():
    sys.exit(0)
print('Primary: 27.57 V DC', flush=True)
while True: time.sleep(1)
"""
        state = MeterState('Supply', 'AA:BB:CC:DD:EE:01')
        collector = Collector(state, [sys.executable, '-c', code], os.environ.copy(),
                              retry_delay=.02)
        collector.start()
        try:
            deadline = time.monotonic() + 1
            while state.snapshot()['primary'] == '—' and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(state.snapshot()['primary'], '27.57 V DC')
        finally:
            collector.stop()
            collector.join(timeout=3)
        self.assertFalse(collector.is_alive())

    def test_capture_retains_session_for_sudo_timestamp(self):
        from rspro_dashboard import Collector, MeterState
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'session'
            code = ("import os,time,sys; from pathlib import Path; "
                    "Path(sys.argv[1]).write_text(str(os.getsid(0))); time.sleep(10)")
            collector = Collector(MeterState('A', 'AA:BB:CC:DD:EE:01'),
                                  [sys.executable, '-c', code, str(marker)], os.environ.copy())
            collector.start()
            try:
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(int(marker.read_text()), os.getsid(0))
            finally:
                collector.stop()
                collector.join(timeout=3)

    def test_cleanup_kills_helper_even_after_leader_has_exited(self):
        from rspro_dashboard import stop_process
        code = """import os, signal, time
reader, writer = os.pipe()
child = os.fork()
if child == 0:
    os.close(reader)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(writer, b'ready')
    while True: time.sleep(1)
os.close(writer)
os.read(reader, 5)
print(child, flush=True)
"""
        process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE,
                                   text=True, start_new_session=True)
        child = int(process.stdout.readline())
        process.wait(timeout=2)
        try:
            stop_process(process)
            deadline = time.monotonic() + 2
            running = True
            while time.monotonic() < deadline:
                try:
                    running = Path(f'/proc/{child}/stat').read_text().split()[2] != 'Z'
                except FileNotFoundError:
                    running = False
                if not running:
                    break
                time.sleep(.02)
            self.assertFalse(running, 'helper survived capture cleanup')
        finally:
            try:
                os.kill(child, 9)
            except ProcessLookupError:
                pass
            process.stdout.close()

    def test_silent_stream_restarts_without_growing_error_memory(self):
        from rspro_dashboard import Collector, MeterState
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'starts'
            code = ("from pathlib import Path; import time,sys; "
                    "p=Path(sys.argv[1]); p.open('a').write('start\\n'); "
                    "sys.stderr.write('x'*20000); sys.stderr.flush(); time.sleep(10)")
            state = MeterState('Silent', 'AA:BB:CC:DD:EE:03')
            collector = Collector(state, [sys.executable, '-c', code, str(marker)],
                                  os.environ.copy(), retry_delay=.02, idle_timeout=.15)
            collector.start()
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if marker.exists() and len(marker.read_text().splitlines()) >= 2:
                        break
                    time.sleep(.02)
                self.assertGreaterEqual(len(marker.read_text().splitlines()), 2)
                self.assertLessEqual(len(state.snapshot()['error']), 160)
            finally:
                collector.stop()
                collector.join(timeout=3)
            self.assertFalse(collector.is_alive())


if __name__ == '__main__':
    unittest.main()
