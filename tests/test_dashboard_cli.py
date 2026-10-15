import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


def fake_btmgmt(root, settings='powered connectable le br/edr ssp'):
    """Stand-in for btmgmt whose 'info' reports the given adapter settings."""
    path = root / 'btmgmt'
    path.write_text('#!/usr/bin/env python3\n'
                    'print("\\tsupported settings: ' + settings + '")\n')
    path.chmod(0o755)
    return path


class DashboardCLI(unittest.TestCase):
    def test_dashboard_help_dispatches_without_capture(self):
        result = subprocess.run([str(REPO / 'rspro'), 'dashboard', '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--config', result.stdout)

    def test_invalid_configuration_is_actionable(self):
        result = subprocess.run([str(REPO / 'rspro'), 'dashboard', '--config',
                                 '/tmp/rspro-no-such-config/meters.json'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('meter configuration', result.stderr)

    def test_adapter_without_ble_stops_with_clear_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'prefix/lib').mkdir(parents=True)
            config = root / 'meters.json'
            config.write_text(json.dumps({'meters': [
                {'name': 'Bench meter', 'address': 'AA:BB:CC:DD:EE:01'}]}))
            stub = root / 'sigrok-cli'
            stub.write_text('#!/bin/bash\nwhile true; do echo "Primary: 1 V DC"; sleep .1; done\n')
            stub.chmod(0o755)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 110, 0, 0))
            process = subprocess.Popen([str(REPO / 'rspro'), 'dashboard', '--configured-only',
                '--config', str(config)], stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True,
                env={**os.environ, 'TERM': 'xterm-256color', 'RSPRO_ROOT': tmp,
                     'RSPRO_USE_SUDO': 'no', 'RSPRO_SIGROK_BIN': str(stub),
                     'RSPRO_BTMGMT_BIN': str(fake_btmgmt(root, settings='powered connectable br/edr ssp'))})
            output = bytearray()
            try:
                deadline = time.monotonic() + 4
                while process.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            output.extend(os.read(master, 65536))
                        except OSError:
                            break
                self.assertEqual(process.wait(timeout=5), 1)
                self.assertIn(b'Bluetooth Low Energy', output)
                self.assertIn(b'Bluetooth 4.0', output)
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=5)
                os.close(master)
                os.close(slave)

    def test_terminal_render_scroll_resize_and_quit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'prefix/lib').mkdir(parents=True)
            config = root / 'meters.json'
            config.write_text(json.dumps({'meters': [
                {'name': f'Bench {i}', 'address': f'AA:BB:CC:DD:EE:{i:02X}'}
                for i in range(1, 9)]}))
            stub = root / 'sigrok-cli'
            stub.write_text('#!/bin/bash\nwhile true; do\n'
                            'echo "Primary: 27.57 V DC"\n'
                            'echo "Secondary: 50 Hz"\nsleep .1\ndone\n')
            stub.chmod(0o755)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 14, 110, 0, 0))
            env = {**os.environ, 'TERM': 'xterm-256color', 'RSPRO_ROOT': tmp,
                   'RSPRO_USE_SUDO': 'no', 'RSPRO_SIGROK_BIN': str(stub),
                   'RSPRO_BTMGMT_BIN': str(fake_btmgmt(root))}
            process = subprocess.Popen([str(REPO / 'rspro'), 'dashboard', '--configured-only', '--config', str(config)],
                                       stdin=slave, stdout=slave, stderr=slave, env=env,
                                       start_new_session=True)
            output = bytearray()
            def drain_until(token, timeout=4):
                deadline = time.monotonic() + timeout
                while token not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            output.extend(os.read(master, 65536))
                        except OSError:
                            break
                self.assertIn(token, output)
            try:
                drain_until(b'27.57 V DC')
                os.write(master, b'jjjjjj')
                drain_until(b'Bench 8')
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 4, 25, 0, 0))
                process.send_signal(signal.SIGWINCH)
                time.sleep(.25)
                os.write(master, b'q')
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                os.close(master)
                os.close(slave)

    def test_discovers_new_meters_live_without_any_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'prefix/lib').mkdir(parents=True)
            bluetooth = root / 'bluetoothctl'
            bluetooth.write_text("#!/usr/bin/env python3\nimport sys,time\n"
                "if sys.argv[1:] == ['devices']: sys.exit(0)\n"
                "print('[NEW] Device AA:BB:CC:DD:EE:01 RS PRO S3', flush=True)\n"
                "time.sleep(.8)\n"
                "print('[NEW] Device AA:BB:CC:DD:EE:02 APPA S3', flush=True)\n"
                "print('[NEW] Device AA:BB:CC:DD:EE:03 headphones', flush=True)\n"
                "print('[CHG] Device AA:BB:CC:DD:EE:01 Name: RS PRO S3', flush=True)\n"
                "time.sleep(20)\n")
            bluetooth.chmod(0o755)
            sigrok = root / 'sigrok-cli'
            sigrok.write_text('#!/bin/bash\n'
                'if [[ "$*" == *EE-01* ]]; then reading="1.11 V DC"; else reading="2.22 V DC"; fi\n'
                'while true; do echo "Primary: $reading"; sleep .1; done\n')
            sigrok.chmod(0o755)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 110, 0, 0))
            process = subprocess.Popen([str(REPO / 'rspro'), 'dashboard', '--below', '1.5',
                '--above', '2', '--unit', 'V', '--mode', 'DC', '--record', str(root / 'discovered.csv')],
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                env={**os.environ, 'TERM': 'xterm-256color', 'RSPRO_ROOT': tmp,
                     'RSPRO_USE_SUDO': 'no', 'RSPRO_CONFIG': str(root / 'missing.json'),
                     'RSPRO_BTMGMT_BIN': str(fake_btmgmt(root)),
                     'RSPRO_BLUETOOTH_BIN': str(bluetooth), 'RSPRO_SIGROK_BIN': str(sigrok)})
            output = bytearray()
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            output.extend(os.read(master, 65536))
                        except OSError:
                            break
                    if b'1.11 V DC' in output and b'2.22 V DC' in output and b'LOW' in output and b'HIGH' in output:
                        break
                self.assertIn(b'1.11 V DC', output)
                self.assertIn(b'2.22 V DC', output)
                self.assertIn(b'LOW', output)
                self.assertIn(b'HIGH', output)
                self.assertNotIn(b'headphones', output)
                self.assertFalse((root / 'missing.json').exists())
                os.write(master, b'q')
                self.assertEqual(process.wait(timeout=5), 0)
                import csv
                with (root / 'discovered.csv').open(newline='') as file:
                    rows = list(csv.DictReader(file))
                self.assertEqual({row['meter_address'] for row in rows},
                                 {'AA:BB:CC:DD:EE:01', 'AA:BB:CC:DD:EE:02'})
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=5)
                os.close(master)
                os.close(slave)

    def test_recording_controls_trends_and_limits_in_terminal(self):
        import csv
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'prefix/lib').mkdir(parents=True)
            config = root / 'meters.json'
            config.write_text(json.dumps({'meters': [{'name': 'Meter one', 'address': 'AA:BB:CC:DD:EE:01'}]}))
            stub = root / 'sigrok-cli'
            stub.write_text('#!/bin/bash\nwhile true; do\n'
                            'echo "Primary: 23 V DC"\n'
                            'echo "Secondary: inf"\nsleep .04\ndone\n')
            stub.chmod(0o755)
            csv_path = root / 'startup.csv'
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 120, 0, 0))
            process = subprocess.Popen([str(REPO / 'rspro'), 'dashboard', '--configured-only',
                '--config', str(config), '--record', str(csv_path), '--below', '24', '--unit', 'V', '--mode', 'DC'],
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                env={**os.environ, 'TERM': 'xterm-256color', 'RSPRO_ROOT': tmp,
                     'RSPRO_USE_SUDO': 'no', 'RSPRO_SIGROK_BIN': str(stub),
                     'RSPRO_BTMGMT_BIN': str(fake_btmgmt(root)), 'XDG_DATA_HOME': str(root / 'data')})
            output = bytearray()
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            output.extend(os.read(master, 65536))
                        except OSError:
                            break
                    if b'LOW' in output and '▄'.encode() in output and b'REC' in output:
                        break
                self.assertIn(b'LOW', output)
                self.assertIn('▄'.encode(), output)
                self.assertIn(b'REC', output)
                os.write(master, b'tr')
                time.sleep(.3)
                os.write(master, b'r\r')
                time.sleep(.3)
                os.write(master, b'q')
                self.assertEqual(process.wait(timeout=5), 0)
                with csv_path.open(newline='') as file:
                    rows = list(csv.DictReader(file))
                self.assertTrue(rows)
                self.assertEqual({row['channel'] for row in rows}, {'measurement', 'additional'})
                self.assertTrue(any(row['value'] == '23.0' and row['unit'] == 'V' for row in rows))
                generated = list((root / 'data/rspro/recordings').glob('*.csv'))
                self.assertEqual(len(generated), 1)
                with generated[0].open(newline='') as file:
                    self.assertTrue(list(csv.DictReader(file)))
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=5)
                os.close(master)
                os.close(slave)

    def test_r_asks_for_meter_and_cancel_does_not_create_recording(self):
        import csv
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'prefix/lib').mkdir(parents=True)
            config = root / 'meters.json'
            config.write_text(json.dumps({'meters': [
                {'name': 'First meter', 'address': 'AA:BB:CC:DD:EE:01'},
                {'name': 'Second meter', 'address': 'AA:BB:CC:DD:EE:02'}]}))
            stub = root / 'sigrok-cli'
            stub.write_text('#!/bin/bash\nwhile true; do echo "Primary: 23 V DC"; sleep .03; done\n')
            stub.chmod(0o755)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 14, 110, 0, 0))
            process = subprocess.Popen([str(REPO / 'rspro'), 'dashboard', '--configured-only', '--config', str(config)],
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                env={**os.environ, 'TERM': 'xterm-256color', 'RSPRO_ROOT': tmp,
                     'RSPRO_USE_SUDO': 'no', 'RSPRO_SIGROK_BIN': str(stub),
                     'RSPRO_BTMGMT_BIN': str(fake_btmgmt(root)), 'XDG_DATA_HOME': str(root / 'data')})
            output = bytearray()
            def wait_for(token):
                deadline = time.monotonic() + 3
                while token not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            output.extend(os.read(master, 65536))
                        except OSError:
                            break
                self.assertIn(token, output)
            try:
                wait_for(b'Second meter')
                os.write(master, b'r')
                wait_for(b'Choose meters to record')
                os.write(master, b'\x1b')
                time.sleep(.3)
                self.assertFalse(list((root / 'data/rspro/recordings').glob('*.csv')))
                os.write(master, b'rj\r')
                time.sleep(.5)
                os.write(master, b'q')
                self.assertEqual(process.wait(timeout=5), 0)
                files = list((root / 'data/rspro/recordings').glob('*.csv'))
                self.assertEqual(len(files), 1)
                with files[0].open(newline='') as file:
                    rows = list(csv.DictReader(file))
                self.assertTrue(rows)
                self.assertEqual({r['meter_address'] for r in rows}, {'AA:BB:CC:DD:EE:02'})
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=5)
                os.close(master)
                os.close(slave)


if __name__ == '__main__':
    unittest.main()
