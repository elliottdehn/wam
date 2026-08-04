"""Zone compiler: sculpted heightfield terrain + surface splat rules +
ground splines (roads carve flat, rivers carve wet) + prop scatter.

A `.zone` file is landforms and rules — never grids or meshes:

    zone mulgore
      size 420 320
      water level=1.2
    textures
      texture grass base=#6b7a42 ...
    terrain
      rim height=42 width=75
      opening at=(210,30) width=60
      plateau at=(-110,-70) radius=60 height=26 terrace=4
      hill at=(70,90) radius=45 height=9
      basin at=(40,-10) radius=40 depth=5
      noise scale=55 amount=2.4
    surface
      grass
      rock where slope>30
      sand where shore<4
      dirt where road
    road from=(200,30) via=(80,10) to=(-160,90) width=5.5
    river from=(-180,-40) via=(10,-15) to=(45,-5) width=7 depth=3
    props
      place stone at=(20,25) yaw=10
      scatter pine count=45 slope<25 scale=0.75..1.25 seed=3

Usage: python3 -m wam.zone models/zone/mulgore.zone
"""
import base64
import json
import math
import os
import re
import sys

import numpy as np

from . import parser as wp
from . import skeleton as ws
from . import mesh as wm
from . import render as wr
from . import texture as wtex
from .parser import WamError, _num, _vec, _hex_color, _split_kv

GRID = 220            # heightfield cells along the longer axis
ATLAS = 4096          # scene atlas; terrain chart on top, props/water below
PROP_CELL = 320
TERR_H = 3072


# ----------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------

def parse_zone(path):
    z = dict(name="zone", size=(400.0, 300.0), water=None, camera=None,
             textures={}, terrain=[], surface=[], splines=[], props=[],
             bridges=[])
    section = None
    cur_tex = None
    for line_no, raw in enumerate(open(path).read().splitlines(), 1):
        line = re.split(r"#(?![0-9a-fA-F]{6}\b)", raw, 1)[0].rstrip()
        if not line.strip():
            continue
        tokens = line.split()
        kw = tokens[0]
        if kw == "zone":
            z["name"] = tokens[1] if len(tokens) > 1 else "zone"
            section = "zone"
            continue
        if kw in ("textures", "terrain", "surface", "props"):
            section = kw
            continue
        if kw == "size":
            z["size"] = (_num(tokens[1], line_no, line), _num(tokens[2], line_no, line))
            continue
        if kw == "camera":
            _, kv, _ = _split_kv(tokens[1:], line_no, line)
            z["camera"] = dict(
                at=_vec(kv["at"], line_no, line)[:2],
                look=_vec(kv["look"], line_no, line)[:2],
                height=_num(kv.get("height", "4"), line_no, line))
            continue
        if kw == "water":
            _, kv, _ = _split_kv(tokens[1:], line_no, line)
            z["water"] = _num(kv.get("level", "0"), line_no, line)
            continue
        if kw == "bridge":
            _, kv, _ = _split_kv(tokens[1:], line_no, line)
            z["bridges"].append(dict(
                p1=_vec(kv["from"], line_no, line)[:2],
                p2=_vec(kv["to"], line_no, line)[:2],
                width=_num(kv.get("width", "3.2"), line_no, line)))
            continue
        if kw in ("road", "river"):
            _, kv, _ = _split_kv(tokens[1:], line_no, line)
            pts = []
            for t in tokens[1:]:
                if t.startswith(("from=", "via=", "to=")):
                    pts.append(_vec(t.split("=", 1)[1], line_no, line)[:2])
            sp = dict(kind=kw, pts=pts,
                      width=_num(kv.get("width", "5"), line_no, line),
                      meander=_num(kv.get("meander", "0"), line_no, line),
                      mwave=_num(kv.get("mwave", "70"), line_no, line))
            if kw == "river":
                sp["depth"] = _num(kv.get("depth", "2.5"), line_no, line)
            z["splines"].append(sp)
            continue
        if section == "textures":
            if kw == "texture":
                _, kv, _ = _split_kv(tokens[2:], line_no, line)
                cur_tex = dict(base=_hex_color(kv["base"], line_no, line), ops=[])
                z["textures"][tokens[1]] = cur_tex
            else:
                _, kv, flags = _split_kv(tokens[1:], line_no, line)
                op = dict(op=kw)
                for k, v in kv.items():
                    if v.startswith("#"):
                        op[k] = _hex_color(v, line_no, line)
                    elif re.match(r"^[a-zA-Z_]", v):
                        op[k] = v
                    else:
                        op[k] = _num(v, line_no, line)
                cur_tex["ops"].append(op)
            continue
        if section == "terrain":
            _, kv, _ = _split_kv(tokens[1:], line_no, line)
            op = dict(op=kw)
            for k, v in kv.items():
                op[k] = _vec(v, line_no, line)[:2] if v.startswith("(") else _num(v, line_no, line)
            z["terrain"].append(op)
            continue
        if section == "surface":
            # surface syntax:  <material> [where <cond> [<cond>...]]
            rule = dict(mat=tokens[1] if kw == "surface" else kw, conds=[])
            toks = tokens[2:] if kw == "surface" else tokens[1:]
            for t in toks:
                if t == "where":
                    continue
                m = re.match(r"^(slope|shore|height)([<>])([\d.+-]+)$", t)
                if m:
                    rule["conds"].append((m.group(1), m.group(2), float(m.group(3))))
                elif t == "road":
                    rule["conds"].append(("road", ">", 0.35))
                else:
                    raise WamError("bad surface condition %r" % t, line_no, line)
            z["surface"].append(rule)
            continue
        if section == "props":
            _, kv, flags = _split_kv(tokens[2:], line_no, line)
            p = dict(kind=kw, model=tokens[1])
            if "at" in kv:
                p["at"] = _vec(kv["at"], line_no, line)[:2]
            if "float" in flags:
                p["float"] = True
            for k in ("yaw", "count", "seed", "raise"):
                if k in kv:
                    p[k] = _num(kv[k], line_no, line)
            if "scale" in kv:
                sv = kv["scale"]
                if ".." in sv:
                    a, b = sv.split("..")
                    p["scale"] = (float(a), float(b))
                else:
                    p["scale"] = (float(sv), float(sv))
            for t in tokens[2:]:
                m = re.match(r"^slope<([\d.]+)$", t)
                if m:
                    p["maxslope"] = float(m.group(1))
            z["props"].append(p)
            continue
        raise WamError("unrecognized zone line", line_no, line)
    return z


# ----------------------------------------------------------------------
# heightfield
# ----------------------------------------------------------------------

def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def catmull_rom(pts, step=2.0):
    """Sample a Catmull-Rom spline through 2D points every ~step meters."""
    P = [np.array(pts[0])] + [np.array(p) for p in pts] + [np.array(pts[-1])]
    out = []
    for i in range(len(P) - 3):
        p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
        seg_len = np.linalg.norm(p2 - p1)
        n = max(2, int(seg_len / step))
        for j in range(n):
            t = j / n
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t +
                              (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t +
                              (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(np.array(pts[-1], dtype=float))
    return np.array(out)


PAD = 70.0        # apron: terrain continues beyond the playable rim into fog


def build_heightfield(z):
    W, D = z["size"]
    Wp, Dp = W + 2 * PAD, D + 2 * PAD
    cell = max(Wp, Dp) / GRID
    nx = max(24, int(Wp / cell))
    nz = max(24, int(Dp / cell))
    xs = np.linspace(-Wp / 2, Wp / 2, nx)
    zs = np.linspace(-Dp / 2, Dp / 2, nz)
    XX, ZZ = np.meshgrid(xs, zs)
    H = np.zeros_like(XX)
    for op in z["terrain"]:
        if op["op"] == "base":
            H[:] = op.get("height", 0.0)

    openings = [op for op in z["terrain"] if op["op"] == "opening"]

    def opening_suppression():
        s = np.ones_like(XX)
        for op in openings:
            ox, oz = op["at"]
            w = op.get("width", 40.0)
            d = np.hypot(XX - ox, ZZ - oz)
            s = np.minimum(s, 1 - smoothstep(1 - d / w))
        return s

    for op in z["terrain"]:
        kind = op["op"]
        if kind == "rim":
            h = op.get("height", 40.0)
            w = op.get("width", 60.0)
            d_edge = np.minimum.reduce([XX + W / 2, W / 2 - XX,
                                        ZZ + D / 2, D / 2 - ZZ])
            wob = op.get("wobble", 0.0)
            if wob:
                wsc = max(op.get("wscale", 60.0), 1e-3)
                P = np.stack([XX.ravel() / wsc, np.zeros(XX.size),
                              ZZ.ravel() / wsc], axis=1)
                d_edge = d_edge + np.clip((wtex.value_noise3(P, seed=17.0)
                                   .reshape(H.shape) * 2 - 1) * 2.0, -1, 1) * wob
            hvar = np.ones_like(H)
            P2 = np.stack([XX.ravel() / 90.0, np.zeros(XX.size),
                           ZZ.ravel() / 90.0], axis=1)
            hvar = 1 + 0.22 * (wtex.value_noise3(P2, seed=23.0)
                               .reshape(H.shape) * 2 - 1)
            f = smoothstep(1 - d_edge / w) * opening_suppression()
            H = np.maximum(H, h * hvar * f)
        elif kind in ("hill", "plateau"):
            cx, cz = op["at"]
            r = op.get("radius", 30.0)
            h = op.get("height", 10.0)
            d = np.hypot(XX - cx, ZZ - cz)
            wob = op.get("wobble", 0.0)
            if wob:
                wsc = max(op.get("wscale", 45.0), 1e-3)
                P = np.stack([XX.ravel() / wsc, np.zeros(XX.size),
                              ZZ.ravel() / wsc], axis=1)
                d = d + np.clip((wtex.value_noise3(P, seed=29.0 + cx)
                         .reshape(H.shape) * 2 - 1) * 2.0, -1, 1) * wob
            if kind == "hill":
                f = smoothstep(1 - d / r)
            else:
                f = smoothstep((r - d) / (r * 0.45))
            c = h * f
            step_h = op.get("terrace", 0.0)
            if step_h:
                n = c / step_h
                frac = n - np.floor(n)
                c = (np.floor(n) + smoothstep(np.clip(frac * 1.8 - 0.4, 0, 1))) * step_h
            H = np.maximum(H, c)
        elif kind == "ridge":
            p1 = np.array(op["from"])
            p2 = np.array(op["to"])
            h = op.get("height", 20.0)
            w = op.get("width", 30.0)
            seg = p2 - p1
            L2 = seg @ seg
            t = np.clip(((XX - p1[0]) * seg[0] + (ZZ - p1[1]) * seg[1]) / max(L2, 1e-9), 0, 1)
            d = np.hypot(XX - (p1[0] + seg[0] * t), ZZ - (p1[1] + seg[1] * t))
            H = np.maximum(H, h * smoothstep(1 - d / w) * opening_suppression())
        elif kind == "level":
            # blended flat pad: forces terrain toward a constant height
            cx, cz = op["at"]
            r = op.get("radius", 30.0)
            target = op.get("height", 0.0)
            fall = max(op.get("falloff", r * 0.4), 1e-3)
            d = np.hypot(XX - cx, ZZ - cz)
            wob = op.get("wobble", 0.0)
            if wob:
                wsc = max(op.get("wscale", 45.0), 1e-3)
                P = np.stack([XX.ravel() / wsc, np.zeros(XX.size),
                              ZZ.ravel() / wsc], axis=1)
                d = d + np.clip((wtex.value_noise3(P, seed=37.0 + cx)
                                 .reshape(H.shape) * 2 - 1) * 2.0, -1, 1) * wob
            f = smoothstep((r - d) / fall)
            H = H * (1 - f) + target * f
        elif kind == "basin":
            cx, cz = op["at"]
            r = op.get("radius", 30.0)
            dep = op.get("depth", 4.0)
            d = np.hypot(XX - cx, ZZ - cz)
            wob = op.get("wobble", 0.0)
            if wob:
                wsc = max(op.get("wscale", 45.0), 1e-3)
                P = np.stack([XX.ravel() / wsc, np.zeros(XX.size),
                              ZZ.ravel() / wsc], axis=1)
                d = d + np.clip((wtex.value_noise3(P, seed=31.0 + cx)
                         .reshape(H.shape) * 2 - 1) * 2.0, -1, 1) * wob
            f = smoothstep(1 - d / r)
            H = H - dep * f
        elif kind == "noise":
            sc = max(op.get("scale", 40.0), 1e-3)
            amt = op.get("amount", 2.0)
            P = np.stack([XX.ravel() / sc, np.zeros(XX.size),
                          ZZ.ravel() / sc], axis=1)
            H = H + amt * (wtex.value_noise3(P, seed=op.get("seed", 2.0)).reshape(H.shape) * 2 - 1)
        elif kind in ("opening", "base"):
            pass
        else:
            raise WamError("unknown terrain op %r" % kind)

    # splines: rivers carve first and win; roads flatten but never fill
    # a river channel (they dead-end at the banks — bridges span the gap)
    road_w = np.zeros_like(H)
    river_w = np.zeros_like(H)
    level = z["water"] if z["water"] is not None else -1e9
    cells = np.stack([XX.ravel(), ZZ.ravel()], axis=1)
    ordered = sorted(enumerate(z["splines"]),
                     key=lambda kv: 0 if kv[1]["kind"] == "river" else 1)
    for spi, sp in ordered:
        samples = catmull_rom(sp["pts"])
        amp = sp.get("meander", 0.0)
        if amp:
            seglen = np.linalg.norm(np.diff(samples, axis=0), axis=1)
            s_acc = np.concatenate([[0.0], np.cumsum(seglen)])
            tang = np.gradient(samples, axis=0)
            tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-9)
            perp = np.stack([-tang[:, 1], tang[:, 0]], axis=1)
            wl = max(sp.get("mwave", 70.0), 1e-3)
            # serpentine = deliberate S-oscillation + noise irregularity
            nn = np.clip((wtex.value_noise3(np.stack(
                [s_acc / (wl * 2.3), np.zeros_like(s_acc),
                 np.full_like(s_acc, 3.7 * (spi + 1))], axis=1)) * 2 - 1) * 2.0,
                -1, 1)
            drift = np.clip((wtex.value_noise3(np.stack(
                [s_acc / (wl * 3.1), np.zeros_like(s_acc),
                 np.full_like(s_acc, 9.1 * (spi + 1))], axis=1)) * 2 - 1) * 2.0,
                -1, 1)
            osc = np.sin(2 * np.pi * s_acc / wl + 5.0 * spi + 2.6 * drift)
            wave = np.clip(0.45 * osc + 0.8 * nn, -1, 1)
            # fade the wander near the endpoints so from/to stay honored
            fade = smoothstep(np.minimum(s_acc, s_acc[-1] - s_acc) / (wl * 0.5))
            samples = samples + perp * (wave * amp * fade)[:, None]
        d = np.full(len(cells), 1e9)
        nearest = np.zeros(len(cells), dtype=int)
        for chunk in range(0, len(samples), 64):
            block = samples[chunk:chunk + 64]
            dd = np.linalg.norm(cells[:, None, :] - block[None, :, :], axis=2)
            bmin = dd.min(axis=1)
            barg = dd.argmin(axis=1) + chunk
            upd = bmin < d
            nearest[upd] = barg[upd]
            d[upd] = bmin[upd]
        w = smoothstep(1 - d / sp["width"]).reshape(H.shape)
        if sp["kind"] == "river":
            target = (level if z["water"] is not None else 0.0) - sp["depth"]
            H = np.minimum(H, H * (1 - w) + target * w)
            river_w = np.maximum(river_w, w)
        else:
            w = w * (1 - np.clip(river_w * 1.7, 0, 1))
            samp_h = np.array([H.ravel()[np.argmin(np.linalg.norm(cells - s, axis=1))]
                               for s in samples])
            k = 9
            kern = np.ones(k) / k
            samp_h = np.convolve(np.pad(samp_h, k // 2, mode="edge"), kern, mode="valid")
            target = samp_h[nearest].reshape(H.shape)
            H = H * (1 - w) + target * w
            road_w = np.maximum(road_w, w)

    return xs, zs, H, road_w


# ----------------------------------------------------------------------
# terrain texturing
# ----------------------------------------------------------------------

def bilinear(A, u, v):
    """Sample grid A (nz, nx) at fractional coords u∈[0,1] (x), v (z)."""
    nz, nx = A.shape
    x = np.clip(u * (nx - 1), 0, nx - 1.001)
    y = np.clip(v * (nz - 1), 0, nz - 1.001)
    x0 = x.astype(int)
    y0 = y.astype(int)
    fx = x - x0
    fy = y - y0
    return (A[y0, x0] * (1 - fx) * (1 - fy) + A[y0, x0 + 1] * fx * (1 - fy) +
            A[y0 + 1, x0] * (1 - fx) * fy + A[y0 + 1, x0 + 1] * fx * fy)


def paint_terrain(z, xs, zs, H, road_w, tw, th, row0=None, row1=None):
    """Rasterize surface rules + texture ops into rows [row0,row1) of the
    (th, tw) terrain chart."""
    if row0 is None:
        row0, row1 = 0, th
    W = xs[-1] - xs[0]
    D = zs[-1] - zs[0]
    u = (np.arange(tw) + 0.5) / tw
    v = (np.arange(row0, row1) + 0.5) / th
    UU, VV = np.meshgrid(u, v)
    Ht = bilinear(H, UU, VV)
    Rt = bilinear(road_w, UU, VV)
    gy, gx = np.gradient(Ht, D / th, W / tw)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    level = z["water"] if z["water"] is not None else -1e9
    wet = Ht < level

    # shore distance in texels -> meters
    texel_m = W / tw
    shore = np.full(Ht.shape, 1e9)
    frontier = wet.copy()
    dist = 0.0
    max_shore = max([c[2] for r in z["surface"] for c in r["conds"] if c[0] == "shore"],
                    default=0.0)
    while dist < max_shore + texel_m and frontier.any():
        shore[frontier & (shore > dist)] = dist
        grown = frontier.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            grown |= np.roll(frontier, (dy, dx), axis=(0, 1))
        frontier = grown
        dist += texel_m

    matmap = np.zeros(Ht.shape, dtype=int)
    mats = []
    for rule in z["surface"]:
        mask = np.ones(Ht.shape, dtype=bool)
        for (what, cmp_, val) in rule["conds"]:
            field = {"slope": slope, "shore": shore, "height": Ht, "road": Rt}[what]
            mask &= (field > val) if cmp_ == ">" else (field < val)
        mi = len(mats)
        mats.append(rule["mat"])
        matmap[mask] = mi

    img = np.zeros(Ht.shape + (3,))
    Hmin, Hmax = Ht.min(), Ht.max()
    vy = (Ht - Hmin) / max(Hmax - Hmin, 1e-9)
    Pw = np.stack([xs[0] + UU * W, Ht, zs[0] + VV * D], axis=-1)
    for mi, mname in enumerate(mats):
        sel = matmap == mi
        if not sel.any():
            continue
        tex = z["textures"].get(mname)
        base = tex["base"] if tex else (0.7, 0.5, 0.8)
        img[sel] = base
        if tex and tex["ops"]:
            yy, xx = np.nonzero(sel)
            img[yy, xx] = wtex.apply_ops(
                tex, img[yy, xx], Pw[yy, xx], UU[yy, xx], vy[yy, xx],
                vy[yy, xx], np.zeros(len(yy)), seed=float(mi))
    return img, wet


# ----------------------------------------------------------------------
# compile
# ----------------------------------------------------------------------

def compile_zone(path, out_prefix):
    z = parse_zone(path)
    W, D = z["size"]
    xs, zs, H, road_w = build_heightfield(z)
    Wp = xs[-1] - xs[0]
    Dp = zs[-1] - zs[0]
    nz, nx = H.shape

    mega = np.zeros((ATLAS, ATLAS, 3))
    terr_h = TERR_H
    bands = 6
    OV = 40   # overlap so shore-distance dilation crosses band seams
    for b0 in range(bands):
        r0 = terr_h * b0 // bands
        r1 = terr_h * (b0 + 1) // bands
        r0p = max(0, r0 - OV)
        r1p = min(terr_h, r1 + OV)
        timg, _ = paint_terrain(z, xs, zs, H, road_w, ATLAS, terr_h,
                                row0=r0p, row1=r1p)
        mega[r0:r1] = timg[r0 - r0p:(r0 - r0p) + (r1 - r0)]

    # ---- terrain mesh ----
    allV, allT, allM, all_uv = [], [], [], []
    mats = [("terrain", (0.5, 0.5, 0.4))]
    XX, ZZ = np.meshgrid(xs, zs)
    tv = np.stack([XX.ravel(), H.ravel(), ZZ.ravel()], axis=1)
    tuv = np.stack([(XX.ravel() - xs[0]) / Wp,
                    ((ZZ.ravel() - zs[0]) / Dp) * (terr_h / ATLAS)], axis=1)
    allV.append(tv)
    all_uv.append(tuv)
    tris = []
    for j in range(nz - 1):
        for i in range(nx - 1):
            a = j * nx + i
            b = a + 1
            c = a + nx
            d = c + 1
            tris.append([a, d, b])
            tris.append([a, c, d])
    allT.extend(tris)
    allM.extend([0] * len(tris))
    voff = len(tv)

    # ---- water surface ----
    level = z["water"]
    if level is not None:
        wslot_x, wslot_y = 0, terr_h            # first prop-row cell reserved for water
        wtex_def = z["textures"].get("water")
        cellimg = np.zeros((PROP_CELL, PROP_CELL, 3))
        wu = (np.arange(PROP_CELL) + 0.5) / PROP_CELL
        WU, WV = np.meshgrid(wu, wu)
        Pw = np.stack([xs[0] + WU * Wp, np.full(WU.shape, level),
                       zs[0] + WV * Dp], axis=-1)
        base = wtex_def["base"] if wtex_def else (0.25, 0.42, 0.47)
        cellimg[:] = base
        if wtex_def and wtex_def["ops"]:
            flat = cellimg.reshape(-1, 3)
            cellimg = wtex.apply_ops(wtex_def, flat, Pw.reshape(-1, 3),
                                     WU.ravel(), np.full(WU.size, 0.5), WV.ravel(),
                                     np.zeros(WU.size), seed=99.0).reshape(cellimg.shape)
        mega[wslot_y:wslot_y + PROP_CELL, wslot_x:wslot_x + PROP_CELL] = cellimg
        wmi = len(mats)
        mats.append(("water", tuple(base)))
        wet = H < level
        wverts = []
        wt = []
        for j in range(nz - 1):
            for i in range(nx - 1):
                if not (wet[j, i] or wet[j, i + 1] or wet[j + 1, i] or wet[j + 1, i + 1]):
                    continue
                k = len(wverts) + voff
                for (jj, ii) in ((j, i), (j, i + 1), (j + 1, i + 1), (j + 1, i)):
                    wverts.append([xs[ii], level, zs[jj]])
                wt.append([k, k + 2, k + 1])
                wt.append([k, k + 3, k + 2])
        if wverts:
            wv = np.array(wverts)
            wuv = np.stack([
                (wslot_x + (wv[:, 0] - xs[0]) / Wp * PROP_CELL) / ATLAS,
                (wslot_y + (wv[:, 2] - zs[0]) / Dp * PROP_CELL) / ATLAS], axis=1)
            allV.append(wv)
            all_uv.append(wuv)
            allT.extend(wt)
            allM.extend([wmi] * len(wt))
            voff += len(wv)

    # ---- props ----
    _cache = {}
    slot_i = [1]                                 # slot 0 = water

    def load(name):
        if name not in _cache:
            base_dir = "models/zone" if os.path.exists("models/zone/%s.wam" % name) else "models/town"
            model = wp.parse_file("%s/%s.wam" % (base_dir, name))
            bones, order = ws.solve(model)
            mesh = wm.build(model, bones)
            V, T, M = mesh.arrays()
            atlas, auv = wtex.bake_atlas(model, mesh, V, T, M, res=PROP_CELL)
            if atlas is None:
                atlas = np.zeros((PROP_CELL, PROP_CELL, 3))
                auv = np.full((len(V), 2), 0.5)
                if mesh.materials:
                    atlas[:, :] = mesh.materials[0][1]
            si = slot_i[0]
            slot_i[0] += 1
            per_row = ATLAS // PROP_CELL
            sx = (si % per_row) * PROP_CELL
            sy = terr_h + (si // per_row) * PROP_CELL
            if sy + PROP_CELL > ATLAS:
                raise WamError("too many unique props for the atlas")
            mega[sy:sy + PROP_CELL, sx:sx + PROP_CELL] = atlas
            auv_g = (auv * PROP_CELL + np.array([sx, sy])) / ATLAS
            _cache[name] = (model, mesh, auv_g)
        return _cache[name]

    def height_at(x, zc):
        u = np.array([(x - xs[0]) / Wp])
        v = np.array([(zc - zs[0]) / Dp])
        return float(bilinear(H, u, v)[0])

    def slope_at(x, zc):
        e = 2.0
        return math.degrees(math.atan(math.hypot(
            (height_at(x + e, zc) - height_at(x - e, zc)) / (2 * e),
            (height_at(x, zc + e) - height_at(x, zc - e)) / (2 * e))))

    def road_at(x, zc):
        u = np.array([(x - xs[0]) / Wp])
        v = np.array([(zc - zs[0]) / Dp])
        return float(bilinear(road_w, u, v)[0])

    instances = []
    exclude = []                    # (x, z, radius) — placed props + camera
    if z.get("camera"):
        cx, cz = z["camera"]["at"]
        exclude.append((cx, cz, 10.0))
    for p in z["props"]:
        if p["kind"] == "place":
            px, pz = p["at"]
            exclude.append((px, pz, 7.0))
    for p in z["props"]:
        if p["kind"] == "place":
            x, zc = p["at"]
            lift = p.get("raise", None)
            if p.get("float") and level is not None:
                lift = level - height_at(x, zc) - 0.06
            instances.append((p["model"], x, zc, p.get("yaw", 0.0),
                              p.get("scale", (1.0, 1.0))[0], lift))
        elif p["kind"] == "scatter":
            rng = np.random.default_rng(int(p.get("seed", 1)))
            want = int(p.get("count", 20))
            smin, smax = p.get("scale", (1.0, 1.0))
            maxslope = p.get("maxslope", 30.0)
            placed = []
            tries = 0
            while len(placed) < want and tries < want * 60:
                tries += 1
                x = (rng.random() - 0.5) * (W - 36)
                zc = (rng.random() - 0.5) * (D - 36)
                h = height_at(x, zc)
                if level is not None and h < level + 0.5:
                    continue
                if slope_at(x, zc) > maxslope:
                    continue
                if road_at(x, zc) > 0.25:
                    continue
                if any((x - q[0]) ** 2 + (zc - q[1]) ** 2 < 36 for q in placed):
                    continue
                if any((x - ex) ** 2 + (zc - ez) ** 2 < er * er
                       for ex, ez, er in exclude):
                    continue
                placed.append((x, zc))
                instances.append((p["model"], x, zc, float(rng.random() * 360),
                                  smin + rng.random() * (smax - smin), None))

    mat_index = {("terrain",): 0}
    for name, x, zc, ry, sc, lift in instances:
        model, mesh, auv_g = load(name)
        V, T, M = mesh.arrays()
        a = math.radians(ry)
        R = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0],
                      [-math.sin(a), 0, math.cos(a)]])
        y = height_at(x, zc) + (lift if lift is not None else -0.25 * sc)
        Vw = (V * model.height * sc) @ R.T + np.array([x, y, zc])
        allV.append(Vw)
        all_uv.append(auv_g)
        remap = {}
        for mi, (mn, rgb) in enumerate(mesh.materials):
            key = (name, mn)
            if key not in mat_index:
                mat_index[key] = len(mats)
                mats.append((mn, rgb))
            remap[mi] = mat_index[key]
        for (ta, tb, tc), mi in zip(T, M):
            allT.append([ta + voff, tb + voff, tc + voff])
            allM.append(remap[int(mi)])
        voff += len(V)

    # ---- bridges: generated spans, terrain-seated at both anchors ----
    for bi, br in enumerate(z["bridges"]):
        p1 = np.array(br["p1"], dtype=float)
        p2 = np.array(br["p2"], dtype=float)
        wdt = br["width"]
        h1 = height_at(p1[0], p1[1]) + 0.05
        h2 = height_at(p2[0], p2[1]) + 0.05
        L = float(np.linalg.norm(p2 - p1))
        dir2 = (p2 - p1) / max(L, 1e-9)
        n2 = np.array([-dir2[1], dir2[0]])
        nseg = max(10, int(L / 1.5))
        arch = min(1.0, L * 0.03)
        # solve the arch: bow the deck just enough that no terrain pierces
        # it anywhere across its width (ends stay ground-seated)
        hw0 = wdt / 2.0
        need = arch
        for i in range(1, nseg):
            t = i / nseg
            if not (0.08 < t < 0.92):
                continue
            pos = p1 + dir2 * (L * t)
            y_line = h1 + (h2 - h1) * t
            g = max(height_at(pos[0], pos[1]),
                    height_at(pos[0] + n2[0] * hw0, pos[1] + n2[1] * hw0),
                    height_at(pos[0] - n2[0] * hw0, pos[1] - n2[1] * hw0))
            gap = g + 0.45 - y_line
            if gap > 0:
                need = max(need, gap / max(math.sin(math.pi * t), 0.25))
        if need > 4.0:
            print("WARN: bridge %d needs a %.1fm arch to clear terrain — "
                  "consider moving an anchor" % (bi, need))
        arch = min(need, 4.0)

        # painted atlas cell for the deck wood
        si = slot_i[0]
        slot_i[0] += 1
        per_row = ATLAS // PROP_CELL
        bx = (si % per_row) * PROP_CELL
        by = terr_h + (si // per_row) * PROP_CELL
        if by + PROP_CELL > ATLAS:
            raise WamError("too many props/bridges for the atlas")
        btex = z["textures"].get("bridge") or dict(
            base=(0.43, 0.32, 0.22),
            ops=[dict(op="planks", dir="v", count=max(6, int(L / 1.4)),
                      width=0.08, seam=(0.30, 0.22, 0.15)),
                 dict(op="streaks", along="u", amount=0.08, scale=1.3)])
        cu = (np.arange(PROP_CELL) + 0.5) / PROP_CELL
        CU, CV = np.meshgrid(cu, cu)
        Pc = np.stack([CU * wdt, np.zeros_like(CU), CV * L], axis=-1)
        cell = np.zeros((PROP_CELL, PROP_CELL, 3))
        cell[:] = btex["base"]
        cell = wtex.apply_ops(btex, cell.reshape(-1, 3), Pc.reshape(-1, 3),
                              CU.ravel(), CV.ravel(), CV.ravel(),
                              np.zeros(CU.size), seed=77.0 + bi
                              ).reshape(cell.shape)
        mega[by:by + PROP_CELL, bx:bx + PROP_CELL] = cell

        bmat = len(mats)
        mats.append(("bridge", tuple(btex["base"])))
        bmat2 = len(mats)
        mats.append(("bridge_dark", (0.30, 0.22, 0.15)))

        def cuv(u, v):
            return [(bx + u * PROP_CELL) / ATLAS, (by + v * PROP_CELL) / ATLAS]

        path = []
        worst = 0.0
        worst_at = (0.0, 0.0, 0.0)
        for i in range(nseg + 1):
            t = i / nseg
            pos = p1 + dir2 * (L * t)
            y = h1 + (h2 - h1) * t + arch * math.sin(math.pi * t)
            if t < 0.18 or t > 0.82:
                # approach spans ride the ground: never clip the banks —
                # check across the full deck width, not just the centerline
                hw0 = wdt / 2.0
                g = max(height_at(pos[0], pos[1]),
                        height_at(pos[0] + n2[0] * hw0, pos[1] + n2[1] * hw0),
                        height_at(pos[0] - n2[0] * hw0, pos[1] - n2[1] * hw0))
                y = max(y, g + 0.15)
            path.append((pos[0], y, pos[1]))
            if 0.08 < t < 0.92:
                intrusion = height_at(pos[0], pos[1]) - y
                if intrusion > worst:
                    worst = intrusion
                    worst_at = (pos[0], pos[1], t)
        if worst > 0.4:
            print("WARN: bridge %d: terrain rises %.1fm above the deck at "
                  "(%.0f,%.0f) [t=%.2f] — move an anchor or lower the "
                  "blocking landform"
                  % (bi, worst, worst_at[0], worst_at[1], worst_at[2]))

        bverts, btris, buvs, bmats_l = [], [], [], []

        def strip(offsets, uspan, mat):
            """Extrude a cross-section polygon (side, up) offsets along path."""
            base_i = len(bverts)
            k = len(offsets)
            for i, (px, py, pz) in enumerate(path):
                t = i / nseg
                for j, (so, uo) in enumerate(offsets):
                    bverts.append([px + n2[0] * so, py + uo, pz + n2[1] * so])
                    u = uspan[0] + (uspan[1] - uspan[0]) * (j / max(k - 1, 1))
                    buvs.append(cuv(u, t))
            for i in range(nseg):
                for j in range(k):
                    j2 = (j + 1) % k
                    a0 = base_i + i * k + j
                    b0 = base_i + i * k + j2
                    c0 = base_i + (i + 1) * k + j2
                    d0 = base_i + (i + 1) * k + j
                    btris.append([a0, b0, c0]); bmats_l.append(mat)
                    btris.append([a0, c0, d0]); bmats_l.append(mat)

        hw = wdt / 2.0
        # deck box: corners around the section (ccw): top-l, top-r, bot-r, bot-l
        strip([(-hw, 0.0), (hw, 0.0), (hw, -0.35), (-hw, -0.35)], (0.0, 1.0), bmat)
        # rails
        for sside in (-1, 1):
            so = sside * (hw - 0.10)
            strip([(so - 0.06, 1.05), (so + 0.06, 1.05),
                   (so + 0.06, 0.90), (so - 0.06, 0.90)], (0.0, 0.2), bmat2)
        # end posts
        for (px, py, pz), tdir in ((path[0], 1), (path[-1], -1)):
            for sside in (-1, 1):
                so = sside * (hw - 0.10)
                cx0, cz0 = px + n2[0] * so, pz + n2[1] * so
                base_i = len(bverts)
                for (dy, r) in ((-0.4, 0.10), (1.15, 0.08)):
                    for (ddx, ddz) in ((-r, -r), (r, -r), (r, r), (-r, r)):
                        bverts.append([cx0 + ddx, py + dy, cz0 + ddz])
                        buvs.append(cuv(0.5, 0.5))
                for j in range(4):
                    j2 = (j + 1) % 4
                    btris.append([base_i + j, base_i + j2, base_i + 4 + j2])
                    bmats_l.append(bmat2)
                    btris.append([base_i + j, base_i + 4 + j2, base_i + 4 + j])
                    bmats_l.append(bmat2)

        bv = np.array(bverts)
        allV.append(bv)
        all_uv.append(np.array(buvs))
        for (ta, tb, tc), mi in zip(btris, bmats_l):
            allT.append([ta + voff, tb + voff, tc + voff])
            allM.append(mi)
        voff += len(bv)

    V = np.concatenate(allV)
    T = np.array(allT, dtype=np.int32)
    M = np.array(allM, dtype=np.int32)
    UV = np.concatenate(all_uv)
    colors = [rgb for _, rgb in mats]

    # ---- tiling ground-detail layer (multiplied at high frequency) ----
    DT = 256
    dv, du = np.meshgrid((np.arange(DT) + 0.5) / DT, (np.arange(DT) + 0.5) / DT,
                         indexing="ij")
    dp = np.stack([du.ravel() * 6.0, np.zeros(DT * DT), dv.ravel() * 6.0], axis=1)
    d1 = wtex.value_noise3(dp, seed=51.0)
    d2 = wtex.value_noise3(dp * 3.1, seed=53.0)
    d3 = wtex.value_noise3(dp * 9.7, seed=57.0)
    dl = (0.5 * d1 + 0.32 * d2 + 0.18 * d3).reshape(DT, DT)
    dl = (dl - dl.min()) / max(dl.max() - dl.min(), 1e-9)
    detail = np.repeat(dl[:, :, None], 3, axis=2)

    # ---- outputs ----
    os.makedirs(os.path.dirname(out_prefix) or ".", exist_ok=True)
    sky = ((0.55, 0.68, 0.83), (0.85, 0.87, 0.83))
    fog = dict(color=(0.80, 0.82, 0.80), start=0.45 * max(W, D),
               end=1.35 * max(Wp, Dp), max=0.55)
    # vista: from the zone's camera directive (or a default inside the valley)
    if z.get("camera"):
        cam = z["camera"]
        ex, ez = cam["at"]
        lx, lz = cam["look"]
        eye = (ex, height_at(ex, ez) + cam["height"], ez)
        look = (lx, height_at(lx, lz) + 2.0, lz)
    else:
        ex, ez = 0.38 * W, 0.06 * D
        eye = (ex, height_at(ex, ez) + 4.0, ez)
        look = (-0.25 * W, 6.0, -0.15 * D)
    img = wr.render_view(V, T, M, colors, width=1200, height=650, fov_deg=55,
                         uv=UV, tex=mega, sky=sky, fog=fog, eye=eye, look=look)
    wr.write_png(out_prefix + "_vista.png", img)
    img = wr.render_view(V, T, M, colors, yaw_deg=215, pitch_deg=24,
                         width=1200, height=700, fov_deg=30, margin=0.78,
                         uv=UV, tex=mega, sky=sky)
    wr.write_png(out_prefix + "_high.png", img)
    wr.write_png(out_prefix + "_tex.png", mega)

    data = dict(
        name=z["name"], height=1.0,
        verts=[round(float(v), 3) for v in V.reshape(-1)],
        tris=[int(i) for i in T.reshape(-1)],
        triMat=[int(i) for i in M],
        mats=[dict(name=n, rgb=[round(c, 3) for c in rgb]) for n, rgb in mats],
        skin=[[0, 1.0, -1]] * len(V),
        bones=[dict(n="world", p=-1, h=[0.0, 0.0, 0.0])],
        anims=[],
        uv=[round(float(c), 4) for c in UV.reshape(-1)],
        tex="data:image/png;base64," + base64.b64encode(wr.png_bytes(mega)).decode(),
        sky=[0.62, 0.72, 0.84],
    )
    with open(out_prefix + "_viewer.json", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    np.savez_compressed(out_prefix + "_scene.npz", V=V, T=T, M=M, UV=UV,
                        tex=mega.astype(np.float32),
                        colors=np.array(colors, dtype=np.float32))
    print("zone %s: %d verts, %d tris, %d prop instances"
          % (z["name"], len(V), len(T), len(instances)))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    path = argv[0]
    out = argv[1] if len(argv) > 1 else os.path.join(
        "out", os.path.splitext(os.path.basename(path))[0])
    try:
        compile_zone(path, out)
    except WamError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
