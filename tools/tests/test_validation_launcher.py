import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


LAUNCHER = Path(__file__).resolve().parents[1] / "run_rosetattoo_validation.py"


class ValidationLauncherTests(unittest.TestCase):
    def test_postload_scene_requires_save_and_prints_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [sys.executable, str(LAUNCHER), "--scummvm", "/unused/scummvm",
                       "--save-dir", str(root / "saves"), "--screenshot-dir", str(root / "screenshots"),
                       "--postload-scene", "26", "--print-only"]
            missing = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("requires --save-slot", missing.stderr)
            valid = subprocess.run(command + ["--save-slot", "0"], capture_output=True, text=True)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn("SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE=26", valid.stdout)
            self.assertIn("--save-slot=0", valid.stdout)


if __name__ == "__main__":
    unittest.main()
