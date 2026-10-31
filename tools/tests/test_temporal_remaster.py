import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import temporal_upscale_rosetattoo_sprites as temporal


class TemporalRemasterTests(unittest.TestCase):
    def test_soft_alpha_preserved_and_resume_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, detail, output = root / "source", root / "detail", root / "output"
            source.mkdir()
            detail.mkdir()
            records = []
            for index in range(2):
                name = f"frame_{index:03d}"
                Image.new("RGBA", (3, 3), (40 + index, 80, 120, 255)).save(source / f"{name}.png")
                image = Image.new("RGBA", (6, 6), (50 + index, 90, 130, 255))
                image.putpixel((0, 0), (50, 90, 130, 80))
                image.save(detail / f"{name}@2x.png")
                records.append({"file": f"{name}.png", "width": 3, "height": 3, "offset_x": 0, "offset_y": 0})
            (source / "metadata.json").write_text(json.dumps({"frames": records}))
            (detail / "metadata.json").write_text(json.dumps({"complete": True, "scale": 2, "alpha_mode": "soft"}))
            result = temporal.stabilize_sequence(source, detail, output, 2)
            self.assertTrue(result["complete"])
            self.assertEqual(result["alpha_mode"], "soft")
            for index in range(2):
                name = f"frame_{index:03d}@2x.png"
                self.assertTrue(np.array_equal(np.asarray(Image.open(detail / name).getchannel("A")),
                                               np.asarray(Image.open(output / name).getchannel("A"))))
            with patch.object(temporal, "rgba_array", side_effect=AssertionError("should reuse")):
                temporal.stabilize_sequence(source, detail, output, 2, skip_existing=True)
            # A changed input invalidates the completed-sequence fingerprint.
            Image.new("RGBA", (3, 3), (1, 2, 3, 255)).save(source / "frame_000.png")
            with patch.object(temporal, "rgba_array", side_effect=RuntimeError("reprocessing")):
                with self.assertRaisesRegex(RuntimeError, "reprocessing"):
                    temporal.stabilize_sequence(source, detail, output, 2, skip_existing=True)


if __name__ == "__main__":
    unittest.main()
