import contextlib
import copy
import hashlib
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_rosetattoo_contour_plan as validator


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def png(width, height, color):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\0" + bytes(color) * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class SelectionPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.roots = {label: self.root / label for label in validator.LABELS}
        for root in self.roots.values():
            root.mkdir()
        self.report_path = self.root / "audit.json"
        self.report = {"schema_version": 1, "file_hashes_version": 1,
                       "inputs": {name: str(root) for name, root in self.roots.items()},
                       "source_verification": "decoded_rgba", "errors": [], "resources": []}
        self.add_resource("scene_044/canim_004", (8, 9), topology_index=9)
        self.add_resource("scene_045/canim_001", (0, 1))
        self.refresh_summary()
        self.write_report()

    def add_resource(self, relative, indices, topology_index=None):
        all_records = {}
        for label, root in self.roots.items():
            directory = root / relative
            directory.mkdir(parents=True)
            records = []
            for index in indices:
                is_source = label == "source"
                name = f"src_{index:03d}.png" if is_source else f"frame_{index:03d}@2x.png"
                size = 1 if is_source else 2
                (directory / name).write_bytes(png(size, size, (20, 40, 60, 255)))
                records.append({"index": index, "file": name, "width": size, "height": size,
                                "offset_x": 0, "offset_y": 0, "encoding": "rle"})
            resource_md5 = hashlib.md5(relative.encode()).hexdigest()
            meta = {"resource_name": relative, "resource_md5": resource_md5,
                    "source_archives": [{"archive": "VGS.LIB", "resource": relative}],
                    "sidecars": ["palette.bin"], "frame_count": len(records), "frames": records}
            if label != "source":
                meta.update(scale=2, complete=True, upscaler="model")
            if label == "candidate":
                meta["alpha_refinement"] = {"method": "native-alpha-scale4-box", "scale": 2,
                                            "radius": 0.35, "thin_guard": True, "preserve_support": True}
            (directory / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            (directory / "palette.bin").write_bytes(b"palette")
            all_records[label] = records
        frames = []
        for position, index in enumerate(indices):
            changed = index == topology_index
            old_sum, new_sum = 510, 511
            frame = {"index": index, "issues": ["threshold_128_crossing"] +
                     (["threshold_128_topology_changed"] if changed else []),
                     "rgb_changed_pixels": 0, "support_changed_pixels": 0,
                     "alpha_sum_before": old_sum, "alpha_sum_after": new_sum,
                     "alpha_weighted_area_before": old_sum / 255, "alpha_weighted_area_after": new_sum / 255,
                     "alpha_weighted_area_delta": (new_sum - old_sum) / 255,
                     "alpha_weighted_area_delta_percent": 100 / old_sum,
                     "threshold_128_added_pixels": 1, "threshold_128_removed_pixels": 0,
                     "threshold_128_topology_before": {"components_8": 1, "holes_4": 0},
                     "threshold_128_topology_after": {"components_8": 1, "holes_4": int(changed)}}
            for label, root in self.roots.items():
                name = all_records[label][position]["file"]
                frame[f"{label}_file"] = name
                frame[f"{label}_png_sha256"] = sha(root / relative / name)
            frames.append(frame)
        count = len(frames)
        hashes = {label: {"metadata": sha(root / relative / "metadata.json"),
                          "sidecars": {"palette.bin": sha(root / relative / "palette.bin")}}
                  for label, root in self.roots.items()}
        entry = {"resource": relative, "issues": [], "file_sha256": hashes, "frames": frames,
                 "frames_compared": count, "suspect_frame_indices": list(indices), "suspect": True,
                 "metrics": {"rgb_changed_frames": 0, "support_changed_frames": 0,
                             "threshold_128_crossing_frames": count,
                             "threshold_128_topology_changed_frames": int(topology_index is not None),
                             "threshold_128_added_pixels": count, "threshold_128_removed_pixels": 0,
                             "alpha_weighted_area_delta": count / 255,
                             "alpha_weighted_area_delta_percent": 100 / 510,
                             "max_absolute_frame_area_delta_percent": 100 / 510}}
        self.report["resources"].append(entry)

    def refresh_summary(self):
        resources = self.report["resources"]
        self.report["summary"] = {"resources": len(resources),
                                  "suspect_resources": sum(r["suspect"] for r in resources),
                                  "frames_compared": sum(r["frames_compared"] for r in resources),
                                  "suspect_frames": sum(len(r["suspect_frame_indices"]) for r in resources),
                                  "global_errors": 0}

    def write_report(self):
        self.report_path.write_text(json.dumps(self.report), encoding="utf-8")

    def validate(self):
        return validator.validate_audit(self.report_path, *(self.roots[label] for label in validator.LABELS))

    def assert_rejected(self, reason=None):
        self.write_report()
        with self.assertRaises(validator.ValidationError) as caught:
            self.validate()
        if reason:
            self.assertIn(reason, str(caught.exception))

    def test_topology_change_falls_back_entire_resource_only(self):
        result = self.validate()
        self.assertEqual(result.fallback_resources, ("scene_044/canim_004",))
        self.assertEqual(result.baseline_root, self.roots["baseline"])
        self.assertEqual(result.candidate_root, self.roots["candidate"])
        self.assertEqual(result.source_root, self.roots["source"])
        self.assertEqual(result.audit_sha256, sha(self.report_path))

    def test_only_crossings_keep_candidate(self):
        frame = self.report["resources"][0]["frames"][1]
        frame["issues"].remove("threshold_128_topology_changed")
        frame["threshold_128_topology_after"]["holes_4"] = 0
        self.report["resources"][0]["metrics"]["threshold_128_topology_changed_frames"] = 0
        self.write_report()
        self.assertEqual(self.validate().fallback_resources, ())

    def test_unchanged_frames_are_not_rejected_as_missing_crossings(self):
        resource = self.report["resources"][1]
        for frame in resource["frames"]:
            frame["issues"] = []
            frame["threshold_128_added_pixels"] = 0
            frame["alpha_sum_after"] = frame["alpha_sum_before"]
            frame["alpha_weighted_area_after"] = frame["alpha_weighted_area_before"]
            frame["alpha_weighted_area_delta"] = 0
            frame["alpha_weighted_area_delta_percent"] = 0
        resource.update(suspect_frame_indices=[], suspect=False)
        resource["metrics"].update(threshold_128_crossing_frames=0, threshold_128_added_pixels=0,
                                   alpha_weighted_area_delta=0, alpha_weighted_area_delta_percent=0,
                                   max_absolute_frame_area_delta_percent=0)
        self.refresh_summary()
        self.write_report()
        self.assertEqual(self.validate().fallback_resources, ("scene_044/canim_004",))

    def test_stale_png_metadata_and_sidecar_on_every_tree(self):
        relative = "scene_044/canim_004"
        frame = self.report["resources"][0]["frames"][0]
        for label, root in self.roots.items():
            for name in (frame[f"{label}_file"], "metadata.json", "palette.bin"):
                path = root / relative / name
                original = path.read_bytes()
                with self.subTest(label=label, name=name):
                    path.write_bytes(original + b" ")
                    self.assert_rejected("SHA-256")
                    path.write_bytes(original)

    def test_missing_and_extra_resources_in_each_tree(self):
        relative = "scene_044/canim_004"
        for label, root in self.roots.items():
            meta = root / relative / "metadata.json"
            contents = meta.read_bytes()
            with self.subTest(label=label, kind="missing"):
                meta.unlink()
                self.assert_rejected("manifest sets differ")
                meta.write_bytes(contents)
            with self.subTest(label=label, kind="extra"):
                extra = root / "scene_999" / "canim_999"
                shutil.copytree(root / relative, extra)
                self.assert_rejected("manifest sets differ")
                shutil.rmtree(extra.parent)

    def test_missing_extra_and_unreferenced_files_in_each_tree(self):
        for label, root in self.roots.items():
            directory = root / "scene_044/canim_004"
            frame = self.report["resources"][0]["frames"][0]
            path = directory / frame[f"{label}_file"]
            data = path.read_bytes()
            with self.subTest(label=label, kind="missing PNG"):
                path.unlink()
                self.assert_rejected("missing or orphan files")
                path.write_bytes(data)
            with self.subTest(label=label, kind="orphan PNG"):
                extra = directory / "orphan.png"
                extra.write_bytes(data)
                self.assert_rejected("missing or orphan files")
                extra.unlink()
            with self.subTest(label=label, kind="orphan sidecar"):
                extra = directory / "unknown.bin"
                extra.write_bytes(b"orphan")
                self.assert_rejected("missing or orphan files")
                extra.unlink()
            with self.subTest(label=label, kind="missing sidecar"):
                sidecar = directory / "palette.bin"
                sidecar.unlink()
                self.assert_rejected("missing or orphan files")
                sidecar.write_bytes(b"palette")

    def test_symlinks_are_rejected(self):
        directory = self.roots["candidate"] / "scene_044/canim_004"
        extra = directory / "link.png"
        extra.symlink_to(directory / "frame_008@2x.png")
        self.assert_rejected("symlink")
        extra.unlink()
        link = self.root / "aliased-source"
        link.symlink_to(self.roots["source"], target_is_directory=True)
        with self.assertRaisesRegex(validator.ValidationError, "symlink"):
            validator.validate_audit(self.report_path, self.roots["baseline"], self.roots["candidate"], link)
        directory_link = self.roots["baseline"] / "directory-link"
        directory_link.symlink_to(self.roots["candidate"], target_is_directory=True)
        self.assert_rejected("symlink")
        directory_link.unlink()
        report_link = self.root / "audit-link.json"
        report_link.symlink_to(self.report_path)
        with self.assertRaisesRegex(validator.ValidationError, "symlink"):
            validator.validate_audit(report_link, *(self.roots[label] for label in validator.LABELS))

    def test_bad_frame_report_fields_fail_closed(self):
        original = copy.deepcopy(self.report)
        mutations = (
            lambda f: f.update(issues=["rgb_changed"]),
            lambda f: f.update(rgb_changed_pixels=1),
            lambda f: f.update(support_changed_pixels=1),
            lambda f: f.update(threshold_128_added_pixels=0),
            lambda f: f.update(candidate_file="../outside.png"),
            lambda f: f.pop("source_png_sha256"),
            lambda f: f.update(candidate_png_sha256="0" * 64),
            lambda f: f.update(index=True),
            lambda f: f.update(alpha_weighted_area_delta=999),
            lambda f: f.update(issues=["threshold_128_crossing", "threshold_128_crossing"]),
            lambda f: f.update(threshold_128_topology_after={"components_8": 1, "holes_4": 1}),
            lambda f: f.pop("alpha_weighted_area_delta_percent"),
        )
        for mutation in mutations:
            self.report = copy.deepcopy(original)
            mutation(self.report["resources"][0]["frames"][0])
            with self.subTest(mutation=mutation):
                self.assert_rejected()

    def test_summary_flags_and_resource_errors_are_checked(self):
        original = copy.deepcopy(self.report)
        mutations = (
            lambda r: r["summary"].update(frames_compared=5),
            lambda r: r["summary"].update(suspect_resources=0),
            lambda r: r.update(errors=["failed"]),
            lambda r: r["resources"][0].update(issues=["missing metadata"]),
            lambda r: r["resources"][0].update(frames_compared=1),
            lambda r: r["resources"][0].update(suspect_frame_indices=[8]),
            lambda r: r["resources"][0].update(suspect_frame_indices=[True, 9]),
            lambda r: r["resources"][0].update(suspect=False),
            lambda r: r["resources"][0]["metrics"].update(threshold_128_topology_changed_frames=0),
            lambda r: r["resources"][0]["frames"].pop(),
            lambda r: r["resources"].append(copy.deepcopy(r["resources"][0])),
        )
        for mutation in mutations:
            self.report = copy.deepcopy(original)
            mutation(self.report)
            with self.subTest(mutation=mutation):
                self.assert_rejected()

    def test_unverifiable_source_older_reports_and_roots_are_rejected(self):
        original = copy.deepcopy(self.report)
        mutations = (
            lambda r: r.pop("file_hashes_version"),
            lambda r: r.update(schema_version=True),
            lambda r: r.update(source_verification="matching_png_tags_only"),
            lambda r: r["inputs"].update(source=None),
            lambda r: r["inputs"].update(candidate=str(self.roots["baseline"])),
            lambda r: r["resources"][0]["file_sha256"].pop("source"),
            lambda r: r["resources"][0]["file_sha256"]["source"]["sidecars"].clear(),
            lambda r: r["resources"][0]["file_sha256"]["source"].pop("metadata"),
            lambda r: r["resources"][0]["file_sha256"]["candidate"].update(extra="0" * 64),
        )
        for mutation in mutations:
            self.report = copy.deepcopy(original)
            mutation(self.report)
            with self.subTest(mutation=mutation):
                self.assert_rejected()

    def test_resource_path_traversal_and_bad_json_are_rejected(self):
        for name in ("../outside", "/absolute", "scene_044/../other", "scene_044//canim_004", "a\\b"):
            with self.subTest(name=name):
                self.report["resources"][0]["resource"] = name
                self.assert_rejected("resource path")
        for data in (b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'{"x":1e9999}', b"not JSON"):
            with self.subTest(data=data):
                self.report_path.write_bytes(data)
                with self.assertRaises(validator.ValidationError):
                    self.validate()

    def test_metadata_is_independently_compared_even_if_report_hash_is_replaced(self):
        relative = "scene_044/canim_004"
        path = self.roots["candidate"] / relative / "metadata.json"
        metadata = json.loads(path.read_text())
        metadata["source_filename"] = "unexpected"
        path.write_text(json.dumps(metadata), encoding="utf-8")
        self.report["resources"][0]["file_sha256"]["candidate"]["metadata"] = sha(path)
        self.assert_rejected("metadata differs")

    def test_sidecar_is_independently_compared_even_if_report_hash_is_replaced(self):
        relative = "scene_044/canim_004"
        path = self.roots["source"] / relative / "palette.bin"
        path.write_bytes(b"different palette")
        self.report["resources"][0]["file_sha256"]["source"]["sidecars"]["palette.bin"] = sha(path)
        self.assert_rejected("sidecar bytes differ")

    def test_unsafe_manifest_filename_is_rejected_even_with_matching_metadata_hashes(self):
        relative = "scene_044/canim_004"
        for label, root in self.roots.items():
            path = root / relative / "metadata.json"
            metadata = json.loads(path.read_text())
            metadata["frames"][0]["file"] = "../escape.png"
            path.write_text(json.dumps(metadata), encoding="utf-8")
            self.report["resources"][0]["file_sha256"][label]["metadata"] = sha(path)
        self.assert_rejected("unsafe filename")

    def test_json_booleans_are_not_equal_to_numbers(self):
        self.assertFalse(validator.same_json({"x": True}, {"x": 1}))
        self.assertFalse(validator.same_json([False], [0]))
        self.assertTrue(validator.same_json({"x": 1}, {"x": 1.0}))

    def test_candidate_must_be_the_selected_contour_method(self):
        relative = "scene_044/canim_004"
        path = self.roots["candidate"] / relative / "metadata.json"
        metadata = json.loads(path.read_text())
        metadata["alpha_refinement"]["preserve_support"] = False
        path.write_text(json.dumps(metadata), encoding="utf-8")
        self.report["resources"][0]["file_sha256"]["candidate"]["metadata"] = sha(path)
        self.assert_rejected("support-preserving geometric")

    def test_previously_refined_baseline_cannot_be_original_fallback(self):
        relative = "scene_044/canim_004"
        path = self.roots["baseline"] / relative / "metadata.json"
        metadata = json.loads(path.read_text())
        metadata["alpha_refinement"] = {"method": "previous-change"}
        path.write_text(json.dumps(metadata), encoding="utf-8")
        self.report["resources"][0]["file_sha256"]["baseline"]["metadata"] = sha(path)
        self.assert_rejected("baseline already has alpha refinement")

    def test_current_summaries_and_source_only_summaries(self):
        for root in self.roots.values():
            (root / "scene_044" / "metadata.json").write_text('{"scene_id":44}', encoding="utf-8")
        self.assertEqual(self.validate().fallback_resources, ("scene_044/canim_004",))
        candidate = self.roots["candidate"] / "scene_044/metadata.json"
        candidate.write_text('{"scene_id":44,"other":1}', encoding="utf-8")
        self.assert_rejected("summaries differ")
        candidate.write_text('{"scene_id":44}', encoding="utf-8")
        source = self.roots["source"] / "scene_044/metadata.json"
        source.write_text('{"scene_id":44,"other":1}', encoding="utf-8")
        self.assert_rejected("source scene summary differs")
        (self.roots["baseline"] / "scene_044/metadata.json").unlink()
        candidate.unlink()
        self.write_report()
        self.assertEqual(self.validate().fallback_resources, ("scene_044/canim_004",))
        source.write_text('{"scene_id":45}', encoding="utf-8")
        self.assert_rejected("scene_id differs")

    def test_source_variant_manifest_is_validated_not_silently_ignored(self):
        variants = []
        for resource in self.report["resources"]:
            name = resource["resource"]
            variants.append({"path": name, "resource": name, "md5": hashlib.md5(name.encode()).hexdigest(),
                             "archives": ["VGS.LIB"], "frames": resource["frames_compared"]})
        manifest = {"variant_count": 2, "resource_count": 2, "frame_count": 4, "variants": variants}
        path = self.roots["source"] / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(self.validate().fallback_resources, ("scene_044/canim_004",))
        manifest["variants"][0]["path"] = "../outside"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assert_rejected("resource path")
        manifest["variants"][0]["path"] = "scene_044/canim_004"
        manifest["frame_count"] = 999
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assert_rejected("counts differ")

    def test_room_summary_counts_are_cross_checked_against_manifests(self):
        frame = {"index": 0, "file": "frame.png", "width": 1, "height": 1,
                 "offset_x": 0, "offset_y": 0}
        metadata = {"room_index": 1, "frame_count": 1, "frames": [frame]}
        tree = validator.Tree(self.root, {"scene_044/room_001": validator.Manifest(self.root, metadata, "")}, {}, {})
        summary = {"scene_id": 44, "room_count": 1, "frame_count": 1,
                   "rooms": [{"room_index": 1, "directory": "room_001", "frame_count": 1}]}
        validator.verify_summary(tree, "scene_044", summary)
        summary["rooms"][0]["frame_count"] = 2
        with self.assertRaisesRegex(validator.ValidationError, "frame_count"):
            validator.verify_summary(tree, "scene_044", summary)

    def test_new_exclusive_plan_and_input_safety(self):
        plan = self.validate()
        before = self.report_path.read_bytes()
        existing = self.root / "existing.json"
        existing.write_text("keep", encoding="utf-8")
        link = self.root / "link.json"
        link.symlink_to(existing)
        for path in (existing, link, self.report_path, self.roots["candidate"] / "new.json"):
            with self.subTest(path=path), self.assertRaises(validator.ValidationError):
                validator.write_plan(path, plan, self.report_path)
        self.assertEqual(existing.read_text(), "keep")
        self.assertEqual(self.report_path.read_bytes(), before)
        self.assertFalse((self.roots["candidate"] / "new.json").exists())
        destination = self.root / "new-plan.json"
        validator.write_plan(destination, plan, self.report_path)
        saved = json.loads(destination.read_text())
        self.assertEqual(saved["fallback_resources"], ["scene_044/canim_004"])
        self.assertEqual(saved["audit_sha256"], sha(self.report_path))

    def test_cli_failure_does_not_create_plan(self):
        self.report["errors"] = ["not trustworthy"]
        self.write_report()
        destination = self.root / "not-created.json"
        args = ["--audit", str(self.report_path), "--baseline", str(self.roots["baseline"]),
                "--candidate", str(self.roots["candidate"]), "--source", str(self.roots["source"]),
                "--plan", str(destination)]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(validator.main(args), 1)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
