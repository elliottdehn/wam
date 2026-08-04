"""Procedural texturing: named operators, evaluated per-vertex (phase 1)
or baked into a texel atlas (phase 2). Ops are deterministic, so renders
are reproducible and tunable by parameter edits.

Atlas layout: every part gets a grid cell chart; vertex UVs captured at
mesh generation map into the chart. Triangles are rasterized in UV space
with barycentric world-position interpolation, so 3D noise stays seamless
across charts.
"""
import math

import numpy as np


def _hash1(k, seed=0.0):
    return np.modf(np.sin(k * 127.1 + seed * 53.7) * 43758.5453)[0] % 1.0


def _hash3(p, seed=0.0):
    return np.modf(np.sin(p[..., 0] * 127.1 + p[..., 1] * 311.7 +
                          p[..., 2] * 74.7 + seed * 53.7) * 43758.5453)[0] % 1.0


def value_noise3(P, seed=0.0):
    """Smooth trilinear value noise for points P (n,3), output in [0,1)."""
    Pf = np.floor(P)
    t = P - Pf
    t = t * t * (3 - 2 * t)
    acc = np.zeros(len(P))
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                corner = Pf + np.array([dx, dy, dz])
                h = _hash3(corner, seed)
                w = (np.where(dx, t[:, 0], 1 - t[:, 0]) *
                     np.where(dy, t[:, 1], 1 - t[:, 1]) *
                     np.where(dz, t[:, 2], 1 - t[:, 2]))
                acc += h * w
    return acc


def _part_params(V):
    """(u, v) per vertex: v along the principal axis, u azimuth around it."""
    c = V.mean(axis=0)
    P = V - c
    if len(V) < 3:
        return np.zeros(len(V)), np.zeros(len(V))
    cov = P.T @ P
    w, vec = np.linalg.eigh(cov)
    axis = vec[:, -1]
    if axis[1] < 0:
        axis = -axis
    proj = P @ axis
    lo, hi = proj.min(), proj.max()
    vparam = (proj - lo) / max(hi - lo, 1e-9)
    ref = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    side = np.cross(axis, ref)
    side /= np.linalg.norm(side)
    other = np.cross(axis, side)
    rad = P - np.outer(proj, axis)
    uparam = (np.arctan2(rad @ other, rad @ side) / (2 * np.pi)) + 0.5
    return uparam, vparam


def _ao(V, N):
    """Cheap painted-AO: darken where geometry crowds above the surface."""
    n = len(V)
    occ = np.zeros(n)
    r = 0.06
    step = max(1, n // 900)
    S = V[::step]
    for i in range(n):
        d = S - V[i]
        dist = np.linalg.norm(d, axis=1)
        m = (dist > 1e-6) & (dist < r)
        if not m.any():
            continue
        toward = (d[m] / dist[m][:, None]) @ N[i]
        occ[i] = np.clip(toward, 0, None).sum() / (len(S) * 0.05)
    hi = np.percentile(occ, 95)
    return np.clip(occ / max(hi, 1e-9), 0, 1)


def apply_ops(tex, col, P, u, vy, vp, ao, seed):
    """Evaluate a texture's op stack over sample arrays. Returns colors."""
    col = col.copy()
    mult = np.ones(len(col))
    for op in tex["ops"]:
        kind = op["op"]
        if kind == "gradient":
            axis = op.get("axis", "v")
            p = vy if axis == "v" else (vp if axis == "along" else u)
            mult *= 1 + op.get("from", -0.15) + \
                (op.get("to", 0.10) - op.get("from", -0.15)) * p
        elif kind == "noise":
            scale = max(op.get("scale", 0.2), 1e-3)
            nz = value_noise3(P / scale, seed=seed + op.get("seed", 0))
            mult *= 1 + op.get("amount", 0.1) * (nz * 2 - 1)
        elif kind == "streaks":
            along = op.get("along", "v")
            fa = op.get("scale", 1.0)
            if along == "v":
                Q = np.stack([u * 24 * fa, vp * 3 * fa,
                              np.full(len(col), seed * 7.1)], axis=1)
            else:
                Q = np.stack([u * 3 * fa, vp * 24 * fa,
                              np.full(len(col), seed * 7.1)], axis=1)
            nz = value_noise3(Q, seed=seed)
            mult *= 1 + op.get("amount", 0.08) * (nz * 2 - 1)
        elif kind == "spots":
            scale = max(op.get("scale", 0.1), 1e-3)
            nz = value_noise3(P / scale, seed=seed + 31 + op.get("seed", 0))
            thresh = 1.0 - op.get("density", 0.4) * 0.5
            f = np.clip((nz - thresh) / 0.12, 0, 1)
            sc = np.array(op.get("color", (0.2, 0.2, 0.2)))
            col = col * (1 - f[:, None]) + sc * f[:, None]
        elif kind == "band":
            axis = op.get("axis", "v")
            p = vy if axis == "v" else (vp if axis == "along" else u)
            at, wd = op.get("at", 0.5), op.get("width", 0.1)
            f = np.clip(1 - np.abs(p - at) / (wd / 2 + 1e-9), 0, 1)
            bc = np.array(op.get("color", (0.2, 0.2, 0.2)))
            col = col * (1 - f[:, None]) + bc * f[:, None]
        elif kind == "planks":
            axis = op.get("dir", "u")
            p = u if axis == "u" else vp
            count = op.get("count", 6)
            k = p * count
            frac = k - np.floor(k)
            width = op.get("width", 0.06)
            seam = np.array(op.get("seam", (0.2, 0.15, 0.1)))
            in_seam = (frac < width) | (frac > 1 - width)
            # per-plank value variation
            mult *= 1 + 0.08 * (_hash1(np.floor(k), seed) * 2 - 1)
            col = np.where(in_seam[:, None], seam, col)
        elif kind == "bricks":
            courses = op.get("courses", 8)
            ratio = op.get("ratio", 2.0)
            width = op.get("width", 0.07)
            seam = np.array(op.get("seam", (0.25, 0.22, 0.2)))
            row = vp * courses
            rowi = np.floor(row)
            rfrac = row - rowi
            colk = u * courses * ratio + np.where(rowi % 2 == 0, 0.0, 0.5)
            cfrac = colk - np.floor(colk)
            mult *= 1 + 0.07 * (_hash1(np.floor(colk) * 57 + rowi, seed) * 2 - 1)
            in_seam = (rfrac < width) | (rfrac > 1 - width) | \
                      (cfrac < width * 0.7) | (cfrac > 1 - width * 0.7)
            col = np.where(in_seam[:, None], seam, col)
        elif kind == "ao":
            mult *= 1 - op.get("amount", 0.2) * ao
    return np.clip(col * mult[:, None], 0, 1)


def _vertex_attrs(model, mesh, V, T, M):
    """Per-vertex material, part params, and AO for both bake paths."""
    n = len(V)
    vmat = np.full(n, -1, dtype=int)
    for (a, b, c), mi in zip(T, M):
        for i in (a, b, c):
            if vmat[i] < 0:
                vmat[i] = mi
    Nrm = np.zeros_like(V)
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    for i in range(3):
        np.add.at(Nrm, T[:, i], fn)
    ln = np.linalg.norm(Nrm, axis=1, keepdims=True)
    ln[ln < 1e-12] = 1
    Nrm = Nrm / ln

    needs_ao = any(op["op"] == "ao" for t in model.textures.values()
                   for op in t["ops"])
    ao = _ao(V, Nrm) if needs_ao else np.zeros(n)

    uvarr = np.array(mesh.uvs)
    U = uvarr[:, 0].copy()      # chart u: around the loft/sweep
    VP = uvarr[:, 1].copy()     # chart v: along it
    VY = np.zeros(n)
    part_of = np.full(n, -1, dtype=int)
    for pi, (pname, (v0, v1)) in enumerate(mesh.part_ranges.items()):
        if v1 <= v0:
            continue
        idx = np.arange(v0, v1)
        part_of[idx] = pi
        ylo, yhi = V[idx, 1].min(), V[idx, 1].max()
        VY[idx] = (V[idx, 1] - ylo) / max(yhi - ylo, 1e-9)
    return vmat, ao, U, VP, VY, part_of


def bake_vertex_colors(model, mesh, V, T, M):
    """Phase-1 fallback: bake ops into vertex colors."""
    if not getattr(model, "textures", None):
        return None
    vmat, ao, U, VP, VY, part_of = _vertex_attrs(model, mesh, V, T, M)
    colors = np.zeros((len(V), 3))
    for mi, (mname, rgb) in enumerate(mesh.materials):
        colors[vmat == mi] = rgb
    for pi in np.unique(part_of):
        if pi < 0:
            continue
        for mi in np.unique(vmat[part_of == pi]):
            if mi < 0 or mesh.materials[mi][0] not in model.textures:
                continue
            sel = np.nonzero((part_of == pi) & (vmat == mi))[0]
            tex = model.textures[mesh.materials[mi][0]]
            colors[sel] = apply_ops(tex, colors[sel], V[sel], U[sel],
                                    VY[sel], VP[sel], ao[sel], seed=float(pi))
    return colors


def bake_atlas(model, mesh, V, T, M, res=1024, pad=3):
    """Phase-2: rasterize ops into a texel atlas.

    Returns (atlas_img (res,res,3), atlas_uv (n,2)) or (None, None).
    """
    if not getattr(model, "textures", None):
        return None, None
    uvs = np.array(mesh.uvs)
    vmat, ao, U, VP, VY, part_of = _vertex_attrs(model, mesh, V, T, M)

    parts = [(p, r) for p, r in mesh.part_ranges.items() if r[1] > r[0]]
    cols_n = int(math.ceil(math.sqrt(len(parts))))
    cell = res // cols_n

    # per-vertex atlas UV (glTF convention: v down)
    atlas_uv = np.zeros((len(V), 2))
    cell_of_part = {}
    for pi, (pname, (v0, v1)) in enumerate(parts):
        cx, cy = pi % cols_n, pi // cols_n
        cell_of_part[pi] = (cx, cy)
        inner = cell - 2 * pad
        idx = np.arange(v0, v1)
        atlas_uv[idx, 0] = (cx * cell + pad + uvs[idx, 0] * inner) / res
        atlas_uv[idx, 1] = (cy * cell + pad + (1 - uvs[idx, 1]) * inner) / res

    # texel buffers
    img = np.zeros((res, res, 3))
    filled = np.zeros((res, res), dtype=bool)
    tP = np.zeros((res, res, 3))
    tU = np.zeros((res, res))
    tVP = np.zeros((res, res))
    tVY = np.zeros((res, res))
    tAO = np.zeros((res, res))
    tmat = np.full((res, res), -1, dtype=int)
    tpart = np.full((res, res), -1, dtype=int)

    part_index_of_vert = np.full(len(V), -1, dtype=int)
    for pi, (pname, (v0, v1)) in enumerate(parts):
        part_index_of_vert[v0:v1] = pi

    px = atlas_uv[:, 0] * res
    py = atlas_uv[:, 1] * res
    for ti, (a, b, c) in enumerate(T):
        xs = np.array([px[a], px[b], px[c]])
        ys = np.array([py[a], py[b], py[c]])
        x0, x1 = int(max(0, np.floor(xs.min()))), int(min(res - 1, np.ceil(xs.max())))
        y0, y1 = int(max(0, np.floor(ys.min()))), int(min(res - 1, np.ceil(ys.max())))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5,
                             np.arange(y0, y1 + 1) + 0.5)
        d = ((ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2]))
        if abs(d) < 1e-9:
            continue
        w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
        w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
        w2 = 1 - w0 - w1
        eps = -0.02
        mask = (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
        if not mask.any():
            continue
        yy, xx = np.nonzero(mask)
        yy += y0
        xx += x0
        wa, wb, wc = w0[mask], w1[mask], w2[mask]
        tP[yy, xx] = (wa[:, None] * V[a] + wb[:, None] * V[b] + wc[:, None] * V[c])
        ua, ub, uc = U[a], U[b], U[c]
        if max(ua, ub, uc) - min(ua, ub, uc) > 0.5:   # cylinder seam triangle
            ua, ub, uc = [x + 1.0 if x < 0.5 else x for x in (ua, ub, uc)]
        tU[yy, xx] = (wa * ua + wb * ub + wc * uc) % 1.0
        tVP[yy, xx] = wa * VP[a] + wb * VP[b] + wc * VP[c]
        tVY[yy, xx] = wa * VY[a] + wb * VY[b] + wc * VY[c]
        tAO[yy, xx] = wa * ao[a] + wb * ao[b] + wc * ao[c]
        tmat[yy, xx] = M[ti]
        tpart[yy, xx] = part_index_of_vert[a]
        filled[yy, xx] = True

    # base colors + ops per (part, material)
    for mi, (mname, rgb) in enumerate(mesh.materials):
        img[tmat == mi] = rgb
    for pi in range(len(parts)):
        pmask = (tpart == pi) & filled
        if not pmask.any():
            continue
        for mi in np.unique(tmat[pmask]):
            if mi < 0 or mesh.materials[mi][0] not in model.textures:
                continue
            sel = pmask & (tmat == mi)
            yy, xx = np.nonzero(sel)
            tex = model.textures[mesh.materials[mi][0]]
            img[yy, xx] = apply_ops(tex, img[yy, xx], tP[yy, xx], tU[yy, xx],
                                    tVY[yy, xx], tVP[yy, xx], tAO[yy, xx],
                                    seed=float(pi))

    # dilate charts so bilinear sampling never bleeds background
    for _ in range(pad + 2):
        empty = ~filled
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            src = np.roll(filled, (dy, dx), axis=(0, 1))
            srcimg = np.roll(img, (dy, dx), axis=(0, 1))
            take = empty & src
            img[take] = srcimg[take]
            filled |= take
            empty = ~filled
    return img, atlas_uv
