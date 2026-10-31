import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smoke_rosetattoo_scenes as smoke


class SceneSmokeTests(unittest.TestCase):
    def run_code(self, code, scene=7, timeout=1):
        with tempfile.TemporaryDirectory() as tmp:
            return smoke.run_child([sys.executable, "-c", code], os.environ.copy(),
                                   Path(tmp), scene, timeout)

    def test_exact_split_marker_counts_and_child_is_stopped(self):
        code = ("import os, signal; os.write(1,b'SHERLOCK_SCENE_SMOKE_'); "
                "os.write(1,b'RENDERED 7\\n'); signal.pause()")
        result, status, output, _ = self.run_code(code)
        self.assertEqual(result, "rendered")
        self.assertEqual(status, -signal.SIGTERM)
        self.assertIn(b"SHERLOCK_SCENE_SMOKE_RENDERED 7\n", output)

    def test_alive_wrong_scene_and_partial_marker_are_not_success(self):
        for text in ("SHERLOCK_SCENE_SMOKE_RENDERED 1\\n", "SHERLOCK_SCENE_SMOKE_RENDERED 7"):
            with self.subTest(text=text):
                code = f"import os, signal; os.write(1,b'{text}'); signal.pause()"
                result, _, _, _ = self.run_code(code, timeout=0.15)
                self.assertEqual(result, "timeout")

    def test_crash_and_clean_exit_without_marker(self):
        self.assertEqual(self.run_code("raise SystemExit(4)")[0], "crash_or_error")
        self.assertEqual(self.run_code("print('no marker')")[0], "exited_without_marker")

    def test_closed_stdout_and_overlong_line_are_not_success(self):
        code = "import os, signal; os.close(1); os.close(2); signal.pause()"
        self.assertEqual(self.run_code(code, timeout=0.15)[0], "timeout")
        code = ("import os, signal; os.write(1,b'x'*10000); "
                "os.write(1,b'SHERLOCK_SCENE_SMOKE_RENDERED 7\\n'); signal.pause()")
        self.assertEqual(self.run_code(code, timeout=0.15)[0], "timeout")

    def test_log_is_bounded_even_when_child_is_noisy(self):
        result, _, output, _ = self.run_code(
            "import os; os.write(1, b'x' * 1100000); raise SystemExit(3)")
        self.assertEqual(result, "crash_or_error")
        self.assertEqual(len(output), smoke.LOG_LIMIT)

    def test_environment_discards_conflicting_hooks(self):
        with mock.patch.dict(os.environ, {"SCUMMVM_SHERLOCK_TATTOO_START_SCENE": "99",
                                          "SDL_VIDEODRIVER": "bad"}):
            env = smoke.child_environment(7, Path("/pack"), Path("/home"))
        self.assertNotIn("SCUMMVM_SHERLOCK_TATTOO_START_SCENE", env)
        self.assertEqual(env["SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE"], "7")
        self.assertEqual(env["SDL_VIDEODRIVER"], "dummy")
        self.assertEqual(env["SCUMMVM_SHERLOCK_TATTOO_SPRITE_FILTER"], "edge")
        self.assertNotIn("SCUMMVM_SHERLOCK_TATTOO_HAZE_NATIVE_FALLBACK", env)
        enabled = smoke.child_environment(7, Path("/pack"), Path("/home"), True)
        self.assertEqual(enabled["SCUMMVM_SHERLOCK_TATTOO_HAZE_NATIVE_FALLBACK"], "1")

    def test_discovers_only_numbered_scene_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("RES00.RRM", "RES01.RRM", "res07.rrm", "RES99.RRM", "RES100.RRM", "RES08.TXT"):
                (root / name).write_bytes(b"")
            (root / "RES02.RRM").mkdir()
            self.assertEqual(smoke.available_scenes(root), [1, 7, 99])

    def test_timeout_and_other_errors_are_not_retried(self):
        for outcome in ("timeout", "crash_or_error", "exited_without_marker"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data, pack, saves = (root / name for name in ("game", "pack", "saves"))
                for path in (data, pack, saves):
                    path.mkdir()
                (data / "RES07.RRM").write_bytes(b"data")
                save = saves / "save"
                save.write_bytes(b"save")
                output = root / "run"
                args = ["--scummvm", sys.executable, "--data-dir", str(data),
                        "--pack", str(pack), "--save", str(save), "--output-dir", str(output),
                        "--scenes", "7", "--sdl-retries", "3"]
                with mock.patch.object(smoke, "run_child", return_value=(outcome, 1, b"error", 0.1)) as child:
                    self.assertEqual(smoke.main(args), 1)
                self.assertEqual(child.call_count, 1)
                report = json.loads((output / "report.json").read_text())
                self.assertTrue(report["completed"])
                self.assertEqual(report["scenes"][0]["attempts"][0]["result"], outcome)

    def test_driver_isolates_each_save_config_and_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, pack, saves = (root / name for name in ("game", "pack", "source-saves"))
            for path in (data, pack, saves):
                path.mkdir()
            for n in (1, 7):
                (data / f"RES{n:02d}.RRM").write_bytes(b"data")
            save = saves / "rosetattoo.123"
            save.write_bytes(b"original save")
            asset = pack / "asset"
            asset.write_bytes(b"untouched")
            fake = root / "fake-scummvm"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, signal, sys\n"
                "args = dict(a[2:].split('=',1) for a in sys.argv[1:] if a.startswith('--') and '=' in a)\n"
                "assert pathlib.Path(args['config']).parent == pathlib.Path.cwd()\n"
                "assert os.environ['HOME'] == str(pathlib.Path.cwd())\n"
                "assert os.environ['SDL_VIDEODRIVER'] == 'dummy'\n"
                "assert os.environ['SCUMMVM_SHERLOCK_TATTOO_HAZE_NATIVE_FALLBACK'] == '1'\n"
                "p = pathlib.Path(args['savepath']) / 'rosetattoo.000'\n"
                "assert p.read_bytes() == b'original save'\n"
                "p.write_bytes(b'changed copy')\n"
                "if pathlib.Path.cwd().name == 'attempt-01':\n"
                " print(\"SDL_BlitSurface failed: Parameter 'src' is invalid!\", flush=True)\n"
                " raise SystemExit(1)\n"
                "print('SHERLOCK_SCENE_SMOKE_RENDERED ' + os.environ['SCUMMVM_SHERLOCK_TATTOO_POSTLOAD_SCENE'], flush=True)\n"
                "signal.pause()\n", encoding="utf-8")
            fake.chmod(0o755)
            output = root / "run"
            command = [sys.executable, smoke.__file__, "--scummvm", str(fake),
                       "--data-dir", str(data), "--pack", str(pack), "--save", str(save),
                       "--output-dir", str(output), "--scenes", "1", "7", "--timeout", "2",
                        "--sdl-retries", "1", "--haze-native-fallback"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "report.json").read_text())
            self.assertTrue(report["completed"])
            self.assertTrue(report["renderer"]["haze_native_fallback"])
            self.assertEqual(report["selected_scenes"], [1, 7])
            self.assertEqual([x["result"] for x in report["scenes"]], ["rendered", "rendered"])
            self.assertEqual([x["result"] for x in report["scenes"][0]["attempts"]],
                             ["sdl_blit_error", "rendered"])
            self.assertEqual(save.read_bytes(), b"original save")
            self.assertEqual(asset.read_bytes(), b"untouched")
            for n in (1, 7):
                for attempt in (1, 2):
                    copy = output / f"scene-{n:03d}" / f"attempt-{attempt:02d}" / "saves" / "rosetattoo.000"
                    self.assertEqual(copy.read_bytes(), b"changed copy")
            # Never reuse an output directory, or write under a protected input.
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            command[command.index(str(output))] = str(pack / "new-run")
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
