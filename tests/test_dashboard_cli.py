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
                   'RSPRO_USE_SUDO': 'no', 'RSPRO_SIGROK_BIN': str(stub)}
            process = subprocess.Popen([str(REPO / 'rspro'), 'dashboard', '--config', str(config)],
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


if __name__ == '__main__':
    unittest.main()
