#!/usr/bin/env python3
"""Regression coverage for non-destructive WAM edit layers.

The tests deliberately exercise the sidecar at the mesh boundary rather than
the HTML implementation.  That boundary is what guarantees a downloaded
layer produces the same correction in PNGs, glTF, and a rebuilt viewer.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import cli as wcli  # noqa: E402
from wam import codex_cli  # noqa: E402
from wam import edits as wedges  # noqa: E402
from wam import mesh as wmesh  # noqa: E402
from wam import parser as wparser  # noqa: E402
from wam import skeleton as wskel  # noqa: E402
from wam import viewer_export as wviewer  # noqa: E402


SRC = """model editfixture
  height 2.0
palette
  steel #336699
skeleton
  root pelvis at 0.5
  bone spine parent=pelvis dir=up len=0.30
parts
  loft core bones=pelvis..spine material=steel
    ring 0.00 w=0.12 d=0.10
    ring 1.00 w=0.10 d=0.08
    cap start=dome end=dome
  mirror
    loft arm bone=spine at=0.55 dir=side len=0.28 material=steel
      ring 0.00 w=0.05 d=0.05
      ring 1.00 w=0.03 d=0.03
      cap start=dome end=dome
  end
"""


RIG_SRC = """model rigfixture
  height 2.0
palette
  steel #336699
skeleton
  root pelvis at 0.5
  bone body parent=pelvis dir=up len=0.30
  bone ear_bone parent=body at=0.8 side=0.10 dir=up len=0.20
parts
  loft core bones=pelvis..body material=steel
    ring 0.00 w=0.12 d=0.10
    ring 1.00 w=0.10 d=0.08
    cap start=dome end=dome
  loft ear bone=ear_bone at=0 dir=up len=0.20 material=steel
    ring 0.00 w=0.05 d=0.04
    ring 1.00 w=0.01 d=0.01 tip
    cap start=dome end=none
  loft shared_badge bone=body at=0.50 dir=fwd len=0.08 material=steel
    ring 0.00 w=0.03 d=0.02
    ring 1.00 w=0.02 d=0.01 tip
    cap start=dome end=none
"""


def build_mesh(path):
    model = wparser.parse_file(path)
    bones, order = wskel.solve(model)
    return model, bones, wmesh.build(model, bones), order


def centre(mesh, part):
    lo, hi = mesh.part_ranges[part]
    return np.mean(np.asarray(mesh.verts[lo:hi]), axis=0)


def vertices(mesh, part):
    lo, hi = mesh.part_ranges[part]
    return np.asarray(mesh.verts[lo:hi], dtype=float)


class EditLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wam-edits-")
        self.source = os.path.join(self.tmp, "fixture.wam")
        with open(self.source, "w", encoding="utf-8") as output:
            output.write(SRC)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def layer(self, operations):
        layer = wedges.new_layer(self.source)
        layer["operations"] = operations
        return layer

    def test_transform_paint_hide_primitive_and_mirror(self):
        _model, bones, mesh, _order = build_mesh(self.source)
        left_before, right_before = centre(mesh, "arm.l"), centre(mesh, "arm.r")
        core_faces = len(wedges._part_faces(mesh, "core"))
        base_vertices, base_triangles = len(mesh.verts), len(mesh.tris)
        layer = self.layer([
            {"type": "transform", "part": "arm.l", "translation": [0.08, 0, 0],
             "rotation": [0, 0, 15], "scale": [1, 1, 1], "mirror": True},
            {"type": "paint_faces", "part": "core", "faces": [0], "color": "#ff0088"},
            {"type": "hide_faces", "part": "core", "faces": [1]},
            {"type": "add_primitive", "id": "patch", "primitive": "box", "bone": "spine",
             "center": [0.22, 1.0, 0], "rotation": [0, 0, 0], "scale": [.1, .1, .1],
             "color": "#11aa33", "mirror": True},
        ])
        wedges.apply_layer(mesh, bones, layer)
        left_after, right_after = centre(mesh, "arm.l"), centre(mesh, "arm.r")
        self.assertGreater(left_after[0], left_before[0])
        self.assertLess(right_after[0], right_before[0])
        self.assertAlmostEqual(left_after[0], -right_after[0], places=4)
        self.assertEqual(len(wedges._part_faces(mesh, "core")), core_faces - 1)
        self.assertGreater(len(mesh.verts), base_vertices)
        self.assertGreater(len(mesh.tris), base_triangles)
        self.assertIn("manual:patch", mesh.part_ranges)
        self.assertIn("manual:patch.mirror", mesh.part_ranges)
        self.assertIn("edit_ff0088", [name for name, _rgb in mesh.materials])

    def test_local_rotation_uses_a_persisted_right_handed_bone_frame(self):
        _model, bones, mesh, _order = build_mesh(self.source)
        part = "arm.l"
        lo, hi = mesh.part_ranges[part]
        before = np.asarray(mesh.verts[lo:hi], dtype=float).copy()
        pivot = centre(mesh, part)
        bone = bones["spine"]
        # The viewer records axes rather than a bone name.  This freezes the
        # local frame in the portable layer and keeps a recompile deterministic.
        axes = [bone.side.tolist(), bone.up.tolist(), bone.dir.tolist()]
        operation = {"type": "transform", "part": part,
                     "translation": [0, 0, 0], "rotation": [0, 0, 35],
                     "scale": [1, 1, 1], "pivot": pivot.tolist(),
                     "rotationSpace": "local", "rotationBasis": axes}
        wedges.apply_layer(mesh, bones, self.layer([operation]))
        frame = np.column_stack([np.asarray(axis, dtype=float) for axis in axes])
        expected = np.asarray([frame @ wedges._rotation(operation["rotation"]) @ frame.T @
                               (point - pivot) + pivot for point in before])
        np.testing.assert_allclose(vertices(mesh, part), expected, atol=1e-8)

        for changes, message in (
            ({"rotationSpace": "view"}, "world or local"),
            ({"rotationSpace": "local", "rotationBasis": [[1, 0, 0], [1, 0, 0], [0, 0, 1]]}, "orthogonal"),
            ({"rotationBasis": axes}, "only supported"),
        ):
            _model, bad_bones, bad_mesh, _order = build_mesh(self.source)
            bad = {"type": "transform", "part": part, "rotation": [0, 0, 10],
                   "translation": [0, 0, 0], "scale": [1, 1, 1]}
            bad.update(changes)
            with self.assertRaisesRegex(wedges.EditLayerError, message):
                wedges.apply_layer(bad_mesh, bad_bones, self.layer([bad]))

    def test_local_move_scale_and_safe_rig_sync(self):
        rig_source = os.path.join(self.tmp, "rig.wam")
        with open(rig_source, "w", encoding="utf-8") as output:
            output.write(RIG_SRC)
        model, bones, mesh, _order = build_mesh(rig_source)
        eligibility = wedges.rig_sync_eligibility(mesh, bones)
        self.assertEqual(eligibility["ear"], ["ear_bone"])
        self.assertFalse(eligibility["core"])
        before_head, before_tail = bones["ear_bone"].head.copy(), bones["ear_bone"].tail.copy()
        before_center = centre(mesh, "ear")
        frame = [bones["ear_bone"].side.tolist(), bones["ear_bone"].up.tolist(),
                 bones["ear_bone"].dir.tolist()]
        layer = wedges.new_layer(rig_source)
        layer["operations"] = [{
            "type": "transform", "part": "ear", "translation": [.12, 0, 0],
            "rotation": [0, 0, 0], "scale": [1, 1, 1], "mirror": False,
            "translationSpace": "local", "translationBasis": frame,
            "scaleSpace": "local", "scaleBasis": frame, "syncRig": True,
        }]
        wedges.apply_layer(mesh, bones, layer)
        local_x = np.asarray(frame[0])
        np.testing.assert_allclose(bones["ear_bone"].head - before_head, local_x * .12, atol=1e-8)
        np.testing.assert_allclose(bones["ear_bone"].tail - before_tail, local_x * .12, atol=1e-8)
        self.assertGreater(np.linalg.norm(centre(mesh, "ear") - before_center), .05)

        _model, scale_bones, scale_mesh, _order = build_mesh(rig_source)
        scale_layer = wedges.new_layer(rig_source)
        scale_layer["operations"] = [{"type": "transform", "part": "ear", "translation": [0, 0, 0],
                                      "rotation": [0, 0, 0], "scale": [1, 1, 1.2], "mirror": False,
                                      "scaleSpace": "local", "scaleBasis": frame}]
        before_scale = vertices(scale_mesh, "ear").copy()
        wedges.apply_layer(scale_mesh, scale_bones, scale_layer)
        self.assertFalse(np.allclose(vertices(scale_mesh, "ear"), before_scale))

        _model, unsafe_bones, unsafe_mesh, _order = build_mesh(rig_source)
        unsafe = wedges.new_layer(rig_source)
        unsafe["operations"] = [{"type": "transform", "part": "core", "translation": [0, 0, 0],
                                  "rotation": [0, 0, 0], "scale": [1, 1, 1], "syncRig": True}]
        with self.assertRaisesRegex(wedges.EditLayerError, "not safe"):
            wedges.apply_layer(unsafe_mesh, unsafe_bones, unsafe)

    def test_rebase_preserves_input_and_only_enables_safe_requested_rig_parts(self):
        old_source = os.path.join(self.tmp, "old.wam")
        new_source = os.path.join(self.tmp, "new.wam")
        for path, suffix in ((old_source, "# old source\n"), (new_source, "# rigged source\n")):
            with open(path, "w", encoding="utf-8") as output:
                output.write(RIG_SRC + suffix)
        original = wedges.new_layer(old_source)
        original["operations"] = [{"type": "transform", "part": "ear", "translation": [.02, 0, 0],
                                   "rotation": [0, 0, 0], "scale": [1, 1, 1], "mirror": False}]
        input_layer = os.path.join(self.tmp, "old.wamedit.json")
        with open(input_layer, "w", encoding="utf-8") as output:
            json.dump(original, output)
        with open(input_layer, "rb") as source:
            original_bytes = source.read()
        rebased = os.path.join(self.tmp, "new.wamedit.json")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = codex_cli.rebase_edits_command([
                input_layer, "--from", old_source, "--to", new_source, "-o", rebased,
                "--sync-rig-parts", "ear",
            ])
        self.assertEqual(code, 0, stdout.getvalue())
        with open(input_layer, "rb") as source:
            self.assertEqual(source.read(), original_bytes)
        with open(rebased, encoding="utf-8") as source:
            migrated = json.load(source)
        self.assertEqual(migrated["source"]["sha256"], wedges.source_sha256(new_source))
        self.assertTrue(migrated["operations"][0]["syncRig"])

    def test_rejects_stale_unknown_and_invalid_edit_references(self):
        path = os.path.join(self.tmp, "bad.wamedit.json")
        with open(path, "w", encoding="utf-8") as output:
            json.dump(wedges.new_layer(self.source), output)
        with open(self.source, "a", encoding="utf-8") as output:
            output.write("\n# fingerprint changes\n")
        with self.assertRaises(wedges.EditLayerError):
            wedges.load_layer(path, self.source)

        # Restore a parseable original and then verify no unknown target is
        # accepted merely because a mesh happens to have a similar name.
        with open(self.source, "w", encoding="utf-8") as output:
            output.write(SRC)
        _model, bones, mesh, _order = build_mesh(self.source)
        with self.assertRaisesRegex(wedges.EditLayerError, "unknown part"):
            wedges.apply_layer(mesh, bones, self.layer([
                {"type": "paint_part", "part": "not-a-part", "color": "#ffffff"},
            ]))
        _model, bones, mesh, _order = build_mesh(self.source)
        with self.assertRaisesRegex(wedges.EditLayerError, "positive"):
            wedges.apply_layer(mesh, bones, self.layer([
                {"type": "transform", "part": "core", "scale": [1, 0, 1]},
            ]))
        _model, bones, mesh, _order = build_mesh(self.source)
        with self.assertRaisesRegex(wedges.EditLayerError, "no exact mirrored"):
            wedges.apply_layer(mesh, bones, self.layer([
                {"type": "paint_part", "part": "core", "color": "#ffffff", "mirror": True},
            ]))

    def test_underscore_pairs_and_explicit_asymmetric_transform_target(self):
        _model, bones, mesh, _order = build_mesh(self.source)
        # WAM authors often use ``arm_l`` rather than the compiler-generated
        # ``arm.l`` spelling.  Both are now conventional automatic pairs.
        mesh.part_ranges["arm_l"] = mesh.part_ranges.pop("arm.l")
        mesh.part_ranges["arm_r"] = mesh.part_ranges.pop("arm.r")
        left_before, right_before = centre(mesh, "arm_l"), centre(mesh, "arm_r")
        wedges.apply_layer(mesh, bones, self.layer([
            {"type": "transform", "part": "arm_l", "translation": [.05, 0, 0],
             "rotation": [0, 0, 0], "scale": [1, 1, 1], "mirror": True},
        ]))
        self.assertGreater(centre(mesh, "arm_l")[0], left_before[0])
        self.assertLess(centre(mesh, "arm_r")[0], right_before[0])

        _model, bones, mesh, _order = build_mesh(self.source)
        source_before, target_before = centre(mesh, "core"), centre(mesh, "arm.l")
        target_vertex = vertices(mesh, "arm.l")[0].copy()
        op = {"type": "transform", "part": "core", "mirror": True, "mirrorPart": "arm.l",
              "translation": [.08, 0, .02], "rotation": [0, 0, 25], "scale": [1.2, .9, 1.1],
              "pivot": source_before.tolist()}
        wedges.apply_layer(mesh, bones, self.layer([op]))
        self.assertGreater(centre(mesh, "core")[0], source_before[0])
        self.assertAlmostEqual(centre(mesh, "arm.l")[0], target_before[0] - .08, places=6)
        reflect = np.diag([-1.0, 1.0, 1.0])
        expected = reflect @ wedges._rotation(op["rotation"]) @ reflect @ (
            (target_vertex - target_before) * np.asarray(op["scale"])) + target_before + reflect @ np.asarray(op["translation"])
        np.testing.assert_allclose(vertices(mesh, "arm.l")[0], expected, atol=1e-8)

        for bad, message in (
            ({"mirrorPart": "missing"}, "unknown part"),
            ({"mirrorPart": "core"}, "differ"),
            ({"mirror": False, "mirrorPart": "arm.l"}, "requires mirror"),
        ):
            _model, bones, bad_mesh, _order = build_mesh(self.source)
            operation = {"type": "transform", "part": "core", "mirror": True,
                         "translation": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}
            operation.update(bad)
            with self.assertRaisesRegex(wedges.EditLayerError, message):
                wedges.apply_layer(bad_mesh, bones, self.layer([operation]))
        _model, bones, bad_mesh, _order = build_mesh(self.source)
        with self.assertRaisesRegex(wedges.EditLayerError, "only supported"):
            wedges.apply_layer(bad_mesh, bones, self.layer([
                {"type": "paint_part", "part": "core", "color": "#ffffff", "mirror": False,
                 "mirrorPart": "arm.l"},
            ]))

    def test_viewer_metadata_groups_parts_by_primary_bone(self):
        model, bones, mesh, order = build_mesh(self.source)
        mesh.part_ranges["arm_l"] = mesh.part_ranges.pop("arm.l")
        mesh.part_ranges["arm_r"] = mesh.part_ranges.pop("arm.r")
        data = wviewer.export_built(model, bones, order, mesh,
                                    os.path.join(self.tmp, "hierarchy_viewer.json"))
        parts = {part["id"]: part for part in data["parts"]}
        self.assertEqual(parts["arm_l"]["mirror"], "arm_r")
        self.assertEqual(parts["arm_r"]["mirror"], "arm_l")
        self.assertEqual(parts["arm_l"]["bone"], "spine")
        self.assertTrue(all("bone" in part for part in data["parts"]))

    def test_compile_edits_reaches_png_gltf_and_viewer(self):
        layer = self.layer([
            {"type": "transform", "part": "arm.l", "translation": [.03, 0, 0],
             "rotation": [0, 0, 8], "scale": [1.05, 1, 1], "mirror": True,
             "mirrorPart": "arm.r", "rotationSpace": "local",
             "rotationBasis": [[-1, 0, 0], [0, 0, 1], [0, 1, 0]]},
            {"type": "paint_part", "part": "core", "color": "#ee8811"},
            {"type": "add_primitive", "id": "viewerpatch", "primitive": "sphere", "bone": "spine",
             "center": [0.15, 1.0, 0], "rotation": [0, 0, 0], "scale": [.1, .1, .1],
             "color": "#11aa33", "mirror": True},
        ])
        edit_path = os.path.join(self.tmp, "fixture.wamedit.json")
        with open(edit_path, "w", encoding="utf-8") as output:
            json.dump(layer, output)
        prefix = os.path.join(self.tmp, "compiled", "fixture")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = codex_cli.compile_command([
                self.source, "-o", prefix, "--views", "front,side", "--width", "80", "--height", "100",
                "--edits", edit_path,
            ])
        self.assertEqual(code, 0, stdout.getvalue())
        report = json.loads(stdout.getvalue())
        for key in ("gltf", "viewer", "viewerData", "sheet"):
            self.assertTrue(os.path.isfile(report["artifacts"][key]), key)
        self.assertEqual(len(report["artifacts"]["views"]), 2)
        with open(report["artifacts"]["gltf"], encoding="utf-8") as source:
            gltf = json.load(source)
        self.assertIn("edit_ee8811", [item["name"] for item in gltf["materials"]])
        self.assertIn("edit_11aa33", [item["name"] for item in gltf["materials"]])
        with open(report["artifacts"]["viewerData"], encoding="utf-8") as source:
            viewer = json.load(source)
        self.assertEqual(viewer["editLayer"]["operations"], layer["operations"])
        self.assertIn("manual:viewerpatch", [part["id"] for part in viewer["parts"]])
        self.assertTrue(all("t" in bone for bone in viewer["bones"]))
        with open(report["artifacts"]["viewer"], encoding="utf-8") as source:
            page = source.read()
        self.assertIn('id="editToggle"', page)
        self.assertIn('id="gizmoMove"', page)
        self.assertIn('id="rotationSpace"', page)
        self.assertIn('id="backgroundColor"', page)
        self.assertIn('id="geometrySelect"', page)
        self.assertIn('id="mirrorTarget"', page)
        self.assertIn('id="pickMirror"', page)
        self.assertIn('id="saveAllChanges"', page)
        self.assertIn('id="diskSaveStatus"', page)
        self.assertIn('id="discardDraft"', page)
        self.assertIn('id="recoveryStatus"', page)
        self.assertIn('id="forgetRecovery"', page)
        self.assertIn('id="downloadEdits"', page)
        self.assertIn('id="loadEdits"', page)
        self.assertIn('id="recenterCamera"', page)
        self.assertIn('id="editLog"', page)
        self.assertIn('id="copyLog"', page)
        self.assertIn('id="clearLog"', page)
        self.assertIn('id="selectObject"', page)
        self.assertIn('id="selectFace"', page)
        self.assertIn('id="paintFace"', page)
        self.assertIn('id="paintHelp"', page)
        self.assertIn('>Pick mirror pair<', page)
        self.assertIn('>Face select<', page)
        self.assertIn('>Paint faces<', page)
        self.assertIn('>Save all changes<', page)
        self.assertIn('>Download edit layer (.wamedit.json)<', page)
        self.assertIn('>Load edit layer file…<', page)
        self.assertIn('>Forget local recovery<', page)
        self.assertIn('>Recenter model<', page)
        self.assertIn("function mirrorTransformSettings", page)
        self.assertIn("function localRotationAxes(D,part)", page)
        self.assertIn("function transformRotationMatrix(op)", page)
        self.assertIn("rotationSpace:'local'", page)
        self.assertIn("function transformOrientationSettings(D,part)", page)
        self.assertIn("translationSpace:'local'", page)
        self.assertIn("scaleSpace:'local'", page)
        self.assertIn('id="syncRigTransform"', page)
        self.assertIn("Local (bone)", page)
        self.assertIn("function beginGizmoDrag", page)
        self.assertIn("function finishGizmoDrag", page)
        self.assertIn("function queueDraft", page)
        self.assertIn("function applyAllDraft", page)
        self.assertIn("function saveAllChanges", page)
        self.assertIn("function editorBridge", page)
        self.assertIn("function updateSaveBoundary", page)
        self.assertIn("Save all changes & rebuild", page)
        self.assertIn("X-WAM-Editor-Token", page)
        self.assertIn("browser-only viewer", page.lower())
        self.assertIn("function startMirrorPicker", page)
        self.assertIn("function pickMirrorSource", page)
        self.assertIn("function pickMirrorTarget", page)
        self.assertIn("function pickMirrorGeometry", page)
        self.assertIn("cv.addEventListener('contextmenu'", page)
        self.assertIn("e.button!==2", page)
        # Picking is an offscreen depth-tested render, rather than a projected
        # triangle approximation.  This keeps an occluded part out of both
        # selection and the mirror pipette.
        self.assertIn("function ensurePickTarget", page)
        self.assertIn("gl.DEPTH_ATTACHMENT", page)
        self.assertIn("function renderPickBuffer", page)
        self.assertIn("function refreshPickPositions", page)
        self.assertIn("gl.readPixels", page)
        self.assertIn("const HFS", page)
        self.assertIn("HALO_SOURCE", page)
        self.assertIn("HALO_MIRROR", page)

        # Gizmo drags visibly preview their transform, but the temporary state
        # is never a layer operation until it is added to the draft and saved.
        finish_gizmo = re.search(r"function finishGizmoDrag\(cancelled\)\{.*?\n\}", page, re.DOTALL)
        update_gizmo = re.search(r"function updateGizmoDrag\(event\)\{.*?\n\}", page, re.DOTALL)
        transform_preview = re.search(r"function updateTransformPreview\(\)\{.*?\n\}", page, re.DOTALL)
        preview_data = re.search(r"function previewData\(\)\{.*?\n\}", page, re.DOTALL)
        self.assertIsNotNone(finish_gizmo)
        self.assertIsNotNone(update_gizmo)
        self.assertIsNotNone(transform_preview)
        self.assertIsNotNone(preview_data)
        self.assertNotIn("queueDraft(", finish_gizmo.group(0))
        self.assertIn("Live preview ready", finish_gizmo.group(0))
        self.assertIn("editor.transformPreview=clone(op);storeTransformSession();refreshPreview();", update_gizmo.group(0))
        self.assertIn("editor.transformPreview=operation;storeTransformSession();refreshPreview();", transform_preview.group(0))
        self.assertIn("allTransformPreviews().forEach", preview_data.group(0))
        self.assertIn("queueDraft(op)", page)
        self.assertIn("editor.ops.concat(editor.draft)", page)

        # Recovery is browser-local and source-bound.  It contains editor
        # state only, validates it before restore, and never changes WAM data.
        recovery_snapshot = re.search(r"function recoverySnapshot\(\)\{.*?\n\}", page, re.DOTALL)
        recovery_validate = re.search(r"function validateRecoverySnapshot\(snapshot\)\{.*?\n\}", page, re.DOTALL)
        recovery_checkpoint = re.search(r"function checkpointRecovery\(reason='changes'\)\{.*?\n\}", page, re.DOTALL)
        recovery_restore = re.search(r"function restoreRecovery\(\)\{.*?\n\}", page, re.DOTALL)
        save_all = re.search(r"function saveAllChanges\(\)\{.*?\n\}", page, re.DOTALL)
        self.assertIsNotNone(recovery_snapshot)
        self.assertIsNotNone(recovery_validate)
        self.assertIsNotNone(recovery_checkpoint)
        self.assertIsNotNone(recovery_restore)
        self.assertIsNotNone(save_all)
        self.assertIn("const RECOVERY_PREFIX='wam.viewer.local-recovery.v2:'", page)
        self.assertIn("window.localStorage", page)
        self.assertIn("source:clone(source)", recovery_snapshot.group(0))
        self.assertIn("baseLayerSignature:baseLayerSignature()", recovery_snapshot.group(0))
        self.assertIn("snapshot.source.sha256!==source.sha256", recovery_validate.group(0))
        self.assertIn("snapshot.baseLayerSignature!==baseLayerSignature()", recovery_validate.group(0))
        self.assertIn("applyEditOp(D,op)", recovery_validate.group(0))
        self.assertIn("store.setItem(key,JSON.stringify(snapshot))", recovery_checkpoint.group(0))
        self.assertIn("Local recovery could not be saved", recovery_checkpoint.group(0))
        self.assertIn("editor.ops=clone(snapshot.ops)", recovery_restore.group(0))
        self.assertIn("editor.draft=clone(snapshot.draft)", recovery_restore.group(0))
        self.assertIn("checkpointRecovery('Save all changes complete')", page)
        self.assertIn("checkpointRecovery('Undo checkpoint updated')", page)
        self.assertIn("checkpointRecovery('Redo checkpoint updated')", page)
        self.assertIn("Save all included "+"'+previews.length+'", save_all.group(0))

        # Palette materials are usable paint inputs.  A swatch only changes
        # the colour picker; it does not queue a recolour on its own.
        self.assertIn("function renderPalette(DATA)", page)
        self.assertIn("function choosePaletteColour(hex,name)", page)
        self.assertIn("button.dataset.paletteColour=hex", page)
        self.assertIn("input.value=hex;syncPaletteSelection();", page)
        self.assertIn("(editor.baseline&&editor.baseline.mats)||DATA.mats||[]", page)
        self.assertIn("data-tooltip", page)
        self.assertIn('id="viewerTooltip"', page)
        self.assertIn("function showTooltip(target)", page)
        self.assertIn("document.addEventListener('focusin'", page)
        static_buttons = re.findall(r"<button\b[^>]*>", page)
        self.assertTrue(static_buttons)
        self.assertTrue(all("data-tooltip=" in button for button in static_buttons))
        self.assertIn("b.dataset.tooltip=anim?", page)

        # A dedicated face brush samples the existing depth-tested pick buffer
        # for every left-button sample and turns one complete stroke into
        # ordinary non-mirrored draft paint operations.
        brush_ops = re.search(r"function brushOperations\(stroke=editor\.paintBrush\.stroke\)\{.*?\n\}", page, re.DOTALL)
        begin_stroke = re.search(r"function beginPaintStroke\(event\)\{.*?\n\}", page, re.DOTALL)
        paint_sample = re.search(r"function paintStrokeAt\(event\)\{.*?\n\}", page, re.DOTALL)
        finish_stroke = re.search(r"function finishPaintStroke\(cancelled\)\{.*?\n\}", page, re.DOTALL)
        select_mode = re.search(r"function setSelectMode\(mode\)\{.*?\n\}", page, re.DOTALL)
        self.assertIsNotNone(brush_ops)
        self.assertIsNotNone(begin_stroke)
        self.assertIsNotNone(paint_sample)
        self.assertIsNotNone(finish_stroke)
        self.assertIsNotNone(select_mode)
        self.assertIn("type:'paint_faces'", brush_ops.group(0))
        self.assertIn("mirror:false", brush_ops.group(0))
        self.assertIn("readPickFaceAt({clientX:previous.x+dx*t,clientY:previous.y+dy*t})", paint_sample.group(0))
        self.assertIn("Math.ceil(Math.hypot(dx,dy)/2)", paint_sample.group(0))
        self.assertIn("renderPickBuffer(buildMVP().m)", paint_sample.group(0))
        self.assertIn("faces.includes(local)", paint_sample.group(0))
        self.assertIn("queueDraftOperations(operations", finish_stroke.group(0))
        self.assertIn("stopMirrorPicker('Mirror picker cancelled: selection mode changed.')", select_mode.group(0))
        self.assertIn("setPaintBrush(false,'Paint brush exited: selection mode changed.')", select_mode.group(0))
        self.assertIn("if(editor.paintBrush.active){beginPaintStroke(e);return;}", page)

        # The two-stage picker records a target but deliberately does not call
        # the normal selection helper, which would replace the source geometry.
        mirror_picker = re.search(r"function chooseMirrorTarget\(part\)\{.*?\n\}", page, re.DOTALL)
        mirror_source = re.search(r"function chooseMirrorSource\(part\)\{.*?\n\}", page, re.DOTALL)
        self.assertIsNotNone(mirror_picker)
        self.assertIsNotNone(mirror_source)
        self.assertNotIn("selectGeometry(", mirror_picker.group(0))
        self.assertNotIn("selectGeometry(", mirror_source.group(0))
        self.assertIn("Mirror source retained.", mirror_picker.group(0))
        self.assertIn("Choose a different target or press Esc.", mirror_picker.group(0))
        self.assertIn("editor.mirrorPickPhase='none';", mirror_picker.group(0))
        self.assertIn("editor.mirrorPickPhase='target';", mirror_source.group(0))
        self.assertIn("Mirror source selected:", mirror_source.group(0))

        # A repeat click on the active source is idempotent.  In particular it
        # cannot erase a manually chosen mirror target or disarm its picker.
        same_selection = re.search(r"function sameSelection\(part,face\)\{.*?\n\}", page, re.DOTALL)
        select_geometry = re.search(r"function selectGeometry\(part,face=null\)\{.*?\n\}", page, re.DOTALL)
        start_picker = re.search(r"function startMirrorPicker\(\)\{.*?\n\}", page, re.DOTALL)
        mirror_controls = re.search(r"function updateMirrorControls\(\)\{.*?\n\}", page, re.DOTALL)
        self.assertIsNotNone(same_selection)
        self.assertIsNotNone(select_geometry)
        self.assertIsNotNone(start_picker)
        self.assertIsNotNone(mirror_controls)
        self.assertIn("const unchanged=sameSelection(part,face),samePart=", select_geometry.group(0))
        self.assertIn("if(!unchanged){\n    if(!samePart)storeTransformSession();\n    editor.selection=", select_geometry.group(0))
        self.assertIn("editor.mirrorPickPhase='none';", select_geometry.group(0))
        self.assertIn("Selection retained:", select_geometry.group(0))
        # The picker must be usable before a source exists. It progresses from
        # source to target, and object mode prevents face-only transforms.
        self.assertIn("picker.disabled=!mirrorEnabled;", mirror_controls.group(0))
        self.assertIn("editor.mirrorPickPhase==='source'", mirror_controls.group(0))
        self.assertIn("editor.mirrorPickPhase='source';", start_picker.group(0))
        self.assertIn("editor.mirrorPickPhase='target';", start_picker.group(0))
        self.assertIn("setSelectMode('object')", start_picker.group(0))
        self.assertIn("function pickMirrorGeometry", page)
        self.assertIn("editor.mirrorPickPhase!=='none'", page)
        stop_picker = re.search(r"function stopMirrorPicker\(message\)\{.*?\n\}", page, re.DOTALL)
        source_click = re.search(r"function pickMirrorSource\(event\)\{.*?\n\}", page, re.DOTALL)
        self.assertIsNotNone(stop_picker)
        self.assertIsNotNone(source_click)
        # Esc and a miss cancel or refuse only the active stage; neither path
        # may clear the confirmed source or mirror target.
        self.assertNotIn("editor.selection=", stop_picker.group(0))
        self.assertNotIn("editor.mirrorTarget=", stop_picker.group(0))
        self.assertIn("Mirror source picker refused", source_click.group(0))
        self.assertNotIn("editor.mirrorPickPhase='none'", source_click.group(0))

        # Preview reloads recompute mesh bounds only.  It preserves the user
        # frame and has one deliberate, visible action for recentering.
        self.assertIn("function cameraClipPlanes", page)
        self.assertIn("function recenterCamera", page)
        self.assertIn("loadModel(previewData(),true)", page)
        self.assertIn("if(!preserveCamera||!camera.center)", page)
        self.assertIn("camera.dist=M.defaultDist;zoomf=1.0", page)

        # Session diagnostics are explicit in the viewer but never part of a
        # serialisable WAM edit layer.
        self.assertIn("function logEditor", page)
        self.assertIn("editor.log.length>200", page)
        self.assertIn("Mirror pair picker armed", page)
        self.assertIn("Mirror target picker armed", page)
        self.assertIn("Target picker refused", page)
        self.assertIn("toLocaleTimeString('en-GB'", page)

        # This standalone public viewer deliberately has one language.  Test
        # high-signal French fragments rather than rejecting international
        # names and other valid UTF-8 model content.
        for french in (
            "Édition", "Sélection", "Pipette", "Brouillon", "Journal",
            "Choisis", "Aucune", "Appliquer", "Caméra", "fr-FR",
        ):
            self.assertNotIn(french, page)

        # Display preferences and pending operations never appear in the
        # exported edit document.
        gizmo_op = {"type": "transform", "part": "core", "translation": [0, .1, 0],
                    "rotation": [0, 0, 0], "scale": [1, 1, 1], "pivot": [0, 1, 0],
                    "mirror": False}
        test_layer = self.layer([gizmo_op])
        _model, bones, mesh, _order = build_mesh(self.source)
        wedges.apply_layer(mesh, bones, test_layer)
        self.assertEqual(test_layer["operations"][0]["type"], "transform")
        self.assertNotIn("background", test_layer)
        current_layer = re.search(r"function currentLayer\(\)\{.*?\n\}", page, re.DOTALL)
        self.assertIsNotNone(current_layer)
        self.assertIn("if(editor.draft.length)throw", current_layer.group(0))
        self.assertIn("hasTransformSessions()||editor.paintBrush.stroke", current_layer.group(0))
        serialised_return = current_layer.group(0).split("return ", 1)[-1]
        self.assertNotIn("editor.transformPreview", serialised_return)
        self.assertNotIn("editor.paintBrush", serialised_return)
        self.assertNotIn("editor.recovery", serialised_return)
        self.assertNotIn("viewerBackground", current_layer.group(0))
        self.assertNotIn("editor.log", current_layer.group(0))
        self.assertNotIn("backgroundColor", json.dumps(viewer))

        # No --edits remains the historical pure-WAM output, including no
        # embedded sidecar record in the viewer data.
        plain = os.path.join(self.tmp, "plain", "fixture")
        wcli.compile_model(self.source, plain, "front", quiet=True, width=64, height=80)
        with open(plain + "_viewer.json", encoding="utf-8") as source:
            self.assertNotIn("editLayer", json.load(source))


if __name__ == "__main__":
    unittest.main()
