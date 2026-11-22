import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_rosetattoo_sprite_contours as audit


class TopologyTests(unittest.TestCase):
    def test_foreground_is_eight_connected(self):
        mask = np.array([[1, 0], [0, 1]], dtype=bool)
        self.assertEqual(audit.topology(mask), {"components_8": 1, "holes_4": 0})

    def test_background_is_four_connected_and_padding_is_excluded(self):
        diagonal_gap = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 0]], dtype=bool)
        self.assertEqual(audit.topology(diagonal_gap), {"components_8": 1, "holes_4": 1})
        diagonal_gap[0, 1] = 0
        self.assertEqual(audit.topology(diagonal_gap), {"components_8": 1, "holes_4": 0})
        for mask, expected in ((np.zeros((2, 3), bool), 0), (np.ones((2, 3), bool), 1)):
            self.assertEqual(audit.topology(mask), {"components_8": expected, "holes_4": 0})

    def test_one_pixel_line_split_is_suspect_even_when_support_is_preserved(self):
        old = np.zeros((3, 5, 4), dtype=np.uint8)
        old[1, 1:4, 3] = 255
        new = old.copy()
        new[1, 2, 3] = 1
        metrics, issues = audit.compare_pixels(old, new)
        self.assertEqual(metrics["support_changed_pixels"], 0)
        self.assertEqual(metrics["threshold_128_removed_pixels"], 1)
        self.assertEqual(metrics["threshold_128_topology_before"]["components_8"], 1)
        self.assertEqual(metrics["threshold_128_topology_after"]["components_8"], 2)
        self.assertEqual(metrics["alpha_weighted_area_delta"], -254 / 255)
        self.assertIn("threshold_128_topology_changed", issues)

    def test_hole_closed_without_support_change(self):
        old = np.zeros((5, 5, 4), dtype=np.uint8)
        old[1:4, 1:4, 3] = 255
        old[2, 2, 3] = 0
        old[1, 2, 3] = 127
        new = old.copy()
        new[1, 2, 3] = 128
        metrics, issues = audit.compare_pixels(old, new)
        self.assertEqual(metrics["support_changed_pixels"], 0)
        self.assertEqual(metrics["threshold_128_added_pixels"], 1)
        self.assertEqual(metrics["threshold_128_topology_before"]["holes_4"], 0)
        self.assertEqual(metrics["threshold_128_topology_after"]["holes_4"], 1)
        self.assertIn("threshold_128_topology_changed", issues)

    def test_rgb_under_transparency_and_zero_denominator(self):
        old = np.zeros((2, 2, 4), dtype=np.uint8)
        new = old.copy()
        new[0, 0, 0] = 1
        metrics, issues = audit.compare_pixels(old, new)
        self.assertEqual(metrics["rgb_changed_pixels"], 1)
        self.assertEqual(metrics["support_changed_pixels"], 0)
        self.assertIsNone(metrics["alpha_weighted_area_delta_percent"])
        self.assertIn("rgb_changed", issues)

    def test_crossing_is_suspect_even_when_topology_counts_are_unchanged(self):
        old = np.zeros((1, 2, 4), dtype=np.uint8)
        old[0, :, 3] = [255, 127]
        new = old.copy()
        new[0, 1, 3] = 128
        metrics, issues = audit.compare_pixels(old, new)
        self.assertEqual(metrics["threshold_128_topology_before"], metrics["threshold_128_topology_after"])
        self.assertIn("threshold_128_crossing", issues)
        self.assertNotIn("threshold_128_topology_changed", issues)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / "base"
        self.candidate = self.root / "candidate"
        self.source = self.root / "source"
        self.resource = Path("deep/variant/resource")
        for root in (self.base, self.candidate, self.source):
            (root / self.resource).mkdir(parents=True)
        source_image = Image.new("RGBA", (3, 3), (10, 20, 30, 0))
        source_image.putpixel((1, 1), (10, 20, 30, 255))
        source_name = "sprite-from-arbitrary-source-name.png"
        source_image.save(self.source / self.resource / source_name)
        digest = audit.source_digest(source_image)
        image_name = "arbitrary-enhanced-name.png"
        self.save_tagged(self.base / self.resource / image_name, source_image, digest)
        baseline_digest = hashlib.sha256((self.base / self.resource / image_name).read_bytes()).hexdigest()
        self.save_tagged(self.candidate / self.resource / image_name, source_image, digest, baseline_digest)
        source_record = {"index": 7, "file": source_name, "width": 3, "height": 3,
                         "offset_x": -2, "offset_y": 4, "encoding": "rle"}
        enhanced_record = {**source_record, "file": image_name}
        common = {"source_filename": "source-specific-name", "frame_count": 1, "sidecars": ["palette.bin"]}
        self.write_meta(self.source, {**common, "frames": [source_record]})
        enhanced_meta = {**common, "frames": [enhanced_record], "scale": 1, "complete": True}
        self.write_meta(self.base, enhanced_meta)
        self.write_meta(self.candidate, {**enhanced_meta, "alpha_refinement": {"preserve_support": True}})
        for root in (self.source, self.base, self.candidate):
            (root / self.resource / "palette.bin").write_bytes(b"palette")

    @staticmethod
    def save_tagged(path, image, digest, input_digest=None):
        info = PngImagePlugin.PngInfo()
        if digest is not None:
            info.add_text("source_sha256", digest)
        if input_digest is not None:
            info.add_text("alpha_input_sha256", input_digest)
        image.save(path, pnginfo=info)

    def write_meta(self, root, metadata):
        (root / self.resource / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    def read_meta(self, root):
        return json.loads((root / self.resource / "metadata.json").read_text(encoding="utf-8"))

    def run_audit(self, source=True):
        return audit.audit(self.base, self.candidate, self.source if source else None)

    def test_valid_arbitrary_source_filename_and_unverified_mode(self):
        report = self.run_audit()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["file_hashes_version"], 1)
        self.assertEqual(report["summary"]["frames_compared"], 1)
        self.assertEqual(report["summary"]["suspect_resources"], 0)
        self.assertEqual(report["resources"][0]["frames"][0]["source_file"], "sprite-from-arbitrary-source-name.png")
        self.assertEqual(report["source_verification"], "decoded_rgba")
        without_source = self.run_audit(False)
        self.assertEqual(without_source["source_verification"], "matching_png_tags_only")
        self.assertNotIn("source", without_source["resources"][0]["file_sha256"])

    def test_exact_hashes_and_changes_after_the_audit(self):
        resource = self.run_audit()["resources"][0]
        frame = resource["frames"][0]
        for label, root in (("baseline", self.base), ("candidate", self.candidate), ("source", self.source)):
            directory = root / self.resource
            image_name = frame[f"{label}_file"]
            for path, digest in (
                (directory / image_name, frame[f"{label}_png_sha256"]),
                (directory / "metadata.json", resource["file_sha256"][label]["metadata"]),
                (directory / "palette.bin", resource["file_sha256"][label]["sidecars"]["palette.bin"]),
            ):
                with self.subTest(path=path):
                    self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
                    # Append-only changes exercise exact file bytes, not just decoded pixels or JSON.
                    path.write_bytes(path.read_bytes() + b" ")
                    self.assertNotEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        updated = self.run_audit()["resources"][0]
        for label in ("baseline", "candidate", "source"):
            self.assertNotEqual(frame[f"{label}_png_sha256"], updated["frames"][0][f"{label}_png_sha256"])
            self.assertNotEqual(resource["file_sha256"][label]["metadata"],
                                updated["file_sha256"][label]["metadata"])
            self.assertNotEqual(resource["file_sha256"][label]["sidecars"]["palette.bin"],
                                updated["file_sha256"][label]["sidecars"]["palette.bin"])

    def test_missing_files_omit_only_their_hashes_and_report_issues(self):
        (self.candidate / self.resource / "arbitrary-enhanced-name.png").unlink()
        (self.source / self.resource / "palette.bin").unlink()
        resource = self.run_audit()["resources"][0]
        frame = resource["frames"][0]
        self.assertIn("baseline_png_sha256", frame)
        self.assertNotIn("candidate_png_sha256", frame)
        self.assertTrue(any("candidate: cannot read frame" in issue for issue in frame["issues"]))
        self.assertNotIn("palette.bin", resource["file_sha256"]["source"]["sidecars"])
        self.assertTrue(any("source: sidecar 'palette.bin'" in issue for issue in resource["issues"]))
        (self.base / self.resource / "metadata.json").unlink()
        resource = self.run_audit()["resources"][0]
        self.assertNotIn("metadata", resource["file_sha256"]["baseline"])
        self.assertIn("metadata", resource["file_sha256"]["candidate"])
        self.assertIn("baseline: resource metadata missing", resource["issues"])

    def test_unpaired_present_png_is_still_hashed(self):
        meta = self.read_meta(self.candidate)
        meta["frames"] = []
        meta["frame_count"] = 0
        self.write_meta(self.candidate, meta)
        frame = self.run_audit()["resources"][0]["frames"][0]
        baseline = self.base / self.resource / "arbitrary-enhanced-name.png"
        self.assertEqual(frame["baseline_png_sha256"], hashlib.sha256(baseline.read_bytes()).hexdigest())
        self.assertNotIn("candidate_png_sha256", frame)
        self.assertIn("frame missing from baseline or candidate metadata", frame["issues"])

    def test_hash_failure_is_reported_without_a_fabricated_digest(self):
        target = (self.candidate / self.resource / "arbitrary-enhanced-name.png").read_bytes()
        sha256 = hashlib.sha256

        def sometimes_fails(data=b""):
            if data == target:
                raise ValueError("simulated hashing failure")
            return sha256(data)

        with patch.object(audit.hashlib, "sha256", side_effect=sometimes_fails):
            resource = self.run_audit()["resources"][0]
        frame = resource["frames"][0]
        self.assertNotIn("candidate_png_sha256", frame)
        self.assertTrue(any("candidate: frame: cannot hash file" in issue for issue in frame["issues"]))
        self.assertEqual(resource["frames_compared"], 1)
        self.assertTrue(resource["suspect"])

    def test_metadata_sidecar_and_source_hash_failures_are_reported(self):
        candidate_meta = (self.candidate / self.resource / "metadata.json").read_bytes()
        source_png = (self.source / self.resource / "sprite-from-arbitrary-source-name.png").read_bytes()
        sha256 = hashlib.sha256

        def sometimes_fails(data=b""):
            if data in (candidate_meta, source_png, b"palette"):
                raise OSError("simulated hashing failure")
            return sha256(data)

        with patch.object(audit.hashlib, "sha256", side_effect=sometimes_fails):
            resource = self.run_audit()["resources"][0]
        self.assertNotIn("metadata", resource["file_sha256"]["candidate"])
        self.assertNotIn("palette.bin", resource["file_sha256"]["source"]["sidecars"])
        self.assertNotIn("source_png_sha256", resource["frames"][0])
        self.assertTrue(any("candidate: metadata: cannot hash file" in issue for issue in resource["issues"]))
        self.assertTrue(any("source: sidecar 'palette.bin': cannot hash file" in issue for issue in resource["issues"]))
        self.assertTrue(any("source: frame: cannot hash file" in issue for issue in resource["frames"][0]["issues"]))

    def test_readable_but_invalid_files_still_have_raw_hashes(self):
        png = self.candidate / self.resource / "arbitrary-enhanced-name.png"
        png.write_bytes(b"not a PNG")
        metadata = self.source / self.resource / "metadata.json"
        metadata.write_bytes(b"not JSON")
        resource = self.run_audit()["resources"][0]
        self.assertEqual(resource["frames"][0]["candidate_png_sha256"], hashlib.sha256(png.read_bytes()).hexdigest())
        self.assertEqual(resource["file_sha256"]["source"]["metadata"], hashlib.sha256(metadata.read_bytes()).hexdigest())
        self.assertTrue(any("cannot read frame" in issue for issue in resource["frames"][0]["issues"]))
        self.assertTrue(any("source: cannot read metadata" in issue for issue in resource["issues"]))

    def test_scaled_source_dimensions_and_offsets_can_match(self):
        image_name = "arbitrary-enhanced-name.png"
        for root in (self.base, self.candidate):
            path = root / self.resource / image_name
            with Image.open(path) as old:
                image = old.resize((6, 6), Image.Resampling.NEAREST)
                digest = old.info["source_sha256"]
            input_digest = hashlib.sha256((self.base / self.resource / image_name).read_bytes()).hexdigest()
            self.save_tagged(path, image, digest, input_digest if root == self.candidate else None)
            meta = self.read_meta(root)
            meta["scale"] = 2
            for key in audit.GEOMETRY:
                meta["frames"][0][key] *= 2
            self.write_meta(root, meta)
        self.assertEqual(self.run_audit()["summary"]["suspect_resources"], 0)

    def test_source_hash_checks_decoded_pixels_not_filename_or_compressed_bytes(self):
        path = self.source / self.resource / "sprite-from-arbitrary-source-name.png"
        image = Image.open(path).convert("RGBA")
        image.putpixel((0, 0), (90, 20, 30, 0))
        image.save(path)
        frame = self.run_audit()["resources"][0]["frames"][0]
        self.assertIn("source: decoded RGBA digest differs from PNG source_sha256", frame["issues"])

    def test_missing_and_invalid_tags_and_input_digest(self):
        path = self.candidate / self.resource / "arbitrary-enhanced-name.png"
        image = Image.open(path).convert("RGBA")
        self.save_tagged(path, image, None, "wrong")
        issues = self.run_audit()["resources"][0]["frames"][0]["issues"]
        self.assertIn("candidate: missing or invalid source_sha256", issues)
        self.assertIn("baseline/candidate source_sha256 differs", issues)
        self.assertIn("candidate: alpha_input_sha256 differs from baseline file", issues)

    def test_support_and_rgb_changes_flag_the_entire_resource(self):
        path = self.candidate / self.resource / "arbitrary-enhanced-name.png"
        with Image.open(path) as old:
            image = old.copy()
            digest = old.info["source_sha256"]
            baseline_digest = old.info["alpha_input_sha256"]
        image.putpixel((0, 0), (11, 20, 30, 1))
        self.save_tagged(path, image, digest, baseline_digest)
        resource = self.run_audit()["resources"][0]
        self.assertTrue(resource["suspect"])
        frame = resource["frames"][0]
        self.assertEqual(frame["rgb_changed_pixels"], 1)
        self.assertEqual(frame["support_changed_pixels"], 1)
        self.assertIn("rgb_changed", frame["issues"])
        self.assertIn("nonzero_alpha_support_changed", frame["issues"])
        self.assertEqual(resource["metrics"]["rgb_changed_frames"], 1)
        self.assertEqual(resource["metrics"]["support_changed_frames"], 1)

    def test_palette_differences_missing_and_undeclared_are_reported(self):
        (self.candidate / self.resource / "palette.bin").write_bytes(b"bad")
        self.assertIn("baseline/candidate sidecars differ", self.run_audit()["resources"][0]["issues"])
        (self.candidate / self.resource / "palette.bin").unlink()
        self.assertTrue(any("sidecar 'palette.bin'" in i for i in self.run_audit()["resources"][0]["issues"]))
        (self.candidate / self.resource / "palette.bin").write_bytes(b"palette")
        meta = self.read_meta(self.candidate)
        meta["sidecars"] = []
        self.write_meta(self.candidate, meta)
        self.assertIn("candidate: palette.bin is not declared as a sidecar", self.run_audit()["resources"][0]["issues"])

    def test_unreferenced_source_frame_is_reported_in_the_resource(self):
        Image.new("RGBA", (1, 1)).save(self.source / self.resource / "unlisted.png")
        resource = self.run_audit()["resources"][0]
        self.assertIn("source: unreferenced PNG unlisted.png", resource["issues"])
        self.assertTrue(resource["suspect"])

    def test_geometry_order_duplicate_indices_and_orphans(self):
        meta = self.read_meta(self.candidate)
        meta["frames"][0]["offset_x"] += 1
        meta["frames"].append(dict(meta["frames"][0]))
        self.write_meta(self.candidate, meta)
        Image.new("RGBA", (1, 1)).save(self.candidate / self.resource / "orphan.png")
        resource = self.run_audit()["resources"][0]
        self.assertIn("candidate: frame_count does not match frames", resource["issues"])
        self.assertIn("candidate: duplicate frame index 7", resource["issues"])
        self.assertIn("candidate: unreferenced PNG orphan.png", resource["issues"])
        self.assertIn("baseline/candidate frame metadata differs: offset_x", resource["frames"][0]["issues"])
        self.assertEqual(resource["suspect_frame_indices"], [7])

    def test_missing_extra_and_unreadable_frames_are_not_silently_skipped(self):
        (self.candidate / self.resource / "arbitrary-enhanced-name.png").write_bytes(b"not a PNG")
        resource = self.run_audit()["resources"][0]
        self.assertEqual(resource["frames_compared"], 0)
        self.assertTrue(any("cannot read frame" in i for i in resource["frames"][0]["issues"]))
        meta = self.read_meta(self.candidate)
        meta["frames"] = []
        meta["frame_count"] = 0
        self.write_meta(self.candidate, meta)
        resource = self.run_audit()["resources"][0]
        self.assertIn("frame missing from baseline or candidate metadata", resource["frames"][0]["issues"])
        self.assertTrue(resource["suspect"])

    def test_scaled_source_geometry_and_dimensions_are_checked(self):
        meta = self.read_meta(self.source)
        meta["frames"][0]["offset_x"] = -1
        meta["frames"][0]["width"] = 2
        self.write_meta(self.source, meta)
        issues = self.run_audit()["resources"][0]["frames"][0]["issues"]
        self.assertIn("source/baseline scaled geometry differs: offset_x", issues)
        self.assertIn("source/baseline scaled geometry differs: width", issues)
        self.assertIn("source: image dimensions differ from metadata", issues)

    def test_unsafe_frame_path_and_missing_source_manifest(self):
        meta = self.read_meta(self.candidate)
        meta["frames"][0]["file"] = "../../outside.png"
        self.write_meta(self.candidate, meta)
        frame = self.run_audit()["resources"][0]["frames"][0]
        self.assertTrue(any("unsafe file name" in i for i in frame["issues"]))
        (self.source / self.resource / "metadata.json").unlink()
        resource = self.run_audit()["resources"][0]
        self.assertTrue(any("source: cannot read metadata" in i for i in resource["issues"]))

    def test_source_resource_symlink_is_not_accepted(self):
        resource_dir = self.source / self.resource
        moved = self.root / "moved-source"
        resource_dir.rename(moved)
        resource_dir.symlink_to(moved, target_is_directory=True)
        resource = self.run_audit()["resources"][0]
        self.assertTrue(any("directory symlink not accepted" in i for i in resource["issues"]))
        self.assertIn("frame missing from source metadata", resource["frames"][0]["issues"])

    def test_resource_metadata_mismatch_and_missing_resource_are_reported(self):
        meta = self.read_meta(self.candidate)
        meta["source_filename"] = "different-source"
        self.write_meta(self.candidate, meta)
        report = self.run_audit()
        self.assertIn("baseline/candidate metadata differs: source_filename", report["resources"][0]["issues"])
        (self.candidate / self.resource / "metadata.json").unlink()
        report = self.run_audit()
        self.assertIn("candidate: resource metadata missing", report["resources"][0]["issues"])
        self.assertTrue(report["resources"][0]["suspect"])

    def test_no_manifest_or_empty_manifest_cannot_pass(self):
        meta = self.read_meta(self.base)
        meta["frames"] = []
        meta["frame_count"] = 0
        self.write_meta(self.base, meta)
        self.assertIn("baseline: frame list is empty", self.run_audit()["resources"][0]["issues"])
        empty_a, empty_b = self.root / "empty-a", self.root / "empty-b"
        empty_a.mkdir()
        empty_b.mkdir()
        self.assertIn("no frame resources found", audit.audit(empty_a, empty_b, None)["errors"])

    def test_report_is_exclusive_and_never_inside_input_tree(self):
        existing = self.root / "existing.json"
        existing.write_text("keep", encoding="utf-8")
        args = ["--baseline", str(self.base), "--candidate", str(self.candidate)]
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                audit.main(args + ["--report", str(existing)])
            with self.assertRaises(SystemExit):
                audit.main(args + ["--report", str(self.base / "new.json")])
        self.assertEqual(existing.read_text(), "keep")
        self.assertFalse((self.base / "new.json").exists())
        link = self.root / "existing-link.json"
        link.symlink_to(existing)
        dangling = self.root / "dangling-link.json"
        dangling.symlink_to(self.root / "not-yet-created.json")
        input_alias = self.root / "input-alias"
        input_alias.symlink_to(self.base, target_is_directory=True)
        with contextlib.redirect_stderr(io.StringIO()):
            for path in (link, dangling, input_alias / "new.json"):
                with self.assertRaises(SystemExit):
                    audit.main(args + ["--report", str(path)])
        self.assertEqual(existing.read_text(), "keep")
        self.assertTrue(dangling.is_symlink())
        self.assertFalse((self.root / "not-yet-created.json").exists())
        report = self.root / "new.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(audit.main(args + ["--report", str(report)]), 0)
        self.assertEqual(json.loads(report.read_text())["summary"]["suspect_resources"], 0)

    def test_report_is_published_atomically_and_races_do_not_overwrite(self):
        args = ["--baseline", str(self.base), "--candidate", str(self.candidate)]
        report = self.root / "atomic.json"
        with patch.object(audit, "audit", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                audit.main(args + ["--report", str(report)])
        self.assertFalse(report.exists())
        self.assertEqual(list(self.root.glob(".sprite-contour-audit-*.tmp")), [])

        original_audit = audit.audit

        def competing_writer(*roots):
            value = original_audit(*roots)
            report.write_text("keep", encoding="utf-8")
            return value

        with patch.object(audit, "audit", side_effect=competing_writer), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                audit.main(args + ["--report", str(report)])
        self.assertEqual(report.read_text(), "keep")
        self.assertEqual(list(self.root.glob(".sprite-contour-audit-*.tmp")), [])

    def test_report_parent_symlink_retarget_cannot_write_into_input(self):
        output_dir = self.root / "output"
        output_dir.mkdir()
        alias = self.root / "output-alias"
        alias.symlink_to(output_dir, target_is_directory=True)
        args = ["--baseline", str(self.base), "--candidate", str(self.candidate),
                "--report", str(alias / "result.json")]
        original_audit = audit.audit

        def retarget(*roots):
            value = original_audit(*roots)
            alias.unlink()
            alias.symlink_to(self.base, target_is_directory=True)
            return value

        with patch.object(audit, "audit", side_effect=retarget), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(audit.main(args), 0)
        self.assertTrue((output_dir / "result.json").is_file())
        self.assertFalse((self.base / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
