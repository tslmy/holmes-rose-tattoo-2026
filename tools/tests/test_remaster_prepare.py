"""De-dither and plate building on synthetic images; no game data needed."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster import dedither, plates


def checkerboard(h, w, a, b):
    yy, xx = np.mgrid[0:h, 0:w]
    out = np.where(((yy + xx) % 2 == 0)[..., None], np.array(a, np.uint8), np.array(b, np.uint8))
    return out.astype(np.uint8)


class DeditherTest(unittest.TestCase):
    def test_checkerboard_of_close_colours_flattens_to_mean(self):
        rgb = checkerboard(24, 24, (100, 80, 60), (120, 96, 72))
        out = dedither.dedither_array(rgb, threshold=48, iterations=2)
        inner = out[3:-3, 3:-3].astype(int)
        self.assertLessEqual(np.abs(inner - np.array([110, 88, 66])).max(), 3)
        before = dedither.dither_energy(Image.fromarray(rgb))
        after = dedither.dither_energy(Image.fromarray(out))
        self.assertLess(after, before * 0.2)

    def test_hard_edge_is_preserved_exactly(self):
        rgb = np.zeros((16, 16, 3), np.uint8)
        rgb[:, 8:] = (230, 220, 40)
        out = dedither.dedither_array(rgb, threshold=48)
        self.assertTrue(np.array_equal(out, rgb))

    def test_text_like_strokes_survive(self):
        rgb = np.full((20, 40, 3), 30, np.uint8)
        rgb[8:12, 5:35] = 240  # a bright stroke, like a sign letter
        out = dedither.dedither_array(rgb, threshold=48)
        self.assertTrue(np.array_equal(out[8:12, 5:35], rgb[8:12, 5:35]))

    def test_deterministic_bytes(self):
        rng = np.random.default_rng(7)
        rgb = rng.integers(0, 256, (40, 50, 3), dtype=np.uint8)
        first = dedither.dedither_array(rgb).tobytes()
        second = dedither.dedither_array(rgb.copy()).tobytes()
        self.assertEqual(first, second)

    def test_alpha_is_untouched_and_transparent_pixels_do_not_bleed(self):
        rgba = np.zeros((12, 12, 4), np.uint8)
        rgba[..., :3] = (100, 100, 100)
        rgba[:, :6, 3] = 255
        rgba[:, 6:, :3] = (110, 110, 110)  # hidden colour behind transparency
        image = Image.fromarray(rgba, "RGBA")
        out = np.asarray(dedither.dedither_image(image))
        self.assertTrue(np.array_equal(out[..., 3], rgba[..., 3]))
        self.assertTrue(np.array_equal(out[:, :6, :3], rgba[:, :6, :3]))
        self.assertTrue(np.array_equal(out[:, 6:], rgba[:, 6:]))

    def test_edge_map_marks_a_step(self):
        rgb = np.zeros((16, 16, 3), np.uint8)
        rgb[:, 8:] = 255
        edges = np.asarray(dedither.edge_map(Image.fromarray(rgb)))
        self.assertTrue(edges[:, 7:9].all())
        self.assertFalse(edges[:, :5].any())


class PlateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def room(self, slot, colour, size=(4, 3), offset=(0, 0)):
        directory = self.root / f"room_{slot:03d}"
        directory.mkdir()
        Image.new("RGBA", size, colour).save(directory / "frame_000.png")
        meta = {"frames": [{"index": 0, "file": "frame_000.png", "width": size[0], "height": size[1],
                            "offset_x": offset[0], "offset_y": offset[1]}]}
        (directory / "metadata.json").write_text(json.dumps(meta))

    @staticmethod
    def obj(index, slot, x, y, flags=0, required=(0, 0), sprite_type="active_bg_shape", scale=256):
        return {"index": index, "name": f"o{index}", "sprite_type": sprite_type, "image_index": slot,
                "position": {"x": x, "y": y}, "flags": flags, "required_flags": list(required),
                "scale_val": scale}

    def test_visibility_order_offset_and_flip(self):
        self.room(1, (255, 0, 0, 255), size=(2, 2))
        self.room(2, (0, 255, 0, 255), offset=(1, 1))
        self.room(3, (0, 0, 255, 255))
        # Half-transparent sprite to check flipping.
        directory = self.root / "room_004"
        directory.mkdir()
        sprite = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
        sprite.putpixel((0, 0), (255, 255, 0, 255))
        sprite.save(directory / "frame_000.png")
        (directory / "metadata.json").write_text(json.dumps({"frames": [
            {"index": 0, "file": "frame_000.png", "width": 2, "height": 1, "offset_x": 0, "offset_y": 0}]}))
        metadata = {"objects": [
            self.obj(1, 1, 2, 2, flags=OBJ_FORWARD | 1),       # FORWARD: drawn last
            self.obj(2, 2, 1, 1, flags=1),                     # NORMAL, offset (1,1) -> at (2,2)
            self.obj(3, 3, 0, 0, flags=0),                     # BEHIND: drawn first
            self.obj(4, 3, 0, 0, required=(5, 0)),             # needs flag 5: hidden
            self.obj(5, 3, 0, 0, required=(-5, 0)),            # needs flag 5 off: shown (BEHIND)
            self.obj(6, 3, 0, 0, sprite_type="static_bg_shape"),
            self.obj(7, 4, 10, 0, flags=2),                    # BEHIND, flipped
            self.obj(8, 9, 0, 0),                              # missing room image
        ]}
        layers, skipped = plates.plan_layers(metadata, self.root)
        self.assertEqual([l.object_index for l in layers], [3, 5, 7, 2, 1])
        self.assertEqual({s["object_index"] for s in skipped}, {4, 8})
        self.assertEqual((layers[3].x, layers[3].y), (2, 2))
        background = Image.new("RGB", (16, 8), (9, 9, 9))
        plate, index = plates.build_plate(background, layers)
        self.assertEqual(plate.getpixel((2, 2)), (255, 0, 0))  # FORWARD over NORMAL
        self.assertEqual(plate.getpixel((5, 3)), (0, 255, 0))  # NORMAL beyond the FORWARD sprite
        self.assertEqual(plate.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(plate.getpixel((10, 0)), (9, 9, 9))   # flipped: opaque pixel moved right
        self.assertEqual(plate.getpixel((11, 0)), (255, 255, 0))
        self.assertEqual(index.getpixel((2, 2)), 5)
        self.assertEqual(index.getpixel((15, 7)), 0)

    def test_normal_shapes_sort_by_bottom_edge(self):
        self.room(1, (255, 0, 0, 255), size=(4, 4))
        self.room(2, (0, 255, 0, 255), size=(4, 2))
        metadata = {"objects": [self.obj(1, 1, 0, 0, flags=1), self.obj(2, 2, 0, 0, flags=1)]}
        layers, _ = plates.plan_layers(metadata, self.root)
        self.assertEqual([l.object_index for l in layers], [2, 1])  # bottom 2 before bottom 4

    def test_perspective_scale_matches_engine_arithmetic(self):
        self.room(1, (255, 0, 0, 255), size=(10, 20))
        layers, _ = plates.plan_layers({"objects": [self.obj(1, 1, 0, 0, scale=512)]}, self.root)
        self.assertEqual((layers[0].width, layers[0].height), (5, 10))  # (n-1)*256/512+1

    def test_sprites_partly_off_plate_are_clipped(self):
        self.room(1, (255, 0, 0, 255), size=(6, 6))
        layers, _ = plates.plan_layers({"objects": [self.obj(1, 1, -3, 5)]}, self.root)
        plate, _ = plates.build_plate(Image.new("RGB", (8, 8)), layers)
        self.assertEqual(plate.getpixel((0, 7)), (255, 0, 0))


OBJ_FORWARD = plates.OBJ_FORWARD

if __name__ == "__main__":
    unittest.main()
