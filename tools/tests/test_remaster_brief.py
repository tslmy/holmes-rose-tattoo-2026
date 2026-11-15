"""`brief` stage: end-to-end run_job with an unreachable LLM and a mocked one.

Model-free: the LLM is pointed at 127.0.0.1:9 with no fallback, so the
stage must finish via its keyword fallbacks; a second test patches
`ChatClient.chat` to feed a canned reply.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster import llm  # noqa: E402
from remaster.config import load_config  # noqa: E402
from remaster.stages import brief, extract, prepare  # noqa: E402

SCENE = 4


def build_repo(tmp: str) -> None:
    """Throwaway repo: remaster.toml plus the inputs brief.run_job reads."""
    path = Path(tmp) / "remaster.toml"
    path.write_text('[paths]\nwork = "work"\nreview = "review"\n'
                    '[llm]\nbase_url = "http://127.0.0.1:9/v1"\n'
                    'vision_model = "nothing"\ntext_model = "nothing"\ntimeout_s = 2\n',
                    encoding="utf-8")
    rooms = extract.scene_dir(load_config(path), "rooms", SCENE)
    rooms.mkdir(parents=True)
    (rooms / "metadata.json").write_text(json.dumps({
        "scene_name": "Outside Scotland Yard",
        "objects": [
            {"index": 1, "name": "sign1", "description": "Street Sign", "sprite_type": "no_shape",
             "action_type": "object", "bounds": {"x": 222, "y": 195, "w": 35, "h": 16}},
            {"index": 2, "name": "Burn", "description": "Police Constable", "sprite_type": "active_bg_shape",
             "action_type": "person", "bounds": {"x": 349, "y": 209, "w": 1, "h": 1}},
        ]},
    ), encoding="utf-8")
    prep_dir = prepare.out_root(load_config(path)) / f"scene_{SCENE:03d}"
    prep_dir.mkdir(parents=True)
    (prep_dir / "plate.json").write_text(json.dumps({
        "size": [640, 480],
        "layers": [{"object_index": 2, "x": 374, "y": 216, "width": 45, "height": 156}],
    }), encoding="utf-8")
    from PIL import Image
    Image.new("RGB", (640, 480), (120, 100, 90)).save(prep_dir / "plate_dedither.png")


class BriefRunJobTest(unittest.TestCase):
    def run_fallback(self, tmp: str, fp: str = "fp1") -> dict:
        cfg = load_config(Path(tmp) / "remaster.toml")
        return brief.run_job(cfg, {"scene": SCENE}, fp)

    def test_unreachable_llm_finishes_with_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_repo(tmp)
            result = self.run_fallback(tmp)
            self.assertNotIn("skipped", result)
            self.assertEqual(result["objects"], 2)
            self.assertGreaterEqual(result["fallbacks"], 1)
            self.assertIsNone(result["seconds"])  # the LLM never answered

            target = Path(tmp) / "work" / "brief" / "scene_004"
            visual = (target / "visual_brief.txt").read_text(encoding="utf-8")
            self.assertTrue(visual.startswith(brief.STYLE))
            self.assertIn("Outside Scotland Yard", visual)
            self.assertIn("Street Sign", visual)
            self.assertIn("Police Constable", visual)

            objects = json.loads((target / "objects.json").read_text(encoding="utf-8"))
            self.assertEqual(objects["size"], [640, 480])
            self.assertEqual([o["id"] for o in objects["objects"]], ["o01", "o02"])
            sign, constable = objects["objects"]
            self.assertEqual(sign["query"], "street sign")
            self.assertTrue(sign["writing"])
            self.assertEqual(sign["box"], [222, 195, 35, 16])
            self.assertEqual(constable["query"], "person")
            self.assertEqual(constable["box"], [374, 216, 45, 156])
            self.assertFalse(constable["writing"])

            record = json.loads((target / "brief.json").read_text(encoding="utf-8"))
            self.assertTrue(record["fallback"])
            self.assertTrue(any("llm" in entry for entry in record["fallback"]),
                            record["fallback"])

    def test_same_fingerprint_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_repo(tmp)
            self.run_fallback(tmp)
            self.assertEqual(self.run_fallback(tmp), {"skipped": True})

    def test_llm_reply_drives_brief_and_o01(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_repo(tmp)
            reply = ('Here you go:\n'
                     '{"brief": "A grey stone police station.", '
                     '"objects": [{"id": "o01", "query": "enamel sign", "writing": true, "clue": false}]}')
            # A plain client, not a MagicMock: common.write_json falls back to
            # hasattr(x, "tolist"), which a MagicMock answers True for, and the
            # JSON dump then hangs on its auto-created attributes.
            client = llm.ChatClient(base_url="http://127.0.0.1:9/v1", model="nothing", timeout=2.0)
            with mock.patch.object(llm.ChatClient, "chat",
                                   return_value=llm.ChatResult(text=reply, seconds=0.5)) as chat, \
                    mock.patch.object(llm.ChatClient, "from_config", return_value=client) as from_config:
                result = self.run_fallback(tmp, fp="fp2")
                from_config.assert_called_once()
                self.assertEqual(chat.call_count, 1)

            self.assertNotIn("skipped", result)
            self.assertEqual(result["fallbacks"], 1)  # only o02 keeps the fallback

            target = Path(tmp) / "work" / "brief" / "scene_004"
            visual = (target / "visual_brief.txt").read_text(encoding="utf-8")
            self.assertTrue(visual.startswith(brief.STYLE))
            self.assertIn("A grey stone police station.", visual)

            objects = json.loads((target / "objects.json").read_text(encoding="utf-8"))
            sign, constable = objects["objects"]
            self.assertEqual(sign["query"], "enamel sign")
            self.assertTrue(sign["writing"])
            self.assertEqual(constable["query"], "person")  # no entry for o02 -> fallback
            self.assertEqual(constable["box"], [374, 216, 45, 156])

            record = json.loads((target / "brief.json").read_text(encoding="utf-8"))
            self.assertEqual(record["seconds"], 0.5)
            self.assertEqual(len(record["fallback"]), 1)
            self.assertEqual(record["fallback"][0], "o02: keyword query/flags")


if __name__ == "__main__":
    unittest.main()
