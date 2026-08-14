#!/usr/bin/env python3
"""Regression coverage for strict multi-view output and its sidecar manifest."""
import json
from contextlib import redirect_stdout
from io import StringIO
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wam import cli as wcli  # noqa: E402
from wam import codex_cli  # noqa: E402
from wam import render as wrender  # noqa: E402
from wam import views as wviews  # noqa: E402


SOURCE = """model multiviewtest
  height 2.0
palette
  body #aa6633
skeleton
  root pelvis at 0.4
  bone spine parent=pelvis dir=up len=0.35
parts
  loft core bones=pelvis..spine material=body
    ring 0.00 w=0.20 d=0.12
    ring 1.00 w=0.08 d=0.05
    cap start=dome end=dome
  loft nose bone=spine at=0.80 dir=fwd len=0.18 material=body
    ring 0.00 w=0.04 d=0.04
    ring 1.00 w=0.01 d=0.01
    cap start=none end=dome
"""


class MultiViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wammultiview")
        self.source = os.path.join(self.tmp, "model.wam")
        with open(self.source, "w", encoding="utf-8") as output:
            output.write(SOURCE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_named_and_custom_views_are_strict(self):
        specs = wviews.parse_views("front,high:25:35,side")
        self.assertEqual([view.id for view in specs], ["front", "high", "side"])
        self.assertEqual((specs[1].yaw, specs[1].pitch), (25.0, 35.0))
        with self.assertRaises(wviews.ViewSpecError):
            wviews.parse_views("front,typo")
        with self.assertRaises(wviews.ViewSpecError):
            wviews.parse_views("front,front")
        with self.assertRaises(wviews.ViewSpecError):
            wviews.parse_views("front,Front:90")
        with self.assertRaises(wviews.ViewSpecError):
            wviews.parse_views("front,")

    def test_shared_framing_uses_each_custom_pitch(self):
        vertices = np.array([
            [-0.5, -2.0, -0.2], [0.5, 2.0, 0.2], [0.0, 0.0, 1.5]
        ])
        view = wviews.ViewSpec("steep", 25.0, 70.0)
        center, distance = wcli.shared_framing(vertices, [view], 320, 480)
        expected = wrender.fit_distance(
            vertices, center, wrender.orbit_basis(view.yaw, view.pitch),
            28.0, 320 / 480, 1.12)
        self.assertAlmostEqual(distance, expected)

    def test_cross_drive_render_path_falls_back_to_absolute(self):
        expected = os.path.abspath(self.source).replace(os.sep, "/")
        with mock.patch("wam.views.os.path.relpath",
                        side_effect=ValueError("different drives")):
            self.assertEqual(wviews._relative(self.source, self.tmp), expected)

    def test_compile_writes_each_panel_and_deterministic_manifest(self):
        prefix = os.path.join(self.tmp, "first", "model")
        wcli.compile_model(
            self.source, prefix, "front,side,back,high:25:35",
            do_gltf=False, do_viewer=False, quiet=True, width=96, height=120)

        manifest_path = prefix + "_views.json"
        with open(manifest_path, encoding="utf-8") as source:
            manifest = json.load(source)
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(set(manifest["diagnostics"]), {"warnings", "infos"})
        self.assertEqual(
            [entry["id"] for entry in manifest["views"]],
            ["front", "side", "back", "high"],
        )
        for entry in manifest["views"]:
            self.assertTrue(os.path.isfile(os.path.join(
                os.path.dirname(manifest_path), entry["file"])))
            self.assertEqual((entry["width"], entry["height"]), (96, 120))
        self.assertNotEqual(
            manifest["views"][0]["sha256"], manifest["views"][1]["sha256"])

        # Determinism is part of the agent contract: a hash change must mean
        # the source, view, renderer, or dimensions actually changed.
        second = os.path.join(self.tmp, "second", "model")
        wcli.compile_model(
            self.source, second, "front,side,back,high:25:35",
            do_gltf=False, do_viewer=False, quiet=True, width=96, height=120)
        with open(second + "_views.json", encoding="utf-8") as source:
            manifest2 = json.load(source)
        self.assertEqual(
            [entry["sha256"] for entry in manifest["views"]],
            [entry["sha256"] for entry in manifest2["views"]],
        )

    def test_codex_cli_never_reports_stale_optional_artifacts(self):
        prefix = os.path.join(self.tmp, "reused", "model")
        first_output = StringIO()
        with redirect_stdout(first_output):
            code = codex_cli.compile_command([
                self.source, "-o", prefix, "--views", "front", "--bones"
            ])
        self.assertEqual(code, 0)
        first = json.loads(first_output.getvalue())
        self.assertTrue(first["artifacts"]["gltf"])
        self.assertTrue(first["artifacts"]["viewer"])
        self.assertTrue(first["artifacts"]["bones"])

        second_output = StringIO()
        with redirect_stdout(second_output):
            code = codex_cli.compile_command([
                self.source, "-o", prefix, "--views", "front",
                "--no-gltf", "--no-viewer",
            ])
        self.assertEqual(code, 0)
        second = json.loads(second_output.getvalue())
        # The files still exist from the first run, which is why existence
        # checks cannot prove that an artifact belongs to the current run.
        self.assertTrue(os.path.isfile(prefix + ".gltf"))
        self.assertIsNone(second["artifacts"]["gltf"])
        self.assertIsNone(second["artifacts"]["viewer"])
        self.assertIsNone(second["artifacts"]["viewerData"])
        self.assertIsNone(second["artifacts"]["bones"])

    def test_codex_cli_rejects_a_requested_missing_animation(self):
        for strict in (False, True):
            output = StringIO()
            arguments = [
                self.source, "-o", os.path.join(self.tmp, "bad-animation"),
                "--views", "front", "--anim", "does_not_exist",
            ]
            if strict:
                arguments.append("--strict")
            with redirect_stdout(output):
                code = codex_cli.compile_command(arguments)
            self.assertEqual(code, 2)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["ok"])
            self.assertIn("no such anim", payload["error"])

    def test_codex_cli_rejects_non_positive_render_dimensions(self):
        for flag, value in (("--width", "0"), ("--height", "-1")):
            with self.subTest(flag=flag):
                output = StringIO()
                with redirect_stdout(output):
                    code = codex_cli.compile_command([
                        self.source, "-o", os.path.join(self.tmp, "bad-size"),
                        "--views", "front", flag, value,
                    ])
                self.assertEqual(code, 2)
                payload = json.loads(output.getvalue())
                self.assertFalse(payload["ok"])
                self.assertIn("positive", payload["error"])

    def test_process_usage_errors_always_emit_json(self):
        commands = (
            ["compile", self.source, "--width", "not-a-number"],
            ["references"],
            ["not-a-command"],
        )
        for arguments in commands:
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    [sys.executable, "-B", "-m", "wam.codex_cli", *arguments],
                    cwd=os.path.dirname(os.path.dirname(__file__)),
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(completed.returncode, 2)
                payload = json.loads(completed.stdout)
                self.assertFalse(payload["ok"])
                self.assertTrue(payload["error"])
                self.assertIn("ERROR:", completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
