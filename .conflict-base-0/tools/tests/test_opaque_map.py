import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extract_rosetattoo_sprites import OPAQUE_RESOURCES, load_vgs_image


class OpaqueMapTests(unittest.TestCase):
    def test_native_opaque_backgrounds_do_not_include_transparent_sprites(self):
        self.assertTrue({"MAP.VGS", "JOURNAL.VGS", "DARTBD.VGS", "PAPER.VGS"} <= OPAQUE_RESOURCES)
        self.assertNotIn("MAPICONS.VGS", OPAQUE_RESOURCES)

    def test_map_can_preserve_palette_index_255_as_opaque(self):
        palette = [0] * 768
        palette[255 * 3:256 * 3] = [27, 38, 49]
        data = struct.pack("<HHBBBB", 1, 0, 0, 0, 0, 0) + bytes([255, 12])
        _, transparent = load_vgs_image(data, default_palette=palette)
        _, opaque = load_vgs_image(data, default_palette=palette, opaque=True)
        self.assertEqual(transparent[0].image.getpixel((0, 0))[3], 0)
        self.assertEqual(opaque[0].image.getpixel((0, 0)), (27, 38, 49, 255))


if __name__ == "__main__":
    unittest.main()
