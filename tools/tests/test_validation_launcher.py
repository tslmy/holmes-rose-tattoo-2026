import os
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
            valid = subprocess.run(command + ["--save-slot", "0", "--palette-safe-sprites"], capture_output=True, text=True)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn("SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE=26", valid.stdout)
            self.assertIn("--save-slot=0", valid.stdout)
            self.assertIn("SCUMMVM_SHERLOCK_TATTOO_PALETTE_SAFE_SPRITES=1", valid.stdout)

    def test_postload_and_palette_flags_reach_child_without_conflicting_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "env.txt"
            executable = root / "fake-scummvm"
            executable.write_text(
                '#!/bin/sh\n'
                'printf "%s|%s|%s|%s" "$SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE" '
                '"$SCUMMVM_SHERLOCK_TATTOO_PALETTE_SAFE_SPRITES" '
                '"$SCUMMVM_SHERLOCK_TATTOO_START_SCENE" '
                '"$SCUMMVM_SHERLOCK_TATTOO_SPRITE_FILTER" > "$CAPTURE_PATH"\n'
            )
            executable.chmod(0o755)
            env = os.environ.copy()
            env["CAPTURE_PATH"] = str(capture)
            env["SCUMMVM_SHERLOCK_TATTOO_START_SCENE"] = "99"
            command = [sys.executable, str(LAUNCHER), "--scummvm", str(executable),
                       "--save-dir", str(root / "saves"), "--screenshot-dir", str(root / "screenshots"),
                       "--postload-scene", "26", "--save-slot", "0", "--palette-safe-sprites",
                       "--sprite-filter", "bilinear"]
            result = subprocess.run(command, capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(capture.read_text(), "26|1||bilinear")


if __name__ == "__main__":
    unittest.main()
