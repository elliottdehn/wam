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


def fbm3(P, seed=0.0, octaves=1, lacunarity=2.0, gain=0.5):
    """Value noise summed over octaves, normalised back to [0,1).

    One octave of value noise is smooth and blobby, which is exactly the look
    that makes procedural stone read as plastic: there is a single feature size
    and the eye finds it immediately. Summing octaves puts detail at several
    scales at once, so the same `amount=` buys grain instead of blotches.
    """
    if octaves <= 1:
        return value_noise3(P, seed=seed)
    acc = np.zeros(len(P))
    amp, freq, norm = 1.0, 1.0, 0.0
    for o in range(int(octaves)):
        acc += amp * value_noise3(P * freq, seed=seed + 19.0 * o)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return acc / max(norm, 1e-9)


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


def _ao(V, N, r=0.06):
    """Cheap painted-AO plus its opposite, in one sweep over the same
    neighbourhood.

    `occ` is what crowds *above* the surface — a crevice. `exp` is what falls
    *below* it — an edge or a corner, the places a real chisel dresses and real
    weather polishes. Low-poly geometry has hard edges and no way to catch
    light on them, which is most of why untextured facets read as plastic; the
    `wear` op spends this channel on exactly that.
    """
    n = len(V)
    occ = np.zeros(n)
    exp = np.zeros(n)
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
        exp[i] = np.clip(-toward, 0, None).sum() / (len(S) * 0.05)
    hi = np.percentile(occ, 95)
    he = np.percentile(exp, 95)
    return (np.clip(occ / max(hi, 1e-9), 0, 1),
            np.clip(exp / max(he, 1e-9), 0, 1))


def apply_ops(tex, col, P, u, vy, vp, ao, seed, edge=None, tri=None,
              vlen=None, ulen=None):
    """Evaluate a texture's op stack over sample arrays. Returns colors."""
    col = col.copy()
    mult = np.ones(len(col))
    if edge is None:
        edge = np.zeros(len(col))
    if tri is None:
        tri = np.zeros(len(col))

    def _count(op, key_n, key_size, span, default_n):
        """Pattern repeats: a count over the chart, or a real size.

        `courses=16` is a count *per chart*, and a chart covers whatever that
        part happens to span — so one number gives 3 m courses on a tower
        shaft and 0.6 m courses on the gallery above it, and a wall built from
        two parts is visibly built from two parts. Sizes are in model-height
        units, exactly like every other length in the language, so `course=`
        means the same thing on every part of every model.
        """
        if key_size in op and span:
            return max(span / max(op[key_size], 1e-6), 1.0)
        return op.get(key_n, default_n)
    for op in tex["ops"]:
        kind = op["op"]
        if kind == "gradient":
            axis = op.get("axis", "v")
            p = vy if axis == "v" else (vp if axis == "along" else u)
            mult *= 1 + op.get("from", -0.15) + \
                (op.get("to", 0.10) - op.get("from", -0.15)) * p
        elif kind == "noise":
            scale = max(op.get("scale", 0.2), 1e-3)
            nz = fbm3(P / scale, seed=seed + op.get("seed", 0),
                      octaves=op.get("octaves", 1),
                      lacunarity=op.get("lacunarity", 2.0),
                      gain=op.get("gain", 0.5))
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
            if "dir" in op:
                # A band on an arbitrary plane. The chart axes can only give a
                # ring (constant v) or a meridian (constant u), so a fracture
                # running diagonally across a sphere — a great circle, which
                # is what a real one is — was unsayable. `at` and `width` are
                # then distances along `dir` in model-height units, like every
                # other length in the language.
                nrm = np.asarray(op["dir"], dtype=float)
                nrm = nrm / max(np.linalg.norm(nrm), 1e-9)
                p = P @ nrm
            else:
                p = vy if axis == "v" else (vp if axis == "along" else u)
            at, wd = op.get("at", 0.5), op.get("width", 0.1)
            # A band is a straight line, which is right for a painted stripe
            # and wrong for everything the natural world does with one. A
            # fracture across a moon, a mineral vein, a mortar line in a wall
            # that has moved — all wander, and none of them hold their width.
            wander = op.get("wander", 0.0)
            ragged = op.get("ragged", 0.0)
            if wander or ragged:
                sc = max(op.get("scale", 0.25), 1e-3)
                oct_ = int(op.get("octaves", 4))
                sd = seed + 113 + op.get("seed", 0)
                if wander:
                    p = p + wander * (fbm3(P / sc, seed=sd, octaves=oct_) * 2 - 1)
                if ragged:
                    wd = wd * (1 + ragged * (
                        fbm3(P / (sc * 0.35), seed=sd + 7, octaves=oct_) * 2 - 1))
            f = np.clip(1 - np.abs(p - at) / (wd / 2 + 1e-9), 0, 1)
            hard = op.get("hard", 1.0)
            if hard != 1.0:
                f = f ** max(hard, 1e-3)
            bc = np.array(op.get("color", (0.2, 0.2, 0.2)))
            col = col * (1 - f[:, None]) + bc * f[:, None]
        elif kind == "planks":
            axis = op.get("dir", "u")
            p = u if axis == "u" else vp
            count = _count(op, "count", "width_m",
                           ulen if axis == "u" else vlen, 6)
            k = p * count
            frac = k - np.floor(k)
            width = op.get("width", 0.06)
            seam = np.array(op.get("seam", (0.2, 0.15, 0.1)))
            in_seam = (frac < width) | (frac > 1 - width)
            # per-plank value variation
            mult *= 1 + 0.08 * (_hash1(np.floor(k), seed) * 2 - 1)
            col = np.where(in_seam[:, None], seam, col)
        elif kind == "bricks":
            courses = _count(op, "courses", "course", vlen, 8)
            if "course" in op and "length" in op and ulen:
                # ratio is columns-per-course; derive it so a brick is the
                # length asked for, whatever this part's girth happens to be
                ratio = (ulen / max(op["length"], 1e-6)) / max(courses, 1e-6)
            else:
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
        elif kind == "facet":
            # Every triangle takes its own tone. Nothing else in the op set can
            # address a face, and hand-painted low-poly stone is exactly a
            # mosaic of flat planes that disagree slightly about their value —
            # noise cannot imitate it because noise does not know where the
            # facets are.
            mult *= 1 + op.get("amount", 0.07) * (
                _hash1(tri.astype(float), seed + op.get("seed", 0)) * 2 - 1)
        elif kind == "ao":
            mult *= 1 - op.get("amount", 0.2) * ao
        elif kind == "wear":
            # exposed edges lighten (or take a named colour): bare stone
            # polished at the arris, metal rubbed back to white at a corner.
            # `break=` roughens the band so it does not outline every facet
            # like a wireframe, which is what a clean edge mask looks like.
            e = edge
            brk = op.get("break", 0.0)
            if brk:
                scale = max(op.get("scale", 0.12), 1e-3)
                nz = fbm3(P / scale, seed=seed + 71 + op.get("seed", 0),
                          octaves=op.get("octaves", 3))
                e = np.clip(e * (1 + brk * (nz * 2 - 1)), 0, 1)
            e = e ** max(op.get("bias", 1.0), 1e-3)
            if "color" in op:
                wc = np.array(op["color"])
                f = (e * op.get("amount", 0.25))[:, None]
                col = col * (1 - f) + wc * f
            else:
                mult *= 1 + op.get("amount", 0.25) * e
    return np.clip(col * mult[:, None], 0, 1)


def _vertex_attrs(model, mesh, V, T, M):
    """Per-vertex material, part params, and AO for both bake paths."""
    n = len(V)
    # A vertex takes the material most of its triangles use. Generators split
    # vertices at material boundaries, so this is normally unanimous; taking
    # whichever triangle merely happened to come first would silently mis-tint
    # any vertex that is genuinely shared across a color edge.
    vmat = np.full(n, -1, dtype=int)
    if len(T):
        tally = {}
        for (a, b, c), mi in zip(T, M):
            for i in (a, b, c):
                key = (int(i), int(mi))
                tally[key] = tally.get(key, 0) + 1
        best = {}
        for (i, mi), count in tally.items():
            if count > best.get(i, (0, 0))[0]:
                best[i] = (count, mi)
        for i, (_, mi) in best.items():
            vmat[i] = mi
    Nrm = np.zeros_like(V)
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    for i in range(3):
        np.add.at(Nrm, T[:, i], fn)
    ln = np.linalg.norm(Nrm, axis=1, keepdims=True)
    ln[ln < 1e-12] = 1
    Nrm = Nrm / ln

    kinds = {op["op"] for t in model.textures.values() for op in t["ops"]}
    if kinds & {"ao", "wear"}:
        # A radius fixed at 0.06 of model height is tuned for a character,
        # whose parts crowd. On a tower nothing is ever that close to anything
        # and both channels came out identically zero — a texture that looked
        # authored and did nothing at all.
        rad = max([op.get("radius", 0.0)
                   for t in model.textures.values() for op in t["ops"]
                   if op["op"] in ("ao", "wear")] + [0.0])
        if rad <= 0:
            span = float(np.ptp(V, axis=0).max()) if len(V) else 1.0
            rad = max(0.06, 0.05 * span)
        ao, edge = _ao(V, Nrm, r=rad)
    else:
        ao = np.zeros(n)
        edge = np.zeros(n)

    uvarr = np.array(mesh.uvs)
    U = uvarr[:, 0].copy()      # chart u: around the loft/sweep
    VP = uvarr[:, 1].copy()     # chart v: along it
    VY = np.zeros(n)
    part_of = np.full(n, -1, dtype=int)
    # how much *world* a chart covers, so a pattern can be sized in metres
    spans = {}
    for pi, (pname, (v0, v1)) in enumerate(mesh.part_ranges.items()):
        if v1 <= v0:
            continue
        idx = np.arange(v0, v1)
        part_of[idx] = pi
        ylo, yhi = V[idx, 1].min(), V[idx, 1].max()
        VY[idx] = (V[idx, 1] - ylo) / max(yhi - ylo, 1e-9)
        spans[pi] = _chart_spans(V[idx], U[idx], VP[idx])
    return vmat, ao, U, VP, VY, part_of, edge, spans


def _chart_spans(Vp, u, v):
    """(along, around) extent of a chart in model units.

    `v` runs along the part and `u` around it, so the along-extent is how far
    the surface travels between v=0 and v=1, and the around-extent is the mean
    circumference. Both are what a pattern has to be divided by to come out a
    stated size.
    """
    if len(Vp) < 4:
        return (1.0, 1.0)
    lo, hi = v <= np.percentile(v, 10), v >= np.percentile(v, 90)
    along = float(np.linalg.norm(Vp[hi].mean(axis=0) - Vp[lo].mean(axis=0)))
    c = Vp.mean(axis=0)
    d = Vp - c
    axis = Vp[hi].mean(axis=0) - Vp[lo].mean(axis=0)
    nrm = np.linalg.norm(axis)
    if nrm > 1e-9:
        axis = axis / nrm
        d = d - np.outer(d @ axis, axis)
    around = 2 * math.pi * float(np.linalg.norm(d, axis=1).mean())
    return (max(along, 1e-6), max(around, 1e-6))


def bake_vertex_colors(model, mesh, V, T, M):
    """Phase-1 fallback: bake ops into vertex colors."""
    if not getattr(model, "textures", None):
        return None
    vmat, ao, U, VP, VY, part_of, edge, spans = _vertex_attrs(
        model, mesh, V, T, M)
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
                                    VY[sel], VP[sel], ao[sel], seed=float(pi),
                                    edge=edge[sel],
                                    vlen=spans.get(pi, (1, 1))[0],
                                    ulen=spans.get(pi, (1, 1))[1])
    return colors


def bake_atlas(model, mesh, V, T, M, res=1024, pad=3):
    """Phase-2: rasterize ops into a texel atlas.

    Returns (atlas_img (res,res,3), atlas_uv (n,2)) or (None, None).
    """
    if not getattr(model, "textures", None):
        return None, None
    uvs = np.array(mesh.uvs)
    vmat, ao, U, VP, VY, part_of, edge, spans = _vertex_attrs(
        model, mesh, V, T, M)

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
    tED = np.zeros((res, res))
    tTRI = np.zeros((res, res))
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
        # Charts are unwrapped with a duplicated seam column, so u is a plain
        # 0..1 ramp across every triangle; no wrap-around repair is needed.
        tU[yy, xx] = wa * U[a] + wb * U[b] + wc * U[c]
        tVP[yy, xx] = wa * VP[a] + wb * VP[b] + wc * VP[c]
        tVY[yy, xx] = wa * VY[a] + wb * VY[b] + wc * VY[c]
        tAO[yy, xx] = wa * ao[a] + wb * ao[b] + wc * ao[c]
        tED[yy, xx] = wa * edge[a] + wb * edge[b] + wc * edge[c]
        tTRI[yy, xx] = ti
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
                                    seed=float(pi), edge=tED[yy, xx],
                                    tri=tTRI[yy, xx],
                                    vlen=spans.get(pi, (1, 1))[0],
                                    ulen=spans.get(pi, (1, 1))[1])

    # dilate charts so bilinear sampling never bleeds background. Five texels
    # is not enough once a chart is downscaled into a merged sheet, and the
    # failure is black speckle along every seam.
    for _ in range(max(pad + 2, 16)):
        empty = ~filled
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            src = np.roll(filled, (dy, dx), axis=(0, 1))
            srcimg = np.roll(img, (dy, dx), axis=(0, 1))
            take = empty & src
            img[take] = srcimg[take]
            filled |= take
            empty = ~filled
    return img, atlas_uv
