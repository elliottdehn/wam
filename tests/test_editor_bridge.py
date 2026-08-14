#!/usr/bin/env python3
"""Regression coverage for the loopback WAM save-and-rebuild bridge."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import cli as wcli  # noqa: E402
from wam import editor_bridge as bridge  # noqa: E402
from wam import edits as wedges  # noqa: E402


SOURCE = """model bridgefixture
  height 2.0
palette
  steel #336699
skeleton
  root pelvis at 0.025
  bone spine parent=pelvis dir=up len=0.30
parts
  loft core bones=pelvis..spine material=steel
    ring 0.00 w=0.12 d=0.10
    ring 1.00 w=0.10 d=0.08
    cap start=dome end=dome
"""


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EditorBridgeTests(unittest.TestCase):
    def setUp(self):
        # The bridge resolves every path it is handed, so the fixture must too.
        # On macOS the system temporary directory is /var -> /private/var, and
        # an unresolved prefix silently fails to match the discovered profile.
        self.tmp = Path(tempfile.mkdtemp(prefix="wam-editor-bridge-")).resolve()
        self.source = self.tmp / "fixture.wam"
        self.source.write_text(SOURCE, encoding="utf-8")
        self.prefix = self.tmp / "renders"
        # This is the ordinary generation a connected save must replace.
        wcli.compile_model(str(self.source), str(self.prefix), "front,side",
                           quiet=True, width=64, height=80,
                           bones_overlay=True, light=(35, 140, .35))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def layer(self, color="#ee8811"):
        layer = wedges.new_layer(self.source)
        layer["operations"] = [{"type": "paint_part", "part": "core",
                                "color": color, "mirror": False}]
        return layer

    def outputs(self):
        return {path.name: file_hash(path) for path in self.tmp.iterdir()
                if path.is_file() and path.name != self.source.name}

    def test_discovers_current_manifest_profile_and_rebuilds_in_place(self):
        profile = bridge.discover_profile(self.source)
        self.assertEqual(profile.out_prefix, self.prefix)
        self.assertEqual(profile.width, 64)
        self.assertEqual(profile.height, 80)
        self.assertEqual(len(profile.views), 2)
        self.assertTrue(profile.bones)
        self.assertEqual(profile.light, (35.0, 140.0, .35))

        # Two output sets for one source are ambiguous by design.  The caller
        # must name the intended prefix instead of silently replacing both.
        wcli.compile_model(str(self.source), str(self.tmp / "alternate"), "front",
                           quiet=True, width=64, height=80)
        with self.assertRaisesRegex(bridge.EditorBridgeError, "multiple render sets"):
            bridge.discover_profile(self.source)
        self.assertEqual(bridge.discover_profile(self.source, "renders").out_prefix,
                         self.prefix)

        source_before = self.source.read_bytes()
        before = self.outputs()
        workspace = bridge.EditorWorkspace(self.source, "renders")
        result = workspace.save_layer(self.layer())

        sidecar = self.source.with_suffix(".wamedit.json")
        self.assertEqual(result["layer"], sidecar.name)
        self.assertTrue(sidecar.is_file())
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertNotEqual(file_hash(self.prefix.with_suffix(".html")), before["renders.html"])
        for suffix in (".html", ".gltf", "_viewer.json", "_views.json",
                       "_sheet.png", "_bones.png", "_view_front.png", "_view_side.png"):
            self.assertTrue((self.tmp / ("renders" + suffix)).is_file(), suffix)
        with open(self.prefix.with_name("renders_views.json"), encoding="utf-8") as input_file:
            manifest = json.load(input_file)
        self.assertEqual(manifest["buildProfile"]["width"], 64)
        self.assertTrue(manifest["buildProfile"]["bones"])
        self.assertEqual(manifest["buildProfile"]["light"], [35.0, 140.0, .35])
        with open(self.prefix.with_name("renders_viewer.json"), encoding="utf-8") as input_file:
            viewer = json.load(input_file)
        self.assertEqual(viewer["editLayer"]["operations"], self.layer()["operations"])

    def test_rejects_stale_layer_without_touching_current_outputs(self):
        workspace = bridge.EditorWorkspace(self.source)
        before = self.outputs()
        stale = self.layer()
        stale["source"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(bridge.EditorBridgeError, "different WAM source"):
            workspace.save_layer(stale)
        self.assertEqual(self.outputs(), before)
        self.assertFalse(self.source.with_suffix(".wamedit.json").exists())

        invalid = self.layer()
        invalid["operations"] = [{"type": "not-a-real-edit"}]
        with self.assertRaisesRegex(bridge.EditorBridgeError, "unknown edit operation"):
            workspace.save_layer(invalid)
        self.assertEqual(self.outputs(), before)

    def test_failed_promotion_restores_the_previous_generation(self):
        sidecar = self.source.with_suffix(".wamedit.json")
        sidecar.write_text(json.dumps(wedges.new_layer(self.source)), encoding="utf-8")
        before = self.outputs()
        workspace = bridge.EditorWorkspace(self.source)
        actual_replace = os.replace

        def fail_when_html_is_promoted(source, destination):
            source_path, destination_path = Path(source), Path(destination)
            if source_path.parent.name == "new" and destination_path == self.tmp / "renders.html":
                raise OSError("simulated promotion failure")
            return actual_replace(source, destination)

        with mock.patch("wam.editor_bridge.os.replace", side_effect=fail_when_html_is_promoted):
            with self.assertRaisesRegex(bridge.EditorBridgeError, "could not replace"):
                workspace.save_layer(self.layer("#11aa33"))
        self.assertEqual(self.outputs(), before)
        self.assertFalse(list(self.tmp.glob(".wam-edit-transaction-*")))

    def test_failure_during_backup_keeps_the_files_not_yet_backed_up(self):
        """The dangerous half of a failed save: crash while still backing up.

        Recovery used to unlink every managed name before restoring, so the
        originals that had not reached the backup directory yet were deleted
        outright -- the recovery path destroying the generation it exists to
        protect.  Only a failure in the *promote* loop was covered before, and
        that is the safe case, because by then every original is in the backup.
        """
        before = self.outputs()
        workspace = bridge.EditorWorkspace(self.source)
        actual_replace = os.replace
        moved = []

        def fail_on_second_backup(source, destination):
            destination_path = Path(destination)
            if destination_path.parent.name == "backup":
                moved.append(destination_path.name)
                if len(moved) == 2:
                    raise OSError("simulated crash mid-backup")
            return actual_replace(source, destination)

        with mock.patch("wam.editor_bridge.os.replace",
                        side_effect=fail_on_second_backup):
            with self.assertRaisesRegex(bridge.EditorBridgeError, "could not replace"):
                workspace.save_layer(self.layer("#11aa33"))
        # The fixture must actually exercise the hazard: a partial backup with
        # more managed artifacts still sitting in the project directory.
        self.assertEqual(len(moved), 2)
        self.assertGreater(len(before), len(moved))
        self.assertEqual(self.outputs(), before)
        self.assertFalse(list(self.tmp.glob(".wam-edit-transaction-*")))
        self.assertFalse(self.source.with_suffix(".wamedit.json").exists())

    def test_startup_recovery_of_a_prepared_transaction_keeps_originals(self):
        """A journal stuck at "prepared" means the backup loop never finished."""
        untouched = self.tmp / "renders_sheet.png"
        original_sheet = untouched.read_bytes()
        target = self.tmp / "renders.html"
        original_html = target.read_bytes()
        transaction = Path(tempfile.mkdtemp(prefix=".wam-edit-transaction-", dir=self.tmp))
        backup, new = transaction / "backup", transaction / "new"
        backup.mkdir()
        new.mkdir()
        os.replace(target, backup / target.name)
        bridge._write_json(transaction / "journal.json", {
            "schemaVersion": 1,
            "source": str(self.source.resolve()),
            "state": "prepared",
            "names": [target.name, untouched.name],
        })
        bridge.EditorWorkspace(self.source)
        self.assertEqual(target.read_bytes(), original_html)
        self.assertEqual(untouched.read_bytes(), original_sheet)
        self.assertFalse(transaction.exists())

    def test_startup_recovers_an_interrupted_promotion(self):
        target = self.tmp / "renders.html"
        original = target.read_bytes()
        transaction = Path(tempfile.mkdtemp(prefix=".wam-edit-transaction-", dir=self.tmp))
        backup, new = transaction / "backup", transaction / "new"
        backup.mkdir()
        new.mkdir()
        os.replace(target, backup / target.name)
        target.write_text("partial replacement", encoding="utf-8")
        bridge._write_json(transaction / "journal.json", {
            "schemaVersion": 1,
            "source": str(self.source.resolve()),
            "state": "backed_up",
            "names": [target.name],
        })
        bridge.EditorWorkspace(self.source)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(transaction.exists())

    def test_loopback_requires_the_session_token(self):
        workspace = bridge.EditorWorkspace(self.source)
        server = bridge._EditorHTTPServer(("127.0.0.1", 0), workspace, "test-token")
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        root = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            with self.assertRaises(HTTPError) as denied:
                urlopen(root + "/", timeout=3)
            self.assertEqual(denied.exception.code, 403)
            with contextlib.closing(urlopen(root + "/?token=test-token", timeout=3)) as response:
                self.assertEqual(response.status, 200)
                page = response.read().decode("utf-8")
            # Asserting the bare identifier proved nothing: the viewer's own
            # client code mentions __WAM_EDITOR_BRIDGE__, so the assertion
            # passed for a year while the injection silently matched no anchor
            # and every connected session looked read-only to the browser.
            # Pin the assignment and the session values instead.
            self.assertIn("window.__WAM_EDITOR_BRIDGE__={", page)
            config = json.loads(page.split("window.__WAM_EDITOR_BRIDGE__=", 1)[1]
                                    .split(";</script>", 1)[0])
            self.assertEqual(config["schemaVersion"], 1)
            self.assertEqual(config["token"], "test-token")
            self.assertEqual(config["saveUrl"], "/api/save")
            self.assertEqual(config["sourceName"], self.source.name)
            # The template is a fragment with no </head>; the config must still
            # precede the viewer script that reads it.
            self.assertLess(page.index("window.__WAM_EDITOR_BRIDGE__={"),
                            page.index("function editorBridge()"))
            request = Request(root + "/api/save", data=b"{}", method="POST",
                              headers={"Content-Type": "application/json"})
            with self.assertRaises(HTTPError) as denied_post:
                urlopen(request, timeout=3)
            self.assertEqual(denied_post.exception.code, 403)
            accepted = Request(
                root + "/api/save", data=json.dumps(self.layer()).encode("utf-8"),
                method="POST", headers={
                    "Content-Type": "application/json",
                    "X-WAM-Editor-Token": "test-token",
                })
            with contextlib.closing(urlopen(accepted, timeout=6)) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(result["ok"])
            self.assertTrue(self.source.with_suffix(".wamedit.json").is_file())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
