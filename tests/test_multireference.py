#!/usr/bin/env python3
"""Regression coverage for several named references and per-image grids."""
import json
import hashlib
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wam import references as wreferences  # noqa: E402


class MultiReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wamrefs")
        self.paths = []
        for name, size, color in (
                ("front view.png", (120, 80), (220, 40, 40)),
                ("côté.png", (80, 120), (40, 220, 40)),
                ("detail.png", (64, 64), (40, 40, 220))):
            path = os.path.join(self.tmp, name)
            Image.new("RGB", size, color).save(path)
            self.paths.append(path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_three_references_keep_independent_roles_views_and_tiles(self):
        out_dir = os.path.join(self.tmp, "prepared refs")
        manifest_path, payload = wreferences.prepare_references(
            ["front=%s" % self.paths[0], "side=%s" % self.paths[1],
             "eye_detail=%s" % self.paths[2]],
            out_dir,
            view_args=["front=front", "side=side"],
            kind_args=["eye_detail=detail"],
            grid="2x3",
        )
        self.assertTrue(os.path.isfile(manifest_path))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "reference_board.png")))
        self.assertEqual([entry["id"] for entry in payload["references"]],
                         ["front", "side", "eye_detail"])
        self.assertEqual(payload["references"][0]["view"]["id"], "front")
        self.assertEqual(payload["references"][1]["view"]["id"], "side")
        self.assertIsNone(payload["references"][2]["view"])
        self.assertEqual(payload["references"][2]["kind"], "detail")
        for entry in payload["references"]:
            self.assertEqual(len(entry["grid"]["files"]), 6)
            for path in entry["grid"]["files"]:
                self.assertTrue(os.path.isfile(os.path.join(out_dir, path)))

        # The on-disk representation is the API consumed by later Codex runs.
        with open(manifest_path, encoding="utf-8") as source:
            self.assertEqual(json.load(source), payload)

    def test_duplicate_and_unknown_reference_metadata_are_rejected(self):
        with self.assertRaises(wreferences.ReferenceSpecError):
            wreferences.prepare_references(
                ["same=%s" % self.paths[0], "same=%s" % self.paths[1]],
                os.path.join(self.tmp, "duplicate"))
        with self.assertRaises(wreferences.ReferenceSpecError):
            wreferences.prepare_references(
                ["Ref=%s" % self.paths[0], "ref=%s" % self.paths[1]],
                os.path.join(self.tmp, "case-duplicate"))
        with self.assertRaises(wreferences.ReferenceSpecError):
            wreferences.prepare_references(
                ["front=%s" % self.paths[0]],
                os.path.join(self.tmp, "unknown"),
                view_args=["rear=back"])
        for reserved in ("NUL", "con", "COM1", "lpt9"):
            with self.subTest(reserved=reserved):
                with self.assertRaises(wreferences.ReferenceSpecError):
                    wreferences.prepare_references(
                        ["%s=%s" % (reserved, self.paths[0])],
                        os.path.join(self.tmp, "reserved-" + reserved),
                        grid="1x1")

    def test_source_cannot_overlap_generated_output(self):
        out_dir = os.path.join(self.tmp, "collision")
        os.makedirs(out_dir)
        source_path = os.path.join(out_dir, "reference_board.png")
        Image.new("RGB", (48, 32), (12, 34, 56)).save(source_path)
        with open(source_path, "rb") as source:
            before = source.read()
        before_hash = hashlib.sha256(before).hexdigest()

        with self.assertRaises(wreferences.ReferenceSpecError):
            wreferences.prepare_references(
                ["front=%s" % source_path], out_dir, grid="2x2")

        with open(source_path, "rb") as source:
            after = source.read()
        self.assertEqual(hashlib.sha256(after).hexdigest(), before_hash)
        self.assertEqual(after, before)

    def test_exif_orientation_is_applied_before_cropping(self):
        path = os.path.join(self.tmp, "phone.jpg")
        exif = Image.Exif()
        exif[274] = 6  # Rotate 90 degrees clockwise for display.
        Image.new("RGB", (20, 10), (90, 120, 150)).save(path, exif=exif)
        _, payload = wreferences.prepare_references(
            ["phone=%s" % path], os.path.join(self.tmp, "phone-out"),
            grid="2x2")
        self.assertEqual(
            (payload["references"][0]["width"],
             payload["references"][0]["height"]),
            (10, 20),
        )

    def test_transparent_pixels_do_not_become_visible_reference_content(self):
        path = os.path.join(self.tmp, "transparent.png")
        image = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
        image.putpixel((4, 4), (0, 0, 255, 255))
        image.save(path)
        _, payload = wreferences.prepare_references(
            ["alpha=%s" % path], os.path.join(self.tmp, "alpha-out"),
            grid="1x1")
        tile = os.path.join(
            self.tmp, "alpha-out", payload["references"][0]["grid"]["files"][0])
        with Image.open(tile) as prepared:
            self.assertEqual(prepared.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(prepared.getpixel((4, 4)), (0, 0, 255))

    def test_tiny_images_fail_as_structured_json_not_empty_crops(self):
        path = os.path.join(self.tmp, "tiny.png")
        Image.new("RGB", (1, 1), (1, 2, 3)).save(path)
        with self.assertRaises(wreferences.ReferenceSpecError):
            wreferences.prepare_references(
                ["tiny=%s" % path], os.path.join(self.tmp, "tiny-out"))

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = wreferences.main([
                "--reference", "tiny=%s" % path,
                "-o", os.path.join(self.tmp, "tiny-cli"),
            ])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(stdout.getvalue())["ok"])
        self.assertIn("each crop", stderr.getvalue())

    def test_cross_drive_manifest_path_falls_back_to_absolute(self):
        expected = os.path.abspath(self.paths[0]).replace(os.sep, "/")
        with mock.patch("wam.references.os.path.relpath",
                        side_effect=ValueError("different drives")):
            self.assertEqual(
                wreferences._relative(self.paths[0], self.tmp), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
