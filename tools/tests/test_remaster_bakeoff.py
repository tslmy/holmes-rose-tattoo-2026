"""Phase 1 stages: warp fitting and checks, brief objects, QA gates, sprite scores, picks.

Model-free: no diffusion, detector or LLM is loaded. Needs the `bakeoff` extra
(OpenCV) and `contour-audit` (scipy).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remaster import bakeoff, imagegen  # noqa: E402
from remaster.config import load_config  # noqa: E402
from remaster.stages import brief, geometry, qa, sprite_test  # noqa: E402
from remaster.warp import GridWarp, fit_grid_warp, match_points  # noqa: E402


def smooth_field(p):
    return p + np.stack([6 * np.sin(p[:, 1] / 120), 4 * np.cos(p[:, 0] / 150)], 1)


class WarpTest(unittest.TestCase):
    def test_fit_recovers_smooth_warp_despite_outliers(self):
        rng = np.random.default_rng(1)
        src = rng.uniform([0, 0], [640, 480], (3000, 2))
        dst = smooth_field(src)
        dst[:300] += rng.normal(0, 40, (300, 2))  # 10% gross mismatches
        warp = fit_grid_warp(640, 480, (1.6, 1.6), src, dst)
        test = rng.uniform([20, 20], [620, 460], (500, 2))
        self.assertLess(np.abs(warp.to_original_units(test) - smooth_field(test)).max(), 1.5)
        stats = warp.jacobian_stats()
        self.assertGreater(stats["min_det"], 0.9)
        self.assertLess(stats["max_stretch"], 1.1)
        self.assertLess(warp.roundtrip_error(), 0.05)

    def test_inverse_and_forward_scale(self):
        warp = GridWarp.identity(640, 480, (1.6, 2.0))
        warp.dx[:] = 3.0
        p = np.array([[100.0, 50.0], [600.0, 400.0]])
        np.testing.assert_allclose(warp.forward(p), (p + [3, 0]) * [1.6, 2.0])
        np.testing.assert_allclose(warp.inverse(warp.forward(p)), p, atol=1e-3)
        mx, my = warp.inverse_maps((64, 48))
        self.assertEqual(mx.shape, (48, 64))

    def test_fold_is_detected(self):
        warp = GridWarp.identity(640, 480, (1, 1), spacing=40)
        warp.dx[:, 8] = 60.0  # one column of nodes jumps past its right neighbour
        stats = warp.jacobian_stats()
        self.assertLess(stats["min_det"], 0)
        self.assertGreater(stats["max_stretch"], 1.6)

    def test_json_round_trip(self):
        warp = GridWarp.identity(1280, 480, (1.2, 1.2))
        warp.dy[2, 3] = 1.5
        again = GridWarp.from_json(json.loads(json.dumps(warp.to_json())))
        np.testing.assert_allclose(again.dy, warp.dy)
        self.assertEqual(again.scale, (1.2, 1.2))

    def test_match_points_finds_a_shift(self):
        import cv2

        rng = np.random.default_rng(3)
        base = cv2.GaussianBlur((rng.random((240, 320)) * 255).astype(np.uint8), (0, 0), 2)
        base = cv2.normalize(base, None, 0, 255, cv2.NORM_MINMAX)
        moved = cv2.warpAffine(base, np.float32([[1, 0, 4], [0, 1, -2]]), (320, 240), borderMode=cv2.BORDER_REFLECT)
        m = match_points(base, moved)
        self.assertGreater(len(m["src"]), 50)
        warp = fit_grid_warp(320, 240, (1, 1), m["src"], m["dst"], m["weights"])
        np.testing.assert_allclose(warp.to_original_units(np.array([[160.0, 120.0]]))[0], [164, 118], atol=0.7)


class BriefTest(unittest.TestCase):
    def test_room_objects_merges_states_and_uses_layer_boxes(self):
        metadata = {"objects": [
            {"index": 1, "name": "sign1", "description": "Street Sign", "sprite_type": "no_shape",
             "action_type": "object", "bounds": {"x": 221, "y": 196, "w": 38, "h": 17}},
            {"index": 2, "name": "sign2", "description": "Street Sign", "sprite_type": "no_shape",
             "action_type": "object", "bounds": {"x": 222, "y": 195, "w": 35, "h": 16}},
            {"index": 3, "name": "Burn", "description": "Police Constable", "sprite_type": "active_bg_shape",
             "action_type": "person", "bounds": {"x": 349, "y": 209, "w": 1, "h": 1}},
            {"index": 4, "name": "Hidden", "description": "Ghost", "sprite_type": "hidden",
             "action_type": "object", "bounds": {"x": 0, "y": 0, "w": 50, "h": 50}},
            {"index": 5, "name": "Off", "description": "Nowhere", "sprite_type": "active_bg_shape",
             "action_type": "object", "bounds": {"x": 1, "y": 1, "w": 1, "h": 1}},
        ]}
        plate = {"size": [640, 480], "layers": [{"object_index": 3, "x": 374, "y": 216, "width": 45, "height": 156}]}
        objects = brief.room_objects(metadata, plate)
        self.assertEqual([o["label"] for o in objects], ["Street Sign", "Police Constable"])
        self.assertEqual(objects[0]["names"], ["sign1", "sign2"])
        self.assertEqual(objects[1]["box"], [374, 216, 45, 156])
        self.assertEqual(brief.default_query(objects[1]), "person")
        self.assertEqual(brief.keyword_flags("Scotland Yard Street Sign"), (True, False))

    def test_parse_reply_tolerates_prose(self):
        self.assertEqual(brief.parse_reply('Sure:\n{"brief": "x", "objects": []}\nDone')["brief"], "x")


class ImagegenTest(unittest.TestCase):
    def test_gen_size_keeps_aspect_in_multiples_of_16(self):
        self.assertEqual(imagegen.gen_size(640, 480), (1152, 864))
        w, h = imagegen.gen_size(1280, 480)
        self.assertEqual((w % 16, h % 16), (0, 0))
        self.assertAlmostEqual(w / h, 1280 / 480, delta=0.05)

    def test_unavailable_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remaster.toml"
            path.write_text('[image.backends.gone]\ntool = "mflux-generate-flux2-edit"\n'
                            'model = "nobody/not-a-real-repo"\nbase_model = "x"\n'
                            '[image.backends.web]\ntool = "http"\nurl = "http://127.0.0.1:9/v1/images/generations"\n'
                            'model = "m"\napi_key_env = "REMASTER_TEST_NO_SUCH_KEY"\n'
                            f'api_key_file = "{tmp}/missing"\n')
            cfg = load_config(path)
            reason = imagegen.availability(cfg, "gone")
            self.assertTrue(reason and ("not in the Hugging Face cache" in reason or "not installed" in reason))
            self.assertIn("no API key", imagegen.availability(cfg, "web"))
            self.assertIn("no [image.backends", imagegen.availability(cfg, "missing"))
            with self.assertRaises(imagegen.Unavailable):
                imagegen.generate(cfg, "web", prompt="p", images=[], size=(64, 64), seed=1, out=Path(tmp) / "o.png")


class LocalSwitchTest(unittest.TestCase):
    def test_local_generation_off_makes_mflux_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remaster.toml"
            path.write_text('[image]\nlocal_generation = false\n[image.backends.local]\n'
                            'tool = "mflux-generate-flux2-edit"\nmodel = "a/b"\nbase_model = "x"\n')
            self.assertIn("local generation is off", imagegen.availability(load_config(path), "local"))

    def test_cancel_drops_unfinished_matching_jobs(self):
        from remaster.jobs import JobQueue, JobSpec

        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(Path(tmp) / "jobs.sqlite")
            for key in ("scene/001/local/L0", "scene/001/remote/L0", "scene/002/local/L1"):
                queue.enqueue(JobSpec("redraw-bg", key))
            done = queue.claim_next()
            queue.complete(done.id)
            self.assertEqual(queue.cancel("/local/", "remote only"), ["redraw-bg:scene/002/local/L1"])
            self.assertEqual(queue.get("redraw-bg:scene/002/local/L1").status, "failed")
            self.assertEqual(queue.get("redraw-bg:scene/001/local/L0").status, "done")
            self.assertEqual(queue.claim_next().id, "redraw-bg:scene/001/remote/L0")
            queue.close()


def sidecar_with(anchors, warp=None):
    warp = warp or GridWarp.identity(640, 480, (1.6, 1.6))
    return {"warp": warp.to_json(), "anchors": anchors}


def anchor(ident, box, det, score=0.4):
    return {"id": ident, "label": ident, "query": ident, "box": box, "detectable": True, "orig_score": score,
            "orig_det": box, "redraw_det": det, "redraw_score": 0.3 if det else 0.0}


class QaGateTest(unittest.TestCase):
    def test_objects_found_missing_and_order(self):
        anchors = [anchor("door", [100, 100, 50, 100], [102, 101, 50, 100]),
                   anchor("lamp", [300, 100, 20, 120], [301, 99, 20, 120]),
                   anchor("cart", [500, 300, 100, 100], None),
                   anchor("crate", [520, 50, 30, 30], None, score=0.1)]
        result = qa.object_gates(sidecar_with(anchors), confident=0.2)
        self.assertEqual(result["found"], 2)
        self.assertEqual(result["missing_confident"], ["cart"])
        self.assertEqual(result["missing_weak"], ["crate"])
        self.assertFalse(result["objects_ok"])
        self.assertTrue(result["order_ok"])

    def test_swapped_people_fail_depth_order_but_walls_do_not(self):
        # two people, one 12 px further forward; each is found near where expected, but swapped in depth
        anchors = [anchor("vendor", [100, 200, 40, 100], [100, 212, 40, 100]),
                   anchor("constable", [120, 212, 40, 100], [120, 200, 40, 100])]
        warp = GridWarp.identity(640, 480, (1, 1))
        result = qa.object_gates(sidecar_with(anchors, warp), 0.2)
        self.assertEqual(result["found"], 2)
        self.assertFalse(result["order_ok"])
        self.assertIn("vendor / constable front-back", result["order_inversions"])
        for a in anchors:  # a window and a door have no depth order
            a["standing"] = False
        self.assertTrue(qa.object_gates(sidecar_with(anchors, warp), 0.2)["order_ok"])

    def test_palette_score_prefers_kept_colours(self):
        from PIL import Image

        rng = np.random.default_rng(0)
        img = Image.fromarray((rng.random((60, 80, 3)) * [255, 120, 60]).astype(np.uint8))
        grey = img.convert("L").convert("RGB")
        same = qa.palette_scores(img, img)
        washed = qa.palette_scores(img, grey)
        self.assertGreater(same["palette_score"], washed["palette_score"])
        self.assertLess(washed["chroma_ratio"], 0.5)


class GeometryHelperTest(unittest.TestCase):
    def test_redraw_detection_follows_global_motion(self):
        anchors = [{"id": "o1", "label": "door", "query": "door", "box": [100, 100, 40, 80], "detectable": True,
                    "orig_score": 0.3, "orig_det": [100, 100, 40, 80]}]
        dets = [{"query": 0, "score": 0.5, "box": [400, 100, 40, 80]},   # a different door far away
                {"query": 0, "score": 0.3, "box": [112, 102, 40, 80]}]   # the moved one
        h = np.array([[1, 0, 10], [0, 1, 2], [0, 0, 1]], float)
        geometry.pick_redraw_detections(anchors, dets, ["door"], h, (640, 480))
        self.assertEqual(anchors[0]["redraw_det"], [112, 102, 40, 80])
        src, dst = geometry.anchor_points(anchors)
        self.assertEqual(src.shape, (5, 2))
        np.testing.assert_allclose(dst[0] - src[0], [12, 2])

    def test_resegment_snaps_to_the_object(self):
        img = np.full((120, 160, 3), 40, np.uint8)
        img[30:90, 50:110] = (220, 200, 60)        # the object in the redraw
        seed = np.zeros((120, 160), np.uint8)
        seed[34:94, 46:106] = 255                  # warped original mask, a few px off
        alpha = geometry.resegment(img, seed, 1.0)
        ys, xs = np.nonzero(alpha > 0.5)
        self.assertLessEqual(abs(xs.mean() - 79.5), 1.5)
        self.assertLessEqual(abs(ys.mean() - 59.5), 1.5)


class SpriteScoreTest(unittest.TestCase):
    def test_flicker_measures_change_inside_the_figure(self):
        a = np.zeros((10, 10, 3), np.uint8)
        b = a.copy()
        b[2:8, 2:8] = 30
        m = np.zeros((10, 10), np.float32)
        m[2:8, 2:8] = 1
        seq = sprite_test.sequence_scores([a, b, a], [m, m, m])
        self.assertEqual(seq["deltas"], [30.0, 30.0])

    def test_fit_canvas_places_figure_bottom_centre(self):
        from PIL import Image

        frame = Image.new("RGBA", (40, 100), (255, 0, 0, 255))
        canvas, (x, y, w, h) = sprite_test.fit_canvas(frame, (384, 768))
        self.assertEqual(canvas.size, (384, 768))
        self.assertAlmostEqual(h, 0.85 * 768, delta=1)
        self.assertEqual(canvas.getpixel((x + w // 2, y + h // 2)), (255, 0, 0))


class PickTest(unittest.TestCase):
    def rows(self):
        def row(scene, backend, level, ok, score, seconds=10.0):
            return {"scene": scene, "backend": backend, "level": level, "status": "ok", "reason": None,
                    "seconds": seconds, "peak_memory_gb": None, "pass": ok, "score": score, "qa": {}, "geometry": {}}
        return [row(1, "a", "L0", True, 0.7), row(1, "a", "L1", True, 0.72), row(1, "a", "L2", False, 0.8),
                row(2, "a", "L0", False, 0.5), row(2, "a", "L1", False, 0.6), row(2, "a", "L2", False, 0.4),
                row(1, "b", "L0", True, 0.9), row(1, "b", "L1", False, 0.9), row(1, "b", "L2", False, 0.9),
                row(2, "b", "L0", False, 0.9), row(2, "b", "L1", False, 0.9), row(2, "b", "L2", False, 0.9),
                {"scene": 1, "backend": "c", "level": "L0", "status": "unavailable", "reason": "no key",
                 "seconds": None, "peak_memory_gb": None, "pass": False, "score": None, "qa": {}, "geometry": {}}]

    def test_backend_by_passes_then_levels_per_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remaster.toml"
            path.write_text("[golden]\nscenes = [1, 2]\n")
            cfg = load_config(path)
            rows = self.rows()
            aggs = bakeoff.aggregate(rows)
            self.assertEqual(aggs["c"]["reasons"], ["no key"])
            picks = bakeoff.pick(cfg, rows, aggs)
            self.assertEqual(picks["backend"], "a")  # 2 passes beats 1, despite b's higher scores
            self.assertEqual(picks["levels"][1]["level"], "L1")
            self.assertEqual(picks["levels"][2]["level"], "baseline")

    def test_sprite_pick_falls_back_to_sr(self):
        rows = [{"character": "holmes", "backend": "x", "status": "ok", "pass": False, "flicker_ratio": 3.0},
                {"character": "watson", "backend": "x", "status": "ok", "pass": False, "flicker_ratio": 2.0}]
        self.assertEqual(bakeoff.pick_sprites(rows)["route"], "sr-baseline")
        for r in rows:
            r["pass"] = True
        self.assertEqual(bakeoff.pick_sprites(rows), {"route": "identity-conditioned", "backend": "x",
                                                      "why": "passed flicker, drift and pose gates"})


if __name__ == "__main__":
    unittest.main()
