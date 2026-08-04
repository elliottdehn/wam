"""WAM compiler CLI.

Usage:
  python3 -m wam.cli model.wam -o out/name [--views front,threequarter,side,back]
                     [--anim walk --frames 6] [--bones] [--no-gltf]
"""
import argparse
import os
import sys

import numpy as np

from . import parser as wparser
from . import skeleton as wskel
from . import mesh as wmesh
from . import lint as wlint
from . import gltf as wgltf
from . import animation as wanim
from . import render as wrender
from . import texture as wtexture

VIEW_ANGLES = {"front": 0, "threequarter": 38, "side": 90, "back": 180,
               "threequarter_back": 142, "side_r": 270}


def bone_debug_mesh(bone_order):
    """Octahedron per bone, for skeleton overlay renders."""
    out = wmesh.MeshOut()
    mat = out.material("bone_dbg", (0.9, 0.35, 0.2))
    for b in bone_order:
        if b.len <= 0:
            continue
        h, t = b.head, b.tail
        d = b.dir
        side, other = wmesh._frame(d)
        r = max(b.len * 0.07, 0.004)
        mid = h + d * b.len * 0.2
        ring = [mid + side * r, mid + other * r, mid - side * r, mid - other * r]
        ids = [out.add_vert(p, [(b.name, 1.0)]) for p in [h] + ring + [t]]
        for k in range(4):
            k2 = (k + 1) % 4
            out.add_tri(ids[0], ids[1 + k2], ids[1 + k], mat)
            out.add_tri(ids[5], ids[1 + k], ids[1 + k2], mat)
    return out


def compile_model(path, out_prefix, views, anim_name=None, frames=6,
                  bones_overlay=False, do_gltf=True, quiet=False):
    model = wparser.parse_file(path)
    bones, bone_order = wskel.solve(model)
    mesh = wmesh.build(model, bones)
    warnings, infos = wlint.lint(model, bones, mesh)

    os.makedirs(os.path.dirname(out_prefix) or ".", exist_ok=True)
    V, T, M = mesh.arrays()
    mat_colors = [rgb for _, rgb in mesh.materials]
    atlas, atlas_uv = wtexture.bake_atlas(model, mesh, V, T, M)
    vcols = None
    if atlas is not None:
        wrender.write_png(out_prefix + "_tex.png", atlas)

    imgs = []
    for vname in views:
        yaw = VIEW_ANGLES.get(vname, 0)
        img = wrender.render_view(V, T, M, mat_colors, yaw_deg=yaw, vert_colors=vcols, uv=atlas_uv, tex=atlas)
        imgs.append(img)
    if imgs:
        sheet = wrender.hstack_views(imgs)
        wrender.write_png(out_prefix + "_sheet.png", sheet)

    if bones_overlay:
        dbg = bone_debug_mesh(bone_order)
        dV, dT, dM = dbg.arrays()
        allV = np.concatenate([V * 0.999, dV])  # shrink body a hair so bones peek out
        allT = np.concatenate([T, dT + len(V)]) if len(dT) else T
        allM = np.concatenate([M, dM + len(mesh.materials)]) if len(dM) else M
        colors = mat_colors + [rgb for _, rgb in dbg.materials]
        bimgs = [wrender.render_view(allV, allT, allM, colors,
                                     yaw_deg=VIEW_ANGLES.get(v, 0)) for v in views[:2]]
        wrender.write_png(out_prefix + "_bones.png", wrender.hstack_views(bimgs))

    if anim_name:
        anim = next((a for a in model.anims if a["name"] == anim_name), None)
        if anim is None:
            print("no such anim: %s" % anim_name, file=sys.stderr)
        else:
            aimgs = []
            for i in range(frames):
                ph = i / frames
                rots = wanim.anim_rotations_at(model, bones, anim, ph)
                Vp = wanim.skin_verts(mesh, bones, bone_order, rots)
                aimgs.append(wrender.render_view(Vp, T, M, mat_colors, yaw_deg=55, vert_colors=vcols, uv=atlas_uv, tex=atlas))
            wrender.write_png(out_prefix + "_anim_%s.png" % anim_name,
                              wrender.hstack_views(aimgs))

    if do_gltf:
        tracks = wanim.gltf_tracks(model, bones, bone_order)
        tex_png = wrender.png_bytes(atlas) if atlas is not None else None
        wgltf.export(out_prefix + ".gltf", model, bones, bone_order, mesh,
                     tracks, scale=model.height, vert_colors=vcols,
                     uv=atlas_uv, tex_png=tex_png)
        from . import viewer_export as wviewer
        wviewer.export(path, out_prefix + "_viewer.json")

    if not quiet:
        for w in warnings:
            print("WARN: %s" % w)
        for i in infos:
            print("info: %s" % i)
    return model, bones, mesh, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wam")
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--views", default="front,threequarter,side,back")
    ap.add_argument("--anim", default=None)
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--bones", action="store_true")
    ap.add_argument("--no-gltf", action="store_true")
    args = ap.parse_args(argv)

    out = args.out
    if out is None:
        base = os.path.splitext(os.path.basename(args.input))[0]
        out = os.path.join("out", base)
    try:
        compile_model(args.input, out, args.views.split(","),
                      anim_name=args.anim, frames=args.frames,
                      bones_overlay=args.bones, do_gltf=not args.no_gltf)
    except wparser.WamError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
