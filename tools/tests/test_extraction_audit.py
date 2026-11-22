import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_rosetattoo_extraction import compare_resource


class ExtractionAuditTests(unittest.TestCase):
    def test_detects_pixel_changes_and_missing_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            resource = Path(temporary)
            record = {"index": 0, "file": "frame_000.png", "width": 2, "height": 2,
                      "offset_x": 1, "offset_y": 2}
            (resource / "metadata.json").write_text(json.dumps({
                "scene_id": 26, "frame_count": 1, "frames": [record],
            }))
            expected = Image.new("RGBA", (2, 2), (1, 2, 3, 255))
            expected.save(resource / record["file"])
            self.assertEqual(compare_resource(resource, [(record, expected)], {"scene_id": 26}), 1)
            Image.new("RGBA", (2, 2), (4, 2, 3, 255)).save(resource / record["file"])
            with self.assertRaisesRegex(ValueError, "pixels differ"):
                compare_resource(resource, [(record, expected)], {"scene_id": 26})
            (resource / record["file"]).unlink()
            with self.assertRaises(FileNotFoundError):
                compare_resource(resource, [(record, expected)], {"scene_id": 26})


if __name__ == "__main__":
    unittest.main()
