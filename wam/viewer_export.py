"""Export a compact JSON blob for the standalone HTML viewer and editor."""
import json
import os

import numpy as np

from . import parser as wparser
from . import skeleton as wskel
from . import mesh as wmesh
from . import animation as wanim
from .gltf import mat_to_quat
from . import texture as wtexture
from . import render as wrender
import base64
from . import edits as wedges


def _mat_entry(model, name, rgb):
    """One material for the viewer, carrying its PBR factors when declared.

    Omitted rather than defaulted, so the viewer can keep its existing flat
    look for every model that never asked for metal or roughness.
    """
    entry = dict(name=name, rgb=[round(c, 3) for c in rgb])
    props = (getattr(model, "material_pbr", {}) or {}).get(name)
    if props:
        entry["metal"] = round(float(props.get("metal", 0.0)), 3)
        entry["rough"] = round(float(props.get("rough", 0.9)), 3)
    return entry


def export(path, out_json, samples=24):
    """Compile a .wam file and write its viewer blob."""
    model = wparser.parse_file(path)
    bones, bone_order = wskel.solve(model)
    mesh = wmesh.build(model, bones)
    return export_built(model, bones, bone_order, mesh, out_json, samples,
                        source_path=path)


def _automatic_mirror_name(name, names):
    """Find only conventional pairs; custom asymmetry is editor-authored."""
    if name.endswith(".l"):
        other = name[:-2] + ".r"
    elif name.endswith(".r"):
        other = name[:-2] + ".l"
    elif name.endswith("_l"):
        other = name[:-2] + "_r"
    elif name.endswith("_r"):
        other = name[:-2] + "_l"
    elif name.endswith(".mirror"):
        other = name[:-7]
    else:
        other = name + ".mirror"
    return other if other in names else None


def _dominant_bone(mesh, part, bone_order):
    """Return the stable primary bone used to group geometry in the viewer."""
    bounds = mesh.part_ranges.get(part)
    if bounds is None:
        return None
    weights = {}
    lo, hi = bounds
    for skin in mesh.skin[lo:hi]:
        for bone, weight in skin:
            weights[bone] = weights.get(bone, 0.0) + float(weight)
    if not weights:
        return None
    # ``bone_order`` breaks an equal-weight tie deterministically, rather than
    # letting incidental vertex order make the geometry menu flicker.
    return max(bone_order, key=lambda bone: weights.get(bone.name, 0.0)).name


def _part_metadata(mesh, bone_order, bones):
    """Expose stable part/local-face identities without leaking generator internals.

    The editor stores face references as ``part + local ordinal``.  A global
    triangle index would change as soon as an unrelated part is hidden.
    """
    tri_part, tri_face = [], []
    faces = {name: [] for name in mesh.part_ranges}
    for index, tri in enumerate(mesh.tris):
        owner = next((name for name, (lo, hi) in mesh.part_ranges.items()
                      if all(lo <= vertex < hi for vertex in tri)), None)
        if owner is None:
            owner = "unowned"
            faces.setdefault(owner, [])
        tri_part.append(owner)
        tri_face.append(len(faces[owner]))
        faces[owner].append(index)

    names = set(faces)
    rig_eligibility = wedges.rig_sync_eligibility(mesh, bones)
    parts = []
    for name, face_indexes in faces.items():
        parts.append(dict(id=name, mirror=_automatic_mirror_name(name, names),
                          bone=_dominant_bone(mesh, name, bone_order),
                          rigBones=rig_eligibility.get(name, []),
                          rigSyncEligible=bool(rig_eligibility.get(name)),
                          faces=len(face_indexes)))
    return parts, tri_part, tri_face


def export_built(model, bones, bone_order, mesh, out_json, samples=24,
                 source_path=None, edit_layer=None):
    """Same, for geometry that is already built.

    A composition has no `.wam` file to re-parse — it only exists as the
    result of grafting several together — and a composed character is exactly
    what someone wants to turn around in the viewer.
    """
    V, T, M = mesh.arrays()

    bindex = {b.name: i for i, b in enumerate(bone_order)}
    skin = []
    for sk in mesh.skin:
        total = sum(w for _, w in sk) or 1.0
        e = [[bindex[bn], round(w / total, 3)] for bn, w in sk[:2]]
        if len(e) == 1:
            e.append([-1, 0.0])
        skin.append([e[0][0], e[0][1], e[1][0]])

    anims = []
    for anim in model.anims:
        tracks = {}
        for i in range(samples + 1):
            ph = (i / samples) % 1.0 if anim["loop"] else i / samples
            rots = wanim.anim_rotations_at(model, bones, anim, ph)
            for bn, r in rots.items():
                R = wanim.rot_matrix_for(bones[bn], r)
                q = mat_to_quat(R)
                tracks.setdefault(bindex[bn], [None] * (samples + 1))[i] = [round(x, 4) for x in q]
        for bi, qs in tracks.items():
            tracks[bi] = [(q if q else [0, 0, 0, 1]) for q in qs]
        anims.append(dict(name=anim["name"], dur=anim["dur"],
                          loop=anim["loop"],
                          tracks={str(k): v for k, v in tracks.items()}))

    # See cli.compile_model: an edit can be face-local while an atlas and
    # vertex colours are shared across generated vertices.  Prefer the actual
    # material assignments whenever a layer has changed the mesh.
    atlas = atlas_uv = vcols = None
    if edit_layer is None:
        atlas, atlas_uv = wtexture.bake_atlas(model, mesh, V, T, M)
        vcols = None if atlas is not None else wtexture.bake_vertex_colors(model, mesh, V, T, M)
    parts, tri_part, tri_face = _part_metadata(mesh, bone_order, bones)
    data = dict(
        name=model.name,
        height=model.height,
        verts=[round(float(x), 4) for x in V.reshape(-1)],
        tris=[int(x) for x in T.reshape(-1)],
        triMat=[int(x) for x in M],
        mats=[_mat_entry(model, n, rgb) for n, rgb in mesh.materials],
        skin=skin,
        parts=parts,
        triPart=tri_part,
        triFace=tri_face,
        bones=[dict(n=b.name, p=(bindex[b.parent.name] if b.parent else -1),
                    h=[round(float(x), 4) for x in b.head],
                    t=[round(float(x), 4) for x in b.tail],
                    side=[round(float(x), 4) for x in b.side],
                    up=[round(float(x), 4) for x in b.up]) for b in bone_order],
        anims=anims,
    )
    if source_path:
        data["editSource"] = {"sha256": wedges.source_sha256(source_path),
                              "name": os.path.basename(source_path)}
    if edit_layer is not None:
        data["editLayer"] = edit_layer
    if vcols is not None:
        data["vcols"] = [round(float(c), 3) for c in vcols.reshape(-1)]
    if atlas is not None:
        data["uv"] = [round(float(c), 4) for c in atlas_uv.reshape(-1)]
        data["tex"] = ("data:image/png;base64,"
                       + base64.b64encode(wrender.png_bytes(atlas)).decode())
    with open(out_json, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    return data


if __name__ == "__main__":
    import sys
    d = export(sys.argv[1], sys.argv[2])
    print("exported: %d verts, %d anims" % (len(d["verts"]) // 3, len(d["anims"])))
