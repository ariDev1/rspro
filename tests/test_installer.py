import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class Installer(unittest.TestCase):
    def test_help_has_no_install_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([str(REPO / 'install_rspro_cli.sh'), '--help'],
                                    env={**os.environ, 'HOME': tmp},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('libsigrok', result.stdout)
            self.assertFalse((Path(tmp) / '.local').exists())

    def test_unknown_option_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([str(REPO / 'install_rspro_cli.sh'), '--unknown'],
                                    env={**os.environ, 'HOME': tmp},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((Path(tmp) / '.local').exists())
