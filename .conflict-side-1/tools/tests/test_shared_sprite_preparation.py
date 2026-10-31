import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_rosetattoo_shared_sprites as shared


class SharedSpritePreparationTests(unittest.TestCase):
    def test_repairs_are_isolated_and_source_is_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, data, output = root / "source", root / "data", root / "output"
            (source / "map_vgs").mkdir(parents=True)
            (source / "other_vgs").mkdir()
            data.mkdir()
            (source / "map_vgs" / "original").write_text("preserve")
            (source / "other_vgs" / "metadata.json").write_text(json.dumps({"frames": []}))

            def fake_extract(data_dir, resource, directory, palette_scene=None, palette_resource=None):
                directory.mkdir()
                (directory / "metadata.json").write_text(json.dumps({"frames": []}))
                return {"frame_count": 0, "fallback_palette": palette_resource or str(palette_scene)}

            with patch.object(shared, "extract_resource", side_effect=fake_extract):
                report = shared.prepare(source, output, data)
            self.assertEqual(len(report["repaired"]), 16)
            self.assertEqual((source / "map_vgs" / "original").read_text(), "preserve")
            self.assertFalse((output / "map_vgs" / "original").exists())
            self.assertTrue((output / "other_vgs" / "metadata.json").is_file())
            with self.assertRaises(FileExistsError):
                shared.prepare(source, output, data)


if __name__ == "__main__":
    unittest.main()
