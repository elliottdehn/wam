"""Export a compact JSON blob for the standalone HTML viewer."""
import json

import numpy as np

from . import parser as wparser
from . import skeleton as wskel
from . import mesh as wmesh
from . import animation as wanim
from .gltf import mat_to_quat
from . import texture as wtexture
from . import render as wrender
import base64


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
    return export_built(model, bones, bone_order, mesh, out_json, samples)


def export_built(model, bones, bone_order, mesh, out_json, samples=24):
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

    atlas, atlas_uv = wtexture.bake_atlas(model, mesh, V, T, M)
    vcols = None if atlas is not None else wtexture.bake_vertex_colors(model, mesh, V, T, M)
    data = dict(
        name=model.name,
        height=model.height,
        verts=[round(float(x), 4) for x in V.reshape(-1)],
        tris=[int(x) for x in T.reshape(-1)],
        triMat=[int(x) for x in M],
        mats=[_mat_entry(model, n, rgb) for n, rgb in mesh.materials],
        skin=skin,
        bones=[dict(n=b.name, p=(bindex[b.parent.name] if b.parent else -1),
                    h=[round(float(x), 4) for x in b.head]) for b in bone_order],
        anims=anims,
    )
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
