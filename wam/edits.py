"""Non-destructive mesh edit layers shared by the compiler and standalone viewer.

The WAM source remains the parametric authority.  A ``.wamedit.json`` layer
only targets the mesh generated from one exact source fingerprint, so a stale
face index can never quietly paint or delete unrelated rebuilt geometry.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

import numpy as np

from .parser import WamError


SCHEMA_VERSION = 1
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_PRIMITIVES = {"box", "sphere", "cylinder", "cone"}


class EditLayerError(WamError):
    """A sidecar edit cannot safely be applied to the generated mesh."""


def source_sha256(path):
    """Return the byte fingerprint that anchors an edit layer to its WAM file."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def new_layer(source_path):
    """Create an empty, exportable layer for one WAM source file."""
    return {"schemaVersion": SCHEMA_VERSION,
            "source": {"sha256": source_sha256(source_path)},
            "operations": []}


def load_layer(path, source_path):
    """Load and fingerprint-check a layer before any compiler artifact is written."""
    try:
        with open(path, encoding="utf-8") as source:
            layer = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise EditLayerError("cannot read edit layer %r: %s" % (path, error))
    if not isinstance(layer, dict) or layer.get("schemaVersion") != SCHEMA_VERSION:
        raise EditLayerError("edit layer needs schemaVersion %d" % SCHEMA_VERSION)
    source = layer.get("source")
    if not isinstance(source, dict) or source.get("sha256") != source_sha256(source_path):
        raise EditLayerError("edit layer was made for a different WAM source; rebase it in the editor")
    if not isinstance(layer.get("operations"), list):
        raise EditLayerError("edit layer needs an operations array")
    return layer


def _vec(value, name, positive=False):
    if not isinstance(value, list) or len(value) != 3:
        raise EditLayerError("%s must be a three-number array" % name)
    try:
        out = np.asarray([float(v) for v in value], dtype=float)
    except (TypeError, ValueError):
        raise EditLayerError("%s must contain numbers" % name)
    if not np.isfinite(out).all() or (positive and (out <= 0).any()):
        raise EditLayerError("%s must contain finite%s values" %
                             (name, " positive" if positive else ""))
    return out


def _color(value):
    if not isinstance(value, str) or not _HEX.match(value):
        raise EditLayerError("color must use #rrggbb")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _part_faces(mesh, part):
    if part not in mesh.part_ranges:
        raise EditLayerError("edit targets unknown part %r" % part)
    lo, hi = mesh.part_ranges[part]
    return [i for i, tri in enumerate(mesh.tris)
            if all(lo <= vertex < hi for vertex in tri)]


def _automatic_mirror_part(mesh, part):
    """Return a conventional counterpart without inferring visual symmetry."""
    if part.endswith(".l"):
        other = part[:-2] + ".r"
    elif part.endswith(".r"):
        other = part[:-2] + ".l"
    elif part.endswith("_l"):
        other = part[:-2] + "_r"
    elif part.endswith("_r"):
        other = part[:-2] + "_l"
    elif part.endswith(".mirror"):
        other = part[:-7]
    else:
        other = part + ".mirror"
    return other if other in mesh.part_ranges else None


def _mirror_part(mesh, part, required, explicit=None):
    """Resolve a reflected transform target, preserving explicit user intent."""
    if part not in mesh.part_ranges:
        raise EditLayerError("edit targets unknown part %r" % part)
    if explicit is not None:
        if not required:
            raise EditLayerError("mirrorPart requires mirror=true")
        if not isinstance(explicit, str) or not explicit:
            raise EditLayerError("mirrorPart must name one target part")
        if explicit == part:
            raise EditLayerError("mirrorPart must differ from the source part")
        if explicit not in mesh.part_ranges:
            raise EditLayerError("mirrorPart targets unknown part %r" % explicit)
        return [part, explicit]
    if not required:
        return [part]
    other = _automatic_mirror_part(mesh, part)
    if other is None:
        raise EditLayerError(
            "part %r has no exact mirrored counterpart; choose a mirror target or disable mirror" % part)
    return [part, other]


def _mat(mesh, color):
    """Reuse a stable synthetic material for every face painted the same colour."""
    name = "edit_%02x%02x%02x" % tuple(round(channel * 255) for channel in color)
    return mesh.material(name, color)


def _rotation(degrees):
    x, y, z = (math.radians(v) for v in degrees)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return np.array([[cy * cz, cz * sx * sy - cx * sz, sx * sz + cx * cz * sy],
                     [cy * sz, cx * cz + sx * sy * sz, cx * sy * sz - cz * sx],
                     [-sy, cy * sx, cx * cy]], dtype=float)


def _transform_frame(op, space_key, basis_key, label):
    """Return a validated world or persisted local edit frame.

    Local axes are saved with the operation instead of being recomputed from a
    later mesh state.  That makes a downloaded layer reproducible even after
    other edits have changed the selected geometry's apparent orientation.
    """
    space = op.get(space_key, "world")
    basis = op.get(basis_key)
    if space == "world":
        if basis is not None:
            raise EditLayerError("%s is only supported by local %s" % (basis_key, label))
        return np.eye(3)
    if space != "local":
        raise EditLayerError("%s must be world or local" % space_key)
    if not isinstance(basis, list) or len(basis) != 3:
        raise EditLayerError("local %s needs three %s axes" % (label, basis_key))
    axes = [_vec(axis, "%s[%d]" % (basis_key, index))
            for index, axis in enumerate(basis)]
    if any(abs(np.linalg.norm(axis) - 1.0) > 1e-4 for axis in axes):
        raise EditLayerError("%s axes must be unit length" % basis_key)
    if any(abs(float(axes[left] @ axes[right])) > 1e-4
           for left, right in ((0, 1), (0, 2), (1, 2))):
        raise EditLayerError("%s axes must be orthogonal" % basis_key)
    frame = np.column_stack(axes)
    if float(np.linalg.det(frame)) < 0.9999:
        raise EditLayerError("%s must be a right-handed frame" % basis_key)
    return frame


def _transform_rotation(op):
    """Return a world-space matrix for a legacy world or explicit local turn.

    A local edit records the rest-frame axes chosen by the viewer.  Persisting
    that frame makes the layer deterministic: the compiler never has to guess
    a part's orientation again after a preceding edit changed its vertices.
    """
    rotation = _vec(op.get("rotation", [0, 0, 0]), "rotation")
    frame = _transform_frame(op, "rotationSpace", "rotationBasis", "rotation")
    return frame @ _rotation(rotation) @ frame.T


def _transform_spec(mesh, part, op, reflected=False, own_pivot=False):
    """Resolve one operation into an affine transform for mesh and rig alike."""
    translation = _vec(op.get("translation", [0, 0, 0]), "translation")
    scale = _vec(op.get("scale", [1, 1, 1]), "scale", positive=True)
    lo, hi = mesh.part_ranges[part]
    default_pivot = np.mean(np.asarray(mesh.verts[lo:hi], dtype=float), axis=0)
    explicit_pivot = "pivot" in op
    pivot = _vec(op.get("pivot", default_pivot.tolist()), "pivot")
    translation_frame = _transform_frame(op, "translationSpace", "translationBasis", "translation")
    scale_frame = _transform_frame(op, "scaleSpace", "scaleBasis", "scale")
    # Preserve the historical result for old layers: world translation and
    # world axis scale are exactly the previous R @ diagonal(scale) mapping.
    translation = translation_frame @ translation
    matrix = _transform_rotation(op) @ scale_frame @ np.diag(scale) @ scale_frame.T
    if reflected:
        # A mirrored rotation is S*R*S, not the same Euler triplet on the
        # opposite limb.  This keeps a paired arm moving away from the centre.
        reflect = np.diag([-1.0, 1.0, 1.0])
        matrix = reflect @ matrix @ reflect
        translation = reflect @ translation
        # A manually chosen asymmetric target keeps its own centre.  Legacy
        # automatic pairs preserve their explicit reflected source pivot.
        if own_pivot:
            pivot = default_pivot
        elif explicit_pivot:
            pivot = reflect @ pivot
    return pivot, matrix, translation


def _transform(mesh, part, op, reflected=False, own_pivot=False):
    pivot, matrix, translation = _transform_spec(mesh, part, op, reflected, own_pivot)
    lo, hi = mesh.part_ranges[part]
    for index in range(lo, hi):
        local = np.asarray(mesh.verts[index], dtype=float) - pivot
        mesh.verts[index] = matrix @ local + pivot + translation
    return pivot, matrix, translation


def rig_sync_eligibility(mesh, bones):
    """Return exclusive bone names per generated part.

    Moving a shared body bone for one cosmetic mesh would tear the rest of a
    character apart.  Only bones weighted by one generated part qualify for
    optional rig synchronization; an empty list deliberately means mesh-only.
    """
    owners = {name: set() for name in bones}
    for part, (lo, hi) in mesh.part_ranges.items():
        for skin in mesh.skin[lo:hi]:
            for bone, weight in skin:
                if weight > 1e-8 and bone in owners:
                    owners[bone].add(part)
    result = {}
    for part, (lo, hi) in mesh.part_ranges.items():
        used = set()
        for skin in mesh.skin[lo:hi]:
            used.update(bone for bone, weight in skin if weight > 1e-8 and bone in bones)
        # A part is synchronizable only when *all* of its weighted bones are
        # exclusive.  Moving just its private root while leaving a shared tip
        # behind would create a newly broken armature rather than a safe fix.
        result[part] = sorted(used) if used and all(owners[bone] == {part} for bone in used) else []
    return result


def _sync_bones(bones, names, pivot, matrix, translation):
    """Apply an approved affine edit to isolated bone frames.

    Bone endpoints and frame axes are transformed together so debug overlays,
    glTF skinning and the standalone viewer all describe the same correction.
    """
    for name in names:
        bone = bones[name]
        head = matrix @ (np.asarray(bone.head, dtype=float) - pivot) + pivot + translation
        tail = matrix @ (np.asarray(bone.tail, dtype=float) - pivot) + pivot + translation
        direction = tail - head
        length = float(np.linalg.norm(direction))
        if length < 1e-9:
            raise EditLayerError("syncRig collapses bone %r" % name)
        direction /= length
        side = matrix @ np.asarray(bone.side, dtype=float)
        side -= float(side @ direction) * direction
        side_len = float(np.linalg.norm(side))
        if side_len < 1e-9:
            raise EditLayerError("syncRig makes bone %r frame degenerate" % name)
        side /= side_len
        # Bone.side, bone.up and bone.dir must stay right-handed after a
        # non-uniform local scale rather than inheriting a skewed basis.
        up = np.cross(direction, side)
        up /= max(float(np.linalg.norm(up)), 1e-12)
        bone.head = head
        bone.dir = direction
        bone.len = length
        bone.side = side
        bone.up = up


def _append_tri(mesh, vertices, faces, material, bone, key, reflect=False):
    """Append one manually owned primitive with a single deliberate skin anchor."""
    start = len(mesh.verts)
    for point in vertices:
        p = np.asarray(point, dtype=float)
        if reflect:
            p[0] *= -1.0
        mesh.add_vert(p, [(bone, 1.0)])
    for a, b, c in faces:
        if reflect:
            mesh.add_tri(start + a, start + c, start + b, material)
        else:
            mesh.add_tri(start + a, start + b, start + c, material)
    mesh.part_ranges[key] = (start, len(mesh.verts))


def _primitive_mesh(kind, center, rotation, scale):
    """Return a compact, deterministic primitive in rest-pose world space."""
    if kind not in _PRIMITIVES:
        raise EditLayerError("primitive must be one of %s" % ", ".join(sorted(_PRIMITIVES)))
    if kind == "box":
        vertices = [(-.5, -.5, -.5), (.5, -.5, -.5), (.5, .5, -.5), (-.5, .5, -.5),
                    (-.5, -.5, .5), (.5, -.5, .5), (.5, .5, .5), (-.5, .5, .5)]
        faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                 (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    else:
        sides = 8
        vertices, faces = [], []
        if kind == "sphere":
            rings = 4
            vertices = [(0, .5, 0), (0, -.5, 0)]
            for ring in range(1, rings):
                theta = math.pi * ring / rings
                for side in range(sides):
                    phi = 2 * math.pi * side / sides
                    vertices.append((.5 * math.sin(theta) * math.cos(phi),
                                     .5 * math.cos(theta),
                                     .5 * math.sin(theta) * math.sin(phi)))
            for side in range(sides):
                nxt = (side + 1) % sides
                faces.append((0, 2 + nxt, 2 + side))
                faces.append((1, 2 + (rings - 2) * sides + side,
                              2 + (rings - 2) * sides + nxt))
            for ring in range(rings - 2):
                a = 2 + ring * sides
                b = a + sides
                for side in range(sides):
                    nxt = (side + 1) % sides
                    faces.extend(((a + side, a + nxt, b + nxt), (a + side, b + nxt, b + side)))
        else:
            vertices = [(0, -.5, 0), (0, .5, 0)]
            for y, radius in ((-.5, .5), (.5, 0 if kind == "cone" else .5)):
                if radius:
                    vertices.extend((radius * math.cos(2 * math.pi * side / sides), y,
                                     radius * math.sin(2 * math.pi * side / sides))
                                    for side in range(sides))
            bottom = 2
            top = None if kind == "cone" else bottom + sides
            for side in range(sides):
                nxt = (side + 1) % sides
                faces.append((0, bottom + side, bottom + nxt))
                if top is None:
                    faces.append((bottom + side, 1, bottom + nxt))
                else:
                    faces.extend(((bottom + side, top + side, top + nxt),
                                  (bottom + side, top + nxt, bottom + nxt),
                                  (1, top + nxt, top + side)))
    R = _rotation(rotation)
    return [R @ (np.asarray(vertex, dtype=float) * scale) + center for vertex in vertices], faces


def _hide_faces(mesh, faces):
    hidden = set(faces)
    mesh.tris = [tri for index, tri in enumerate(mesh.tris) if index not in hidden]
    mesh.tri_mat = [mat for index, mat in enumerate(mesh.tri_mat) if index not in hidden]


def apply_layer(mesh, bones, layer):
    """Mutate a generated MeshOut according to a validated sidecar operation list."""
    eligibility = rig_sync_eligibility(mesh, bones)
    for op in layer["operations"]:
        if not isinstance(op, dict) or not isinstance(op.get("type"), str):
            raise EditLayerError("each edit operation needs a string type")
        kind = op["type"]
        mirrored = bool(op.get("mirror", False))
        transform_fields = {"rotationSpace", "rotationBasis", "translationSpace",
                            "translationBasis", "scaleSpace", "scaleBasis", "syncRig"}
        if kind != "transform" and any(field in op for field in transform_fields):
            raise EditLayerError("transform orientation and syncRig fields are only supported by transform")
        if kind in {"transform", "paint_part", "paint_faces", "hide_part", "hide_faces"}:
            part = op.get("part")
            if not isinstance(part, str):
                raise EditLayerError("%s needs a part name" % kind)
            has_explicit_target = "mirrorPart" in op
            explicit_target = op.get("mirrorPart") if has_explicit_target else None
            if has_explicit_target and kind != "transform":
                raise EditLayerError("mirrorPart is only supported by transform")
            if has_explicit_target and (not isinstance(explicit_target, str) or not explicit_target):
                raise EditLayerError("mirrorPart must name one target part")
            targets = _mirror_part(mesh, part, mirrored, explicit_target)
            if kind == "transform":
                sync_rig = op.get("syncRig", False)
                if not isinstance(sync_rig, bool):
                    raise EditLayerError("syncRig must be true or false")
                if sync_rig:
                    unsafe = [target for target in targets if not eligibility.get(target)]
                    if unsafe:
                        raise EditLayerError(
                            "syncRig is not safe for %s because it has no exclusively owned bones"
                            % ", ".join(repr(target) for target in unsafe))
                for index, target in enumerate(targets):
                    pivot, matrix, translation = _transform(
                        mesh, target, op, reflected=(mirrored and index == 1),
                        own_pivot=(has_explicit_target and index == 1))
                    if sync_rig:
                        _sync_bones(bones, eligibility[target], pivot, matrix, translation)
            elif kind == "paint_part":
                material = _mat(mesh, _color(op.get("color")))
                for target in targets:
                    for face in _part_faces(mesh, target):
                        mesh.tri_mat[face] = material
            elif kind == "paint_faces":
                faces = op.get("faces")
                if not isinstance(faces, list) or not all(isinstance(face, int) and face >= 0 for face in faces):
                    raise EditLayerError("paint_faces needs non-negative local face indexes")
                material = _mat(mesh, _color(op.get("color")))
                for target in targets:
                    target_faces = _part_faces(mesh, target)
                    if any(face >= len(target_faces) for face in faces):
                        raise EditLayerError("paint_faces references a missing face in %r" % target)
                    for face in faces:
                        mesh.tri_mat[target_faces[face]] = material
            else:
                hidden = []
                for target in targets:
                    target_faces = _part_faces(mesh, target)
                    if kind == "hide_faces":
                        faces = op.get("faces")
                        if not isinstance(faces, list) or not all(isinstance(face, int) and face >= 0 for face in faces):
                            raise EditLayerError("hide_faces needs non-negative local face indexes")
                        if any(face >= len(target_faces) for face in faces):
                            raise EditLayerError("hide_faces references a missing face in %r" % target)
                        hidden.extend(target_faces[face] for face in faces)
                    else:
                        hidden.extend(target_faces)
                _hide_faces(mesh, hidden)
        elif kind == "add_primitive":
            ident = op.get("id")
            primitive = op.get("primitive")
            bone = op.get("bone")
            if not isinstance(ident, str) or not ident or any(char.isspace() for char in ident):
                raise EditLayerError("add_primitive needs a compact id")
            if not isinstance(bone, str) or bone not in bones:
                raise EditLayerError("add_primitive names an unknown bone")
            key = "manual:" + ident
            if key in mesh.part_ranges:
                raise EditLayerError("duplicate manual primitive id %r" % ident)
            center = _vec(op.get("center", [0, 0, 0]), "center")
            rotation = _vec(op.get("rotation", [0, 0, 0]), "rotation")
            scale = _vec(op.get("scale", [0.1, 0.1, 0.1]), "scale", positive=True)
            material = _mat(mesh, _color(op.get("color")))
            vertices, faces = _primitive_mesh(primitive, center, rotation, scale)
            _append_tri(mesh, vertices, faces, material, bone, key)
            if mirrored:
                _append_tri(mesh, vertices, faces, material, bone, key + ".mirror", reflect=True)
        else:
            raise EditLayerError("unknown edit operation %r" % kind)
    return mesh
