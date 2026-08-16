"""WAM compiler CLI.

Usage:
  python3 -m wam.cli model.wam -o out/name [--views front,threequarter,side,...]
                     [--anim walk --frames 6] [--anim-views side,front]
                     [--bones] [--no-gltf] [--width 480 --height 600]
"""
import argparse
import math
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
from . import views as wviews
from . import edits as wedges

# Backwards-compatible alias for callers that imported the old constant.
VIEW_ANGLES = wviews.STANDARD_VIEW_ANGLES


class CompileRequestError(ValueError):
    """Report an invalid compile option before any artifact is written."""


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


def shared_framing(V, views, width, height, fov=28.0, pitch=10.0, margin=1.12):
    """Return one center and distance that contains every requested camera.

    Numeric values retain the legacy yaw-only API and use ``pitch``. A
    :class:`wam.views.ViewSpec` carries its own pitch, which matters for steep
    custom views: fitting those as if they were at 10 degrees can crop them.
    """
    V = np.asarray(V, dtype=float)
    center = (V.min(axis=0) + V.max(axis=0)) / 2
    angles = [
        (view.yaw, view.pitch) if isinstance(view, wviews.ViewSpec)
        else (float(view), pitch)
        for view in views
    ]
    dist = max(wrender.fit_distance(
        V, center, wrender.orbit_basis(yaw, view_pitch), fov,
        width / height, margin)
        for yaw, view_pitch in angles)
    return center, dist


def compile_model(path, out_prefix, views, anim_name=None, frames=6,
                  bones_overlay=False, do_gltf=True, quiet=False,
                  width=480, height=600, anim_views=None, do_viewer=True,
                  light=None, report=None, edits_path=None):
    """Compile one model while preserving the historical four-value return.

    ``report`` is an optional mutable mapping for agent callers that need both
    warning and informational lint lines without scraping human console text.
    """
    if width < 1 or height < 1:
        raise CompileRequestError("--width and --height must be positive")
    views = wviews.parse_views(views)
    anim_views = wviews.parse_views(anim_views or views)
    model = wparser.parse_file(path)
    anim = None
    if anim_name:
        anim = next((item for item in model.anims
                     if item["name"] == anim_name), None)
        if anim is None:
            raise CompileRequestError("no such anim: %s" % anim_name)
        if frames < 1:
            raise CompileRequestError("--frames must be at least 1")
    bones, bone_order = wskel.solve(model)
    mesh = wmesh.build(model, bones)
    wanim.validate_bends(model, bones)
    warnings, infos = wlint.lint(model, bones, mesh)
    edit_layer = None
    if edits_path:
        # Apply only after source linting: a user-owned overlay must not erase
        # a warning about the parametric WAM source it is intended to guide.
        edit_layer = wedges.load_layer(edits_path, path)
        wedges.apply_layer(mesh, bones, edit_layer)
        infos.append("edit layer: %d operation(s) applied" %
                     len(edit_layer["operations"]))
    artifacts = {
        "viewsManifest": None,
        "views": [],
        "sheet": None,
        "gltf": None,
        "viewer": None,
        "viewerData": None,
        "texture": None,
        "bones": None,
        "animation": None,
    }
    if report is not None:
        report.clear()
        # Keep the same dictionary by reference while files are written. An
        # agent must never infer freshness from a path left by an older run.
        report.update(warnings=list(warnings), infos=list(infos),
                      artifacts=artifacts)

    os.makedirs(os.path.dirname(out_prefix) or ".", exist_ok=True)
    V, T, M = mesh.arrays()
    mat_colors = [rgb for _, rgb in mesh.materials]
    props = getattr(model, "material_pbr", {}) or {}
    mat_pbr = [((props[n]["metal"], props[n]["rough"],
                 props[n].get("emit", 0.0)) if n in props else None)
               for n, _ in mesh.materials]
    # Bake after the edit layer, not instead of it.  The atlas is charted from
    # the mesh and rasterized per triangle with a per-texel material, so a
    # per-face repaint is exactly representable: painting 2 of a part's 48
    # faces colours 2% of its texels, not the whole chart.  Skipping the bake
    # cost every authored band, grain and crevice the moment anything was
    # edited, which is a far larger loss than the bleed it was avoiding.
    vcols = None
    atlas, atlas_uv = wtexture.bake_atlas(model, mesh, V, T, M)
    if atlas is not None:
        artifacts["texture"] = out_prefix + "_tex.png"
        wrender.write_png(artifacts["texture"], atlas)

    # A model authored for a night film renders near-black under the default
    # key, and shape cannot be judged in a silhouette-free black mass. The
    # sheet is an inspection tool, so let the author light it.
    lightkw = {}
    if light:
        el, az, amb = light[0], light[1], light[2]
        key = light[3] if len(light) > 3 else 0.95
        fill = light[4] if len(light) > 4 else 0.20
        r_el, r_az = math.radians(el), math.radians(az)
        lightkw["sun"] = (math.cos(r_el) * math.sin(r_az), math.sin(r_el),
                          math.cos(r_el) * math.cos(r_az))
        lightkw["ambient"] = (amb, key, fill)

    imgs = []
    view_paths = []
    center = dist = None
    if len(V) and views:
        center, dist = shared_framing(V, views, width, height)
    for view in views:
        img = wrender.render_view(V, T, M, mat_colors, yaw_deg=view.yaw,
                                  pitch_deg=view.pitch,
                                  width=width, height=height,
                                  center=center, dist=dist,
                                  vert_colors=vcols, uv=atlas_uv, tex=atlas,
                                  shade_group=mesh.shade_group,
                                  mat_pbr=mat_pbr, **lightkw)
        imgs.append(img)
        # Individual panels preserve useful resolution for agents and humans;
        # the historical sheet remains available as a quick overview.
        view_path = "%s_view_%s.png" % (out_prefix, view.id)
        wrender.write_png(view_path, img)
        view_paths.append(view_path)
    artifacts["views"] = list(view_paths)
    sheet_path = None
    if imgs:
        # Keep the historical left-to-right sheet contract. Codex consumes
        # the new full-resolution per-view files, so compatibility does not
        # require changing the dimensions of the established overview.
        sheet = wrender.hstack_views(imgs)
        sheet_path = out_prefix + "_sheet.png"
        wrender.write_png(sheet_path, sheet)
        artifacts["sheet"] = sheet_path
        artifacts["viewsManifest"] = out_prefix + "_views.json"
        # A render manifest is also the durable rebuild recipe for the local
        # editor bridge.  It contains options, never source geometry or edits.
        build_profile = {
            "schemaVersion": 1,
            "views": [view.as_dict() for view in views],
            "width": int(width),
            "height": int(height),
            "animation": ({
                "name": anim_name,
                "frames": int(frames),
                "views": [view.as_dict() for view in anim_views],
            } if anim_name else None),
            "bones": bool(bones_overlay),
            "gltf": bool(do_gltf),
            "viewer": bool(do_viewer),
            "light": ([float(value) for value in light] if light else None),
        }
        wviews.write_render_manifest(
            artifacts["viewsManifest"], path, views, view_paths, sheet_path,
            width, height, center, dist, warnings=warnings, infos=infos,
            build_profile=build_profile)

    if bones_overlay:
        dbg = bone_debug_mesh(bone_order)
        dV, dT, dM = dbg.arrays()
        allV = np.concatenate([V * 0.999, dV])  # shrink body a hair so bones peek out
        allT = np.concatenate([T, dT + len(V)]) if len(dT) else T
        allM = np.concatenate([M, dM + len(mesh.materials)]) if len(dM) else M
        colors = mat_colors + [rgb for _, rgb in dbg.materials]
        bone_views = views[:2]
        bcenter, bdist = shared_framing(allV, bone_views, width, height)
        bimgs = [wrender.render_view(allV, allT, allM, colors,
                                     yaw_deg=v.yaw, pitch_deg=v.pitch,
                                     width=width, height=height,
                                     center=bcenter, dist=bdist)
                 for v in bone_views]
        bsheet = wrender.hstack_views(bimgs)
        artifacts["bones"] = out_prefix + "_bones.png"
        wrender.write_png(artifacts["bones"], bsheet)

    if anim_name:
        posed = []
        for i in range(frames):
            # A loop's phase 1.0 is its phase 0, so sampling must stop short
            # of it; a one-shot must reach 1.0 or its final pose is omitted.
            ph = i / frames if anim["loop"] else i / max(frames - 1, 1)
            rots = wanim.anim_rotations_at(model, bones, anim, ph)
            posed.append(wanim.skin_verts(mesh, bones, bone_order, rots))
        # Frame the whole strip against every pose at once, so the model does
        # not appear to scale as the animation moves it.
        allpose = np.concatenate(posed)
        acenter, adist = shared_framing(
            allpose, anim_views, width, height)
        rows = []
        for view in anim_views:
            rows.append(wrender.hstack_views(
                [wrender.render_view(Vp, T, M, mat_colors,
                                     yaw_deg=view.yaw,
                                     pitch_deg=view.pitch,
                                     width=width, height=height,
                                     center=acenter, dist=adist,
                                     vert_colors=vcols, uv=atlas_uv,
                                     tex=atlas)
                 for Vp in posed]))
        strip = rows[0] if len(rows) == 1 else wrender.vstack_views(rows)
        artifacts["animation"] = out_prefix + "_anim_%s.png" % anim_name
        wrender.write_png(artifacts["animation"], strip)

    if do_gltf:
        tracks = wanim.gltf_tracks(model, bones, bone_order)
        tex_png = wrender.png_bytes(atlas) if atlas is not None else None
        artifacts["gltf"] = out_prefix + ".gltf"
        wgltf.export(artifacts["gltf"], model, bones, bone_order, mesh,
                     tracks, scale=model.height, vert_colors=vcols,
                     uv=atlas_uv, tex_png=tex_png)

    # The viewer page is the deliverable — a turnaround sheet is how the
    # author checks their own work. It used to be gated behind `do_gltf` and
    # baked by a separate script, which meant `--no-gltf` (the flag you reach
    # for while iterating, because it is faster) silently produced nothing
    # anyone could open, and finishing was a step you had to remember. Both
    # are the compiler's job, so it does them.
    if do_viewer:
        from . import viewer_export as wviewer
        artifacts["viewerData"] = out_prefix + "_viewer.json"
        artifacts["viewer"] = out_prefix + ".html"
        wviewer.export_built(model, bones, bone_order, mesh,
                             artifacts["viewerData"], source_path=path,
                             edit_layer=edit_layer)
        from scripts.build_viewer import build as build_viewer_page
        build_viewer_page(artifacts["viewerData"], artifacts["viewer"])

    if not quiet:
        for w in warnings:
            print("WARN: %s" % w)
        for i in infos:
            print("info: %s" % i)
        if do_viewer:
            print("open: %s.html" % out_prefix)
    return model, bones, mesh, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wam")
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--views",
                    default=",".join(wviews.DEFAULT_TURNAROUND),
                    help="turnaround order; a three-quarter rear is included "
                         "because a flat back view puts wings, capes and "
                         "spines edge-on exactly where you need to read them; "
                         "custom views use id:yaw[:pitch]")
    ap.add_argument("--anim", default=None)
    ap.add_argument("--anim-views", default=None,
                    help="views for the animation strip (default: --views); "
                         "one row of frames per view")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--bones", action="store_true")
    ap.add_argument("--edits", default=None, metavar="LAYER.wamedit.json",
                    help="apply a non-destructive manual edit layer to outputs")
    ap.add_argument("--no-gltf", action="store_true")
    ap.add_argument("--no-viewer", action="store_true",
                    help="skip the standalone viewer page (it is the "
                         "deliverable, so this is rarely what you want)")
    ap.add_argument("--light", default=None, metavar="EL,AZ,AMBIENT[,KEY,FILL]",
                    help="light the sheet, e.g. --light 35,140,0.35. A model "
                         "authored for a night film is unreadable under the "
                         "default key, and a black mass hides every defect")
    ap.add_argument("--width", type=int, default=480,
                    help="panel width in pixels (default 480)")
    ap.add_argument("--height", type=int, default=600,
                    help="panel height in pixels; go landscape for models "
                         "longer than they are tall (default 600)")
    args = ap.parse_args(argv)

    out = args.out
    if out is None:
        base = os.path.splitext(os.path.basename(args.input))[0]
        out = os.path.join("out", base)
    try:
        compile_model(args.input, out, args.views.split(","),
                      anim_name=args.anim, frames=args.frames,
                      bones_overlay=args.bones, do_gltf=not args.no_gltf,
                      do_viewer=not args.no_viewer,
                      width=args.width, height=args.height,
                      light=(tuple(float(v) for v in args.light.split(","))
                             if args.light else None),
                      anim_views=(args.anim_views.split(",")
                                  if args.anim_views else None),
                      edits_path=args.edits)
    except (wparser.WamError, wviews.ViewSpecError, CompileRequestError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
