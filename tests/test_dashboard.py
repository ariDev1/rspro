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

    def test_startup_has_its_own_budget_before_stream_idle_timeout(self):
        from rspro_dashboard import Collector, MeterState
        state = MeterState('Slow', 'AA:BB:CC:DD:EE:01')
        code = """import time
 time.sleep(.25)
 while True:
     print('Primary: 1 V DC', flush=True)
     time.sleep(.03)
""".replace('\n ', '\n')
        collector = Collector(state, [sys.executable, '-c', code], os.environ.copy(),
                              idle_timeout=.08, connect_timeout=.8, retry_delay=.03)
        collector.start()
        try:
            deadline = time.monotonic() + 2
            while state.snapshot()['primary'] == '—' and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(state.snapshot()['primary'], '1 V DC')
        finally:
            collector.stop()
            collector.join(timeout=3)
        self.assertFalse(collector.is_alive())

    def test_retry_policy_backs_off_and_resets_only_after_sustained_samples(self):
        from rspro_capture import RetryBackoff
        policy = RetryBackoff(base=2, maximum=30, reset_after=10)
        self.assertEqual([policy.failed() for _ in range(6)], [2, 4, 8, 16, 30, 30])
        self.assertEqual(policy.failed(healthy_duration=1), 30)
        self.assertEqual(policy.failed(healthy_duration=10), 2)
        self.assertEqual(policy.failed(), 4)

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
                                  os.environ.copy(), retry_delay=.02, idle_timeout=.15, connect_timeout=.15)
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

class AutomaticDiscovery(unittest.TestCase):
    def test_matches_only_s3_meters_and_normalizes_scan_events(self):
        from rspro_dashboard import parse_discovery_line
        cases = [
            ('Device 18:7A:93:E4:4D:D1 RS PRO S3', ('18:7A:93:E4:4D:D1', 'RS PRO S3')),
            ('[NEW] Device aa:bb:cc:dd:ee:ff APPA S3', ('AA:BB:CC:DD:EE:FF', 'APPA S3')),
            ('[CHG] Device 18:7A:93:E4:4D:D1 Name: RS PRO S3', ('18:7A:93:E4:4D:D1', 'RS PRO S3')),
            ('[\x1b[0;92mNEW\x1b[0m] Device 18:7A:93:E4:4D:D1 RS PRO S3', ('18:7A:93:E4:4D:D1', 'RS PRO S3')),
            ('Device AA:BB:CC:DD:EE:01 headphones', None),
            ('[CHG] Device 18:7A:93:E4:4D:D1 RSSI: -40', None),
            ('Device AA:BB:CC:DD:EE:01 APPA S30', None),
        ]
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_discovery_line(line), expected)

    def test_new_advertisement_is_available_before_scan_ends_and_deduplicated(self):
        from rspro_dashboard import Discovery
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / 'bluetoothctl'
            fixture.write_text("#!/usr/bin/env python3\nimport sys,time\n"
                               "if sys.argv[1:] == ['devices']: sys.exit(0)\n"
                               "print('[NEW] Device 18:7A:93:E4:4D:D1 RS PRO S3', flush=True)\n"
                               "print('[CHG] Device 18:7A:93:E4:4D:D1 Name: RS PRO S3', flush=True)\n"
                               "time.sleep(10)\n")
            fixture.chmod(0o755)
            discovery = Discovery(str(fixture), os.environ.copy(), scan_duration=10, retry_delay=.02)
            discovery.start()
            try:
                deadline = time.monotonic() + 2
                while not discovery.snapshot()[0] and time.monotonic() < deadline:
                    time.sleep(.02)
                found, status = discovery.snapshot()
                self.assertEqual(found, {'18:7A:93:E4:4D:D1': 'RS PRO S3'})
                self.assertIn('Scanning', status)
            finally:
                discovery.stop()
                discovery.join(timeout=3)
            self.assertFalse(discovery.is_alive())

    def test_snapshot_stays_responsive_while_cache_query_is_pending(self):
        from rspro_dashboard import Discovery
        from unittest.mock import patch
        import threading
        entered, release, responsive = threading.Event(), threading.Event(), threading.Event()
        def slow_cache(*args):
            entered.set()
            release.wait(2)
            return {}
        discovery = Discovery('/not-needed', os.environ.copy())
        reader = threading.Thread(target=lambda: (discovery.snapshot(), responsive.set()))
        with patch('rspro_discovery.cached_meters', side_effect=slow_cache):
            discovery.start()
            try:
                self.assertTrue(entered.wait(1))
                reader.start()
                self.assertTrue(responsive.wait(.2), 'cache lookup blocked dashboard snapshots')
            finally:
                discovery.stop()
                release.set()
                reader.join(timeout=2)
                discovery.join(timeout=2)

    def test_auto_mode_can_start_discovery_without_config_cache_or_sudo(self):
        from rspro_dashboard import needs_sudo
        from unittest.mock import patch
        with patch.dict(os.environ, {'RSPRO_USE_SUDO': 'auto'}), \
             patch('rspro_dashboard.subprocess.run', side_effect=AssertionError('authentication before discovery')):
            self.assertFalse(needs_sudo('/not-needed', os.environ.copy(), None))


if __name__ == '__main__':
    unittest.main()
