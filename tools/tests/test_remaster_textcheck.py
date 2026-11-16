"""Acceptance test for delegate task `text-check` (Phase 1b). Written by Opus; do not edit to make it pass."""

import inspect
import sys
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster import textcheck  # noqa: E402
from remaster.llm import ChatResult  # noqa: E402
from remaster.stages import geometry, qa  # noqa: E402


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, system, user, *, images=None, max_tokens=None, **kw):
        self.calls.append({"user": user, "images": images})
        return ChatResult(text=self.replies.pop(0), seconds=0.1)


class TextCheckTest(unittest.TestCase):
    def test_normalise_and_match(self):
        self.assertEqual(textcheck.normalise(" Metropolitan\nPolice! "), "METROPOLITANPOLICE")
        self.assertTrue(textcheck.text_matches("METROPOLITAN POLICE", "Metropolitan  Police"))
        self.assertFalse(textcheck.text_matches("METROPOLITAN POLICE", "NETBOPOLITAN POLICF"))
        self.assertIsNone(textcheck.text_matches("", "anything"))   # original unreadable: unknown
        self.assertFalse(textcheck.text_matches("POLICE", ""))

    def test_read_text_crops_upscales_and_parses(self):
        img = Image.new("RGB", (200, 100), "white")
        client = FakeClient(['Sure: {"text": "Metropolitan Police"}'])
        self.assertEqual(textcheck.read_text(client, img, [10, 20, 40, 10]), "Metropolitan Police")
        sent = client.calls[0]["images"][0]
        with Image.open(sent) as crop:
            self.assertGreaterEqual(max(crop.size), 512)          # small crops are upscaled before reading
        self.assertEqual(textcheck.read_text(FakeClient(["no json here"]), img, [0, 0, 10, 10]), "")

    def test_text_gate(self):
        ok, missing = textcheck.text_gate(["METROPOLITAN POLICE", "No", "Phrenologist"],
                                          ["Metropolitan Police", "Phrenologist, heads read"])
        self.assertTrue(ok)
        self.assertEqual(missing, [])          # "No" is under 4 characters and ignored; substring counts
        ok, missing = textcheck.text_gate(["GENERAL STORE"], ["GENFRAL STQRE CO"])
        self.assertFalse(ok)
        self.assertEqual(missing, ["GENERAL STORE"])

    def test_geometry_decides_per_writing_region(self):
        src = inspect.getsource(geometry.estimate)
        self.assertIn("textcheck.read_text(", src)
        self.assertIn("textcheck.text_matches(", src)
        self.assertIn("redraw text legible", src)

    def test_qa_has_text_gate(self):
        self.assertIn("textcheck.text_gate(", inspect.getsource(qa))
        self.assertIn('gates["text"]', inspect.getsource(qa.evaluate))
        self.assertIn("original_text", qa.VLM_TEXT)


if __name__ == "__main__":
    unittest.main()
