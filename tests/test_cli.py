import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class CLI(unittest.TestCase):
    def run_cli(self, *args, **env):
        return subprocess.run([str(REPO / 'rspro'), *args], text=True,
                              capture_output=True, env={**os.environ, **env})

    def test_extra_arguments_rejected(self):
        self.assertEqual(self.run_cli('--raw', 'ignored').returncode, 2)

    def test_invalid_sudo_mode_rejected(self):
        result = self.run_cli(RSPRO_USE_SUDO='invalid')
        self.assertEqual(result.returncode, 2)
        self.assertIn('RSPRO_USE_SUDO', result.stderr)

    def test_invalid_device_rejected(self):
        result = self.run_cli(RSPRO_DEVICE='not-a-mac')
        self.assertEqual(result.returncode, 2)
        self.assertIn('RSPRO_DEVICE', result.stderr)

    def test_auto_escalates_when_scan_exits_zero_without_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'prefix/lib').mkdir(parents=True)
            sigrok = root / 'sigrok-cli'
            sigrok.write_text('#!/bin/bash\n'
                              '[[ "$*" == *--scan* ]] && exit 0\n'
                              'echo "Primary: 1 V DC"\n')
            sigrok.chmod(0o755)
            sudo = root / 'sudo'
            sudo.write_text('#!/bin/bash\necho sudo-used >&2\nexec "$@"\n')
            sudo.chmod(0o755)
            result = self.run_cli(RSPRO_ROOT=tmp, RSPRO_SIGROK_BIN=str(sigrok),
                                  RSPRO_USE_SUDO='auto', PATH=tmp + ':' + os.environ['PATH'])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('sudo-used', result.stderr)

    def test_filter_raw_connection_and_failure(self):
        with tempfile.TemporaryDirectory(prefix='rspro test ') as tmp:
            root = Path(tmp)
            (root / 'prefix/lib').mkdir(parents=True)
            stub = root / 'sigrok-cli'
            stub.write_text('#!/bin/bash\n'
                            'echo "$*" >&2\n'
                            'echo "Primary: 27.57 V DC"\n'
                            'echo "Secondary: inf"\n'
                            'exit "${TEST_EXIT:-0}"\n')
            stub.chmod(0o755)
            env = dict(RSPRO_ROOT=tmp, RSPRO_SIGROK_BIN=str(stub),
                       RSPRO_USE_SUDO='no', RSPRO_DEVICE='AA:BB:CC:DD:EE:FF')
            result = self.run_cli(**env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('27.57 V DC', result.stdout)
            self.assertNotIn('Secondary', result.stdout)
            self.assertIn('bt/appa-dmm/AA-BB-CC-DD-EE-FF', result.stderr)
            self.assertIn('Secondary', self.run_cli('--raw', **env).stdout)
            self.assertEqual(self.run_cli(TEST_EXIT='7', **env).returncode, 7)


if __name__ == '__main__':
    unittest.main()
