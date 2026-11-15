"""LLM client selection: remote primary with local fallback; no server needed."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster.llm import ChatClient, strip_thinking

LLM = {
    "text_base_url": "http://127.0.0.1:9/v1",  # discard port: nothing listens
    "text_model": "remote-model",
    "fallback_base_url": "http://127.0.0.1:11434/v1",
    "fallback_text_model": "local-model",
    "timeout_s": 5,
}


class ClientSelectionTest(unittest.TestCase):
    def test_unreachable_primary_falls_back(self):
        client = ChatClient.from_config(LLM, "text")
        self.assertEqual((client.base_url, client.model), ("http://127.0.0.1:11434/v1", "local-model"))

    def test_no_probe_or_no_fallback_keeps_primary(self):
        self.assertEqual(ChatClient.from_config(LLM, "text", probe=False).model, "remote-model")
        bare = {k: v for k, v in LLM.items() if not k.startswith("fallback")}
        self.assertEqual(ChatClient.from_config(bare, "text").model, "remote-model")

    def test_strip_thinking(self):
        self.assertEqual(strip_thinking("<think>hmm</think> answer"), "answer")
        self.assertEqual(strip_thinking("reasoning</think>answer"), "answer")


if __name__ == "__main__":
    unittest.main()
