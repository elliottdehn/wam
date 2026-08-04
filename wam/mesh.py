"""Mesh generation: lofts, sweeps, and attachments -> triangles + skin weights.

All geometry is generated in world (rest-pose) space, in model-height units;
the compiler scales to meters at export time.
"""
import math
import numpy as np

from .parser import WamError
from .skeleton import (resolve_dir, rot_axis, chain_bones,
                       resolve_bone_ref, rot_x, rot_y, rot_z, Bone)

STYLE_SIDES = {"chunky": 8, "smooth": 12, "fine": 16}
SHAPE_EXP = {"round": 2.0, "squarish": 3.0, "box": 6.0}


class MeshOut:
    """Accumulates triangles across parts."""

    def __init__(self):
        self.verts = []       # (3,) arrays
        self.tris = []        # (i,j,k) with material index
        self.tri_mat = []
        self.skin = []        # per-vert list of (bone_name, weight)
        self.materials = []   # (name, rgb)
        self._mat_index = {}
        self.part_ranges = {}  # part name -> (vstart, vend)
        self.warnings = []

    def material(self, name, rgb):
        if name not in self._mat_index:
            self._mat_index[name] = len(self.materials)
            self.materials.append((name, rgb))
        return self._mat_index[name]

    def add_vert(self, p, skin):
        self.verts.append(np.asarray(p, dtype=float))
        self.skin.append(skin)
        return len(self.verts) - 1

    def add_tri(self, i, j, k, mat):
        self.tris.append((i, j, k))
        self.tri_mat.append(mat)

    def arrays(self):
        V = np.array(self.verts) if self.verts else np.zeros((0, 3))
        T = np.array(self.tris, dtype=np.int32) if self.tris else np.zeros((0, 3), dtype=np.int32)
        M = np.array(self.tri_mat, dtype=np.int32) if self.tri_mat else np.zeros(0, dtype=np.int32)
        return V, T, M


def _closest_point_tri(p, a, b, c):
    """Closest point on triangle abc to point p (Ericson, RTCD 5.1.5)."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = ab @ ap, ac @ ap
    if d1 <= 0 and d2 <= 0:
        return a
    bp = p - b
    d3, d4 = ab @ bp, ac @ bp
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return a + (d1 / (d1 - d3)) * ab
    cp = p - c
    d5, d6 = ab @ cp, ac @ cp
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return a + (d2 / (d2 - d6)) * ac
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        return b + ((d4 - d3) / ((d4 - d3) + (d5 - d6))) * (c - b)
    denom = 1.0 / (va + vb + vc)
    return a + ab * (vb * denom) + ac * (vc * denom)


def snap_on(out, part, origin, suffix, default_inset=0.012):
    """If the part declares on=<part>, snap origin onto that part's surface
    and sink it inward by inset, so attachments are flush by construction."""
    ref = part.get("on")
    if not ref:
        return origin
    key = None
    for cand in (ref + suffix, ref):
        if cand in out.part_ranges:
            key = cand
            break
    if key is None:
        raise WamError("part %r: on=%r not found (the base part must be "
                       "defined earlier in the file)" % (part["name"], ref))
    v0, v1 = out.part_ranges[key]
    best, best_d = None, 1e18
    for ti, (i, j, k) in enumerate(out.tris):
        if not (v0 <= i < v1):
            continue
        p = _closest_point_tri(np.asarray(origin, dtype=float),
                               out.verts[i], out.verts[j], out.verts[k])
        d = float(np.dot(p - origin, p - origin))
        if d < best_d:
            best, best_d = p, d
    if best is None:
        return origin
    inset = part.get("inset", default_inset)
    u = best - origin
    n = np.linalg.norm(u)
    if n < inset * 0.5:      # already touching/inside: leave it
        return origin
    return best + u / n * inset


def _frame(tangent):
    """Return (side, other) axes for a ring perpendicular to tangent.

    'other' carries the ring depth d.  For mostly-vertical tangents the depth
    axis is the forward (+Z) projection; for mostly-forward tangents it is the
    up (+Y) projection.
    """
    t = tangent / np.linalg.norm(tangent)
    Z = np.array([0.0, 0.0, 1.0])
    Y = np.array([0.0, 1.0, 0.0])
    if abs(t @ Z) < 0.75:
        other = Z - (Z @ t) * t
        other /= np.linalg.norm(other)
        side = np.cross(other, t)   # for t=+Y this gives +X
    else:
        other = Y - (Y @ t) * t
        other /= np.linalg.norm(other)
        side = np.cross(other, t)   # for t=+Z: Y x Z = +X
    side /= np.linalg.norm(side)
    return side, other


def _superellipse(n_sides, exp):
    """Unit cross-section points (cos-like, sin-like) for a superellipse."""
    pts = []
    for i in range(n_sides):
        phi = 2 * math.pi * (i + 0.5) / n_sides
        c, s = math.cos(phi), math.sin(phi)
        pts.append((math.copysign(abs(c) ** (2.0 / exp), c),
                    math.copysign(abs(s) ** (2.0 / exp), s)))
    return pts


def _lerp_rings(rings, t):
    """Interpolate ring params at position t from the sorted ring list."""
    if t <= rings[0]["t"]:
        return dict(rings[0])
    if t >= rings[-1]["t"]:
        return dict(rings[-1])
    for a, b in zip(rings, rings[1:]):
        if a["t"] <= t <= b["t"]:
            f = (t - a["t"]) / max(b["t"] - a["t"], 1e-9)
            out = dict(a)
            for k in ("w", "d", "fwd", "side", "up", "roll"):
                va, vb = a.get(k), b.get(k)
                if va is None and vb is None:
                    continue
                va = va if va is not None else vb
                vb = vb if vb is not None else va
                out[k] = va * (1 - f) + vb * f
            return out
    return dict(rings[-1])


class PathSampler:
    """Arc-length sampling of a polyline with smoothed tangents."""

    def __init__(self, points):
        self.pts = [np.asarray(p, dtype=float) for p in points]
        self.seg_vecs = [b - a for a, b in zip(self.pts, self.pts[1:])]
        self.seg_lens = [np.linalg.norm(v) for v in self.seg_vecs]
        self.total = sum(self.seg_lens)
        if self.total < 1e-9:
            raise WamError("degenerate path (zero length)")
        self.cum = np.concatenate([[0.0], np.cumsum(self.seg_lens)])

    def point(self, t):
        s = np.clip(t, 0, 1) * self.total
        i = int(np.searchsorted(self.cum, s, side="right")) - 1
        i = max(0, min(i, len(self.seg_vecs) - 1))
        f = (s - self.cum[i]) / max(self.seg_lens[i], 1e-9)
        return self.pts[i] + self.seg_vecs[i] * f

    def tangent(self, t):
        eps = 0.02
        a = self.point(max(0.0, t - eps))
        b = self.point(min(1.0, t + eps))
        v = b - a
        n = np.linalg.norm(v)
        if n < 1e-9:
            v = self.seg_vecs[0]
            n = np.linalg.norm(v)
        return v / n

    def seg_index(self, t):
        s = np.clip(t, 0, 1) * self.total
        i = int(np.searchsorted(self.cum, s, side="right")) - 1
        return max(0, min(i, len(self.seg_vecs) - 1))

    def seg_frac(self, t):
        """(segment index, fraction along that segment)."""
        i = self.seg_index(t)
        s = np.clip(t, 0, 1) * self.total
        return i, (s - self.cum[i]) / max(self.seg_lens[i], 1e-9)


def _chain_skin(sampler, chain, t):
    """Skin weights for a ring at path position t: blend near joints."""
    i, f = sampler.seg_frac(t)
    bone = chain[min(i, len(chain) - 1)]
    blend = 0.18
    if f < blend and i > 0:
        w = 0.5 + 0.5 * (f / blend)
        return [(bone.name, w), (chain[i - 1].name, 1 - w)]
    if f > 1 - blend and i < len(chain) - 1:
        w = 0.5 + 0.5 * ((1 - f) / blend)
        return [(bone.name, w), (chain[i + 1].name, 1 - w)]
    return [(bone.name, 1.0)]


def _emit_tube(out, centers, axes, params, mats, skins, n_sides,
               cap_start, cap_end, flip=False):
    """Build a tube from per-ring data.

    centers: list of (3,), axes: list of (side, other, tangent),
    params: list of dicts with w,d,exp,roll, mats: material idx per band,
    skins: per-ring skin list.
    """
    nrings = len(centers)
    t_first = len(out.tris)
    ring_ids = []
    for ri in range(nrings):
        c = centers[ri]
        side, other, tang = axes[ri]
        p = params[ri]
        exp = p.get("exp", 2.0)
        roll = p.get("roll", 0.0)
        if roll:
            R = rot_axis(tang, roll)
            side, other = R @ side, R @ other
        w2, d2 = p["w"] / 2.0, p["d"] / 2.0
        skin_at = skins[ri] if callable(skins[ri]) else (lambda pt, sk=skins[ri]: sk)
        if w2 < 1e-6 and d2 < 1e-6:
            vid = out.add_vert(c, skin_at(c))
            ring_ids.append([vid] * n_sides)
            continue
        ids = []
        for (cx, sy) in _superellipse(n_sides, exp):
            pt = c + side * (cx * w2) + other * (sy * d2)
            ids.append(out.add_vert(pt, skin_at(pt)))
        ring_ids.append(ids)

    def quad(a, b, c, d, mat):
        if flip:
            out.add_tri(a, c, b, mat)
            out.add_tri(a, d, c, mat)
        else:
            out.add_tri(a, b, c, mat)
            out.add_tri(a, c, d, mat)

    for ri in range(nrings - 1):
        A, B = ring_ids[ri], ring_ids[ri + 1]
        mat = mats[ri]
        for k in range(n_sides):
            k2 = (k + 1) % n_sides
            if A[k] == A[k2] and B[k] == B[k2]:
                continue
            quad(A[k], A[k2], B[k2], B[k], mat)

    def cap(ring, center, skin, mat, direction, style):
        if style == "none" or len(set(ring)) < 3:
            return
        _, _, tang = direction
        if style == "dome":
            apex_off = tang * 0.0  # dome handled as raised fan below
        # flat fan (dome = raised fan)
        raise_amt = 0.0
        if style in ("dome", "point"):
            # apex raised along tangent by 40% of mean radius
            pts = [out.verts[i] for i in ring]
            r = np.mean([np.linalg.norm(p - center) for p in pts])
            raise_amt = r * (0.45 if style == "dome" else 1.0)
        apex = center + direction[2] * raise_amt
        cid = out.add_vert(apex, skin(apex) if callable(skin) else skin)
        for k in range(n_sides):
            k2 = (k + 1) % n_sides
            if ring[k] == ring[k2]:
                continue
            if flip:
                out.add_tri(ring[k], ring[k2], cid, mat)
            else:
                out.add_tri(ring[k2], ring[k], cid, mat)

    # start cap faces along -tangent, end cap along +tangent
    s_side, s_other, s_tang = axes[0]
    e_side, e_other, e_tang = axes[-1]
    cap(ring_ids[0], centers[0], skins[0], mats[0], (s_side, s_other, -s_tang), cap_start)
    cap(ring_ids[-1], centers[-1], skins[-1], mats[-1], (e_side, e_other, e_tang), cap_end)

    # orient outward: vote with band-face normals vs radial direction from the
    # ring-center polyline (signed volume is unreliable for open tubes)
    vote = 0.0
    for ti in range(t_first, len(out.tris)):
        i, j, k = out.tris[ti]
        a, b, c = out.verts[i], out.verts[j], out.verts[k]
        n = np.cross(b - a, c - a)
        fc = (a + b + c) / 3.0
        best = None
        for cc in centers:
            d2 = float(np.dot(fc - cc, fc - cc))
            if best is None or d2 < best[0]:
                best = (d2, cc)
        radial = fc - best[1]
        vote += float(np.dot(n, radial))
    # mirrored parts are reflected after emission, which reverses orientation:
    # their vote must come out negative here to be outward after reflection
    if (vote < 0) != flip:
        for ti in range(t_first, len(out.tris)):
            i, j, k = out.tris[ti]
            out.tris[ti] = (i, k, j)
    return ring_ids


def build_loft(out, model, bones, part, suffix="", reflect=False):
    n_sides = int(part.get("sides", STYLE_SIDES.get(model.style, 8)))
    default_exp = SHAPE_EXP.get(part.get("shape", "round"), 2.0)
    base_mat = part.get("material") or "default"
    rings = sorted(part["rings"], key=lambda r: r["t"])
    if len(rings) < 2:
        raise WamError("loft %r needs at least 2 rings" % part["name"])

    if "chain" in part:
        chain = chain_bones(bones, part["chain"][0], part["chain"][1], suffix)
        pts = [chain[0].head] + [b.tail for b in chain]
        sampler = PathSampler(pts)
        skin_for = lambda t: _chain_skin(sampler, chain, t)
    elif "bone" in part:
        host = resolve_bone_ref(bones, part["bone"], suffix)
        at = part.get("at", 1.0)
        origin = host.point_at(at)
        origin = origin + np.array(part.get("offset", (0, 0, 0)), dtype=float)
        first_w = part["rings"][0].get("w", 0.05) if part["rings"] else 0.05
        origin = snap_on(out, part, origin, suffix,
                         default_inset=max(0.012, first_w * 0.25))
        d = resolve_dir(part.get("dir", "up"), part.get("pitch", 0.0),
                        part.get("yaw", 0.0), part.get("tilt", 0.0))
        length = part.get("len")
        if length is None:
            raise WamError("loft %r on a bone needs len=" % part["name"])
        sampler = PathSampler([origin, origin + d * length])
        skin_for = lambda t: [(host.name, 1.0)]
    else:
        raise WamError("loft %r needs bones=a..b or bone=" % part["name"])

    # sample a ring at every authored ring position
    centers, axes, params, mats, skins = [], [], [], [], []
    for r in rings:
        t = r["t"]
        c = sampler.point(t)
        tang = sampler.tangent(t)
        side, other = _frame(tang)
        c = c + other * r.get("fwd", 0.0) + side * r.get("side", 0.0)
        if "up" in r:
            c = c + np.array([0.0, r["up"], 0.0])
        w = r["w"]
        d = r.get("d", w)
        if r.get("tip"):
            w = d = 0.0
        exp = SHAPE_EXP.get(r.get("shape", ""), default_exp)
        centers.append(c)
        axes.append((side, other, tang))
        params.append(dict(w=w, d=d, exp=exp, roll=r.get("roll", 0.0)))
        mats.append(out.material(r.get("material", base_mat),
                                 model.palette.get(r.get("material", base_mat), (0.7, 0.5, 0.8))))
        base_skin = skin_for(t)
        if "follow" in r:
            # cloth skinning: ring verts partially follow a mirrored bone pair,
            # split left/right by vertex x. follow=thigh:0.8
            spec = r["follow"]
            fname, _, ffrac = spec.partition(":")
            frac = float(ffrac) if ffrac else 0.8
            def find(nm):
                if nm in bones:
                    return bones[nm]
                return None
            bl = find(fname + ".l") or find(fname)
            br = find(fname + ".r") or find(fname)
            if bl is None:
                raise WamError("follow bone %r not found" % fname)
            half_w = max(w, 1e-6) / 2.0

            def cloth_skin(pt, bl=bl, br=br, frac=frac, base=base_skin, hw=half_w):
                lat = min(1.0, abs(pt[0]) / (0.5 * hw))
                fr = frac * lat
                if fr < 1e-3:
                    return base
                fb = bl if pt[0] >= 0 else br
                bn, _ = base[0]
                return [(fb.name, fr), (bn, 1.0 - fr)]
            skins.append(cloth_skin)
        else:
            skins.append(base_skin)

    # fold check: a ring wider than the bend's reach doubles the tube back
    for i in range(len(centers) - 1):
        t1, t2 = axes[i][2], axes[i + 1][2]
        c = float(np.clip(t1 @ t2, -1.0, 1.0))
        if c > 0.985:
            continue
        theta = math.acos(c)
        L = float(np.linalg.norm(centers[i + 1] - centers[i]))
        reach = L / (2.0 * math.sin(theta / 2.0) + 1e-9)
        rmax = max(params[i]["w"], params[i]["d"],
                   params[i + 1]["w"], params[i + 1]["d"]) / 2.0
        if rmax > reach:
            out.warnings.append(
                "loft %r folds between rings %.2f and %.2f (path bends %.0f\u00b0 "
                "while the ring radius %.3f exceeds the bend reach %.3f) — the "
                "surface doubles back and looks like missing faces; split the "
                "loft at the joint or shrink the rings"
                % (part["name"], rings[i]["t"], rings[i + 1]["t"],
                   math.degrees(theta), rmax, reach))

    v0 = len(out.verts)
    _emit_tube(out, centers, axes, params, mats, skins, n_sides,
               part.get("cap_start", "flat"), part.get("cap_end", "flat"),
               flip=reflect)
    if reflect:
        _reflect_range(out, v0, suffix)
    out.part_ranges[part["name"] + (".r" if reflect else suffix)] = (v0, len(out.verts))


def build_sweep(out, model, bones, part, suffix="", reflect=False):
    n_sides = max(6, int(part.get("sides", STYLE_SIDES.get(model.style, 8)) - 2))
    base_mat = part.get("material") or "default"
    mat = out.material(base_mat, model.palette.get(base_mat, (0.7, 0.5, 0.8)))
    if not part["segs"]:
        raise WamError("sweep %r has no segs" % part["name"])
    host = resolve_bone_ref(bones, part.get("bone"), suffix)
    origin = host.point_at(part.get("at", 1.0))
    origin = origin + np.array(part.get("offset", (0, 0, 0)), dtype=float)
    origin = snap_on(out, part, origin, suffix,
                     default_inset=max(0.012, part["segs"][0]["r"] * 0.8))
    d = resolve_dir(part.get("dir", "up"), part.get("pitch", 0.0),
                    part.get("yaw", 0.0), part.get("tilt", 0.0))

    # local frame transported along the sweep
    side, other = _frame(d)
    tang = d.copy()
    centers, axes, params, mats, skins = [], [], [], [], []
    skin = [(host.name, 1.0)]

    def push(c, r):
        centers.append(c.copy())
        axes.append((side.copy(), other.copy(), tang.copy()))
        params.append(dict(w=2 * r, d=2 * r, exp=2.0))
        mats.append(mat)
        skins.append(skin)

    Y = np.array([0.0, 1.0, 0.0])
    Z = np.array([0.0, 0.0, 1.0])
    pos = origin.copy()
    push(pos, part["segs"][0]["r"])
    for seg in part["segs"]:
        # bends are world-referenced: up=+deg tips the tangent toward +Y,
        # fwd=+deg toward +Z (negative values bend the other way)
        R = np.eye(3)
        if seg.get("up"):
            axis = np.cross(tang, Y)
            R = rot_axis(axis, seg["up"]) @ R
        if seg.get("fwd"):
            axis = np.cross(tang, Z)
            R = rot_axis(axis, seg["fwd"]) @ R
        if seg.get("curl"):
            # planar bend about the transported side axis: stable for rings
            # and spirals where up/fwd axes would degenerate
            R = rot_axis(side, seg["curl"]) @ R
        if seg.get("roll"):
            R = rot_axis(tang, seg["roll"]) @ R
        tang = R @ tang
        side = R @ side
        other = R @ other
        pos = pos + tang * seg["len"]
        r = 0.0 if seg.get("tip") else seg["r"]
        push(pos, r)

    v0 = len(out.verts)
    _emit_tube(out, centers, axes, params, mats, skins, n_sides,
               part.get("cap_start", "flat"),
               "none" if part["segs"][-1].get("tip") else part.get("cap_end", "dome"),
               flip=reflect)
    if reflect:
        _reflect_range(out, v0, suffix)
    out.part_ranges[part["name"] + (".r" if reflect else suffix)] = (v0, len(out.verts))


def _reflect_range(out, v0, suffix):
    """Reflect verts added since v0 across X=0 and remap skin .l -> .r."""
    for i in range(v0, len(out.verts)):
        out.verts[i] = out.verts[i] * np.array([-1.0, 1.0, 1.0])
        out.skin[i] = [(n[:-2] + ".r" if n.endswith(".l") else n, w)
                       for (n, w) in out.skin[i]]


def build_attach(out, model, bones, part, suffix="", reflect=False):
    prim = part.get("prim", "sphere")
    base_mat = part.get("material") or "default"
    mat = out.material(base_mat, model.palette.get(base_mat, (0.7, 0.5, 0.8)))
    host = resolve_bone_ref(bones, part.get("bone"), suffix)
    at = part.get("at", 0.0)
    origin = host.point_at(at) + np.array(part.get("offset", (0, 0, 0)), dtype=float)
    origin = snap_on(out, part, origin, suffix)
    size = part.get("size", 0.05)
    skin = [(host.name, 1.0)]
    v0 = len(out.verts)

    if prim in ("sphere", "eye"):
        lats, lons = (3, 6) if prim == "eye" else (4, 8)
        w = part.get("w", size)
        h = part.get("h", size)
        d = part.get("d", size)
        ids = []
        top = out.add_vert(origin + np.array([0, h / 2, 0]), skin)
        bot = out.add_vert(origin - np.array([0, h / 2, 0]), skin)
        for li in range(1, lats):
            th = math.pi * li / lats
            row = []
            for lo in range(lons):
                ph = 2 * math.pi * lo / lons
                p = origin + np.array([math.sin(th) * math.cos(ph) * w / 2,
                                       math.cos(th) * h / 2,
                                       math.sin(th) * math.sin(ph) * d / 2])
                row.append(out.add_vert(p, skin))
            ids.append(row)
        for lo in range(lons):
            lo2 = (lo + 1) % lons
            out.add_tri(top, ids[0][lo2], ids[0][lo], mat)
            out.add_tri(bot, ids[-1][lo], ids[-1][lo2], mat)
        for a, b in zip(ids, ids[1:]):
            for lo in range(lons):
                lo2 = (lo + 1) % lons
                out.add_tri(a[lo], a[lo2], b[lo2], mat)
                out.add_tri(a[lo], b[lo2], b[lo], mat)

    elif prim in ("box", "hoof"):
        w = part.get("w", size * (1.0 if prim == "box" else 0.9))
        h = part.get("h", size)
        d = part.get("d", size * (1.0 if prim == "box" else 1.15))
        taper = part.get("taper", 1.0 if prim == "box" else 1.25)
        # top face centered at origin, bottom face h below, scaled by taper,
        # hoof bottom also shifted slightly forward
        shift = d * 0.18 if prim == "hoof" else 0.0
        tw, td = w / 2, d / 2
        bw, bd = tw * taper, td * taper
        c = [origin + np.array([sx * tw, 0, sz * td]) for sx, sz in
             ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        c += [origin + np.array([sx * bw, -h, sz * bd + shift]) for sx, sz in
              ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        ids = [out.add_vert(p, skin) for p in c]
        faces = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1),
                 (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
        for (a, b, cc, dd) in faces:
            out.add_tri(ids[a], ids[b], ids[cc], mat)
            out.add_tri(ids[a], ids[cc], ids[dd], mat)
    else:
        raise WamError("unknown attach kind %r" % prim)

    if reflect:
        _reflect_range(out, v0, suffix)
    out.part_ranges[part["name"] + (".r" if reflect else suffix)] = (v0, len(out.verts))


def _fix_winding(out, t0, t1):
    """Flip triangle winding if the part's signed volume is negative."""
    if t1 <= t0:
        return
    tris = out.tris[t0:t1]
    vs = np.array([out.verts[i] for tri in tris for i in tri]).reshape(-1, 3, 3)
    centroid = vs.reshape(-1, 3).mean(axis=0)
    a, b, c = vs[:, 0] - centroid, vs[:, 1] - centroid, vs[:, 2] - centroid
    vol = np.einsum('ij,ij->i', a, np.cross(b, c)).sum()
    if vol < 0:
        for i in range(t0, t1):
            x, y, z = out.tris[i]
            out.tris[i] = (x, z, y)


def build(model, bones):
    out = MeshOut()
    builders = {"loft": build_loft, "sweep": build_sweep, "attach": build_attach}
    pending = [None, 0]   # [group dict, vert start]

    def finalize_group():
        g, v0 = pending
        if g is None:
            return
        host = resolve_bone_ref(bones, g["bone"])
        if g.get("dir"):
            # aim the group's local +Y along the resolved direction, then spin
            v = resolve_dir(g["dir"], g.get("pitch", 0.0), g.get("yaw", 0.0),
                            g.get("tilt", 0.0))
            y_axis = np.array([0.0, 1.0, 0.0])
            axis = np.cross(y_axis, v)
            c = float(np.clip(y_axis @ v, -1.0, 1.0))
            if np.linalg.norm(axis) < 1e-9:
                R = np.eye(3) if c > 0 else rot_x(180.0)
            else:
                R = rot_axis(axis, math.degrees(math.acos(c)))
            R = rot_axis(v, g.get("spin", 0.0)) @ R
        else:
            R = rot_y(g.get("yaw", 0.0)) @ rot_x(g.get("pitch", 0.0)) @ rot_z(g.get("tilt", 0.0))
        T = host.point_at(g.get("at", 1.0)) + np.array(g.get("offset", (0, 0, 0)), dtype=float)
        for i in range(v0, len(out.verts)):
            out.verts[i] = R @ out.verts[i] + T
            out.skin[i] = [(host.name if n == "__group__" else n, w)
                           for n, w in out.skin[i]]
        pending[0] = None

    for part in model.parts:
        g = part.get("group")
        if pending[0] is not None and g is not pending[0]:
            finalize_group()
        if g is not None and pending[0] is None:
            pending[0], pending[1] = g, len(out.verts)
        fn = builders[part["kind"]]
        if g is not None:
            # build in the group's local frame around a synthetic origin bone
            fake = {"__group__": Bone("__group__", None, (0, 0, 0), (0, 1, 0), 0.0)}
            p2 = dict(part)
            p2["bone"] = "__group__"
            p2["at"] = 0.0
            p2["offset"] = part.get("gpos", (0, 0, 0))
            p2.pop("chain", None)
            t0 = len(out.tris)
            fn(out, model, fake, p2, suffix="", reflect=False)
            if part["kind"] == "attach":
                _fix_winding(out, t0, len(out.tris))
            continue
        variants = ([(".l", False), (".l", True)] if part.get("mirror")
                    else [("", False)])
        for suffix, reflect in variants:
            t0 = len(out.tris)
            fn(out, model, bones, part, suffix=suffix, reflect=reflect)
            if part["kind"] == "attach":
                _fix_winding(out, t0, len(out.tris))
    finalize_group()
    return out
