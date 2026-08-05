
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
        self.uvs = []          # per-vertex chart-local (u, v), parallel to verts
        # glTF materials are single-sided by default. Parts may opt a material
        # into double-sided rendering with the `double_sided` flag, intended
        # only for genuinely thin/open surfaces.
        self.double_sided_materials = set()

    def material(self, name, rgb):
        if name not in self._mat_index:
            self._mat_index[name] = len(self.materials)
            self.materials.append((name, rgb))
        return self._mat_index[name]

    def add_vert(self, p, skin, uv=(0.5, 0.5)):
        self.verts.append(np.asarray(p, dtype=float))
        self.skin.append(skin)
        self.uvs.append((float(uv[0]), float(uv[1])))
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


def mat_rgb(model, name):
    """Base color for a material name: palette, else texture base, else magenta."""
    if name in model.palette:
        return model.palette[name]
    if name in getattr(model, "textures", {}):
        return model.textures[name]["base"]
    return (0.7, 0.5, 0.8)


FRAME_AXES = {"up": (0.0, 1.0, 0.0), "fwd": (0.0, 0.0, 1.0),
              "side": (1.0, 0.0, 0.0)}


def _section_extent(p):
    """Widest full extent of a ring section, across every half-override."""
    w, d = p.get("w", 0.0), p.get("d", 0.0)
    return max(w, d, p.get("wtop", w), p.get("wbot", w),
               p.get("dtop", d), p.get("dbot", d))


def _explicit_skin(bones, spec, suffix, part_name):
    """Resolve an authored `skin=` list to [(bone name, weight), ...]."""
    resolved = []
    for name, weight in spec:
        try:
            resolved.append((resolve_bone_ref(bones, name, suffix).name, weight))
        except WamError:
            raise WamError("part %r: skin= names unknown bone %r"
                           % (part_name, name))
    return resolved


def _frame(tangent, ref=None):
    """Return (side, other) axes for a ring perpendicular to tangent.

    'other' carries the ring depth d and is the projection of a reference axis
    into the ring plane; 'side' carries w.  With no reference axis the choice
    is automatic: the forward (+Z) projection for mostly-vertical tangents,
    the up (+Y) projection for mostly-forward ones.  That switch happens at
    |t·Z| = 0.75 and silently reorients rings near the threshold, so parts
    that run diagonally should pin the axis with `frame=` / `refaxis=`.
    """
    t = tangent / np.linalg.norm(tangent)
    Z = np.array([0.0, 0.0, 1.0])
    Y = np.array([0.0, 1.0, 0.0])
    if ref is not None:
        ref = np.asarray(ref, dtype=float)
        n = np.linalg.norm(ref)
        if n > 1e-9:
            r = ref / n
            other = r - (r @ t) * t
            if np.linalg.norm(other) > 1e-6:
                other /= np.linalg.norm(other)
                side = np.cross(other, t)
                return side / np.linalg.norm(side), other
        # ref parallel to the tangent (or zero): fall through to automatic
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


def _frame_ref(part):
    """Reference axis for a part's ring frame, or None for automatic."""
    if part.get("refaxis") is not None:
        return np.asarray(part["refaxis"], dtype=float)
    name = part.get("frame", "auto")
    if name == "auto":
        return None
    return np.array(FRAME_AXES[name], dtype=float)


def _check_frame_ref(out, part, tangents, ref):
    """Warn when a pinned reference axis degenerates against the path."""
    if ref is None:
        return
    n = np.linalg.norm(ref)
    if n < 1e-9:
        raise WamError("part %r: refaxis= must not be the zero vector"
                       % part["name"])
    r = ref / n
    for t in tangents:
        if abs(float(r @ (t / np.linalg.norm(t)))) > 0.995:
            out.warnings.append(
                "part %r pins its ring frame to an axis nearly parallel to "
                "the path — the rings there fall back to the automatic frame; "
                "pick a reference axis across the part, not along it"
                % part["name"])
            return


def _resolve_arcs(out, model, arcs, double_sided):
    """Resolve [(material, lo, hi)] to [(material index, lo, hi)]."""
    if not arcs:
        return None
    resolved = []
    for name, lo, hi in arcs:
        if double_sided:
            out.double_sided_materials.add(name)
        resolved.append((out.material(name, mat_rgb(model, name)), lo, hi))
    return resolved


def _arc_material(arcs, base, deg):
    """Material index for a circumferential position, in degrees."""
    if not arcs:
        return base
    a = deg % 360.0
    for mat, lo, hi in arcs:
        lo_n, hi_n = lo % 360.0, hi % 360.0
        inside = (lo_n <= a <= hi_n) if lo_n <= hi_n else (a >= lo_n or a <= hi_n)
        if inside:
            return mat
    return base


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
            for k in ("w", "d", "fwd", "side", "up", "roll",
                      "wtop", "wbot", "dtop", "dbot"):
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


def _triangle_components(out, t0, t1):
    """Return triangle-index components connected by shared vertex indices."""
    if t1 <= t0:
        return []
    by_vert = {}
    for ti in range(t0, t1):
        for vi in out.tris[ti]:
            by_vert.setdefault(vi, []).append(ti)
    seen = set()
    comps = []
    for seed in range(t0, t1):
        if seed in seen:
            continue
        stack = [seed]
        seen.add(seed)
        comp = []
        while stack:
            ti = stack.pop()
            comp.append(ti)
            for vi in out.tris[ti]:
                for nb in by_vert.get(vi, ()):
                    if nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        comps.append(comp)
    return comps


def _closest_point_polyline(p, centers):
    """Closest point to p on the center polyline used to generate a tube."""
    if len(centers) == 1:
        return centers[0]
    best_p, best_d2 = centers[0], float("inf")
    for a, b in zip(centers, centers[1:]):
        ab = b - a
        den = float(ab @ ab)
        f = 0.0 if den < 1e-18 else float(np.clip(((p - a) @ ab) / den, 0.0, 1.0))
        q = a + ab * f
        d2 = float((p - q) @ (p - q))
        if d2 < best_d2:
            best_p, best_d2 = q, d2
    return best_p


def _orient_tube_components(out, t0, t1, roles, centers, axes, part_name):
    """Orient each disconnected tube component outward in final coordinates.

    Material seams deliberately duplicate ring vertices, so a single authored
    tube can contain multiple index-disconnected components. Each component is
    checked independently after mirroring. Side faces vote against the nearest
    point on the centerline; caps vote against their authored outward tangent.
    """
    if t1 <= t0:
        return
    if len(roles) != t1 - t0:
        raise AssertionError("tube role count does not match emitted triangles")

    start_out = -np.asarray(axes[0][2], dtype=float)
    end_out = np.asarray(axes[-1][2], dtype=float)
    centers = [np.asarray(c, dtype=float) for c in centers]

    def expected(ti, fc):
        role = roles[ti - t0]
        if role == "start_cap":
            return start_out
        if role == "end_cap":
            return end_out
        return fc - _closest_point_polyline(fc, centers)

    for comp in _triangle_components(out, t0, t1):
        vote = 0.0
        usable = 0
        for ti in comp:
            i, j, k = out.tris[ti]
            a, b, c = out.verts[i], out.verts[j], out.verts[k]
            n = np.cross(b - a, c - a)
            nn = float(np.linalg.norm(n))
            if nn < 1e-14:
                continue
            fc = (a + b + c) / 3.0
            e = expected(ti, fc)
            en = float(np.linalg.norm(e))
            if en < 1e-14:
                continue
            vote += float(n @ (e / en))
            usable += 1
        if usable and vote < 0.0:
            for ti in comp:
                i, j, k = out.tris[ti]
                out.tris[ti] = (i, k, j)

    # A component-level correction preserves manifold edge consistency. Report
    # strongly disagreeing local faces rather than flipping them individually,
    # which would create cracks/non-manifold winding at shared edges.
    bad = 0
    checked = 0
    for ti in range(t0, t1):
        i, j, k = out.tris[ti]
        a, b, c = out.verts[i], out.verts[j], out.verts[k]
        n = np.cross(b - a, c - a)
        nn = float(np.linalg.norm(n))
        if nn < 1e-14:
            continue
        fc = (a + b + c) / 3.0
        e = expected(ti, fc)
        en = float(np.linalg.norm(e))
        if en < 1e-14:
            continue
        checked += 1
        if float((n / nn) @ (e / en)) < -0.15:
            bad += 1
    if checked and bad > max(2, int(checked * 0.08)):
        out.warnings.append(
            "tube %r has %d/%d locally inward-facing triangles after winding "
            "correction — the path probably folds or self-intersects"
            % (part_name, bad, checked))


def _emit_tube(out, centers, axes, params, mats, skins, n_sides,
               cap_start, cap_end, arcs=None):
    """Build a tube from per-ring data.

    centers: list of (3,), axes: list of (side, other, tangent),
    params: list of dicts with w,d,exp,roll (plus optional wtop/wbot/dtop/dbot
    half-section overrides), mats: material idx per band, skins: per-ring skin
    list, arcs: optional per-ring [(material index, lo_deg, hi_deg)] giving
    circumferential material bands.

    Vertices are shared between neighbouring faces of the same material and
    split across material boundaries — along the tube *and* around it — so
    every color edge stays crisp without hand-authored seams.
    """
    nrings = len(centers)
    arcs = arcs or [None] * nrings

    def ring_geometry(ri):
        """(points around the ring, collapsed?, v coordinate)."""
        c = centers[ri]
        side, other, tang = axes[ri]
        p = params[ri]
        exp = p.get("exp", 2.0)
        roll = p.get("roll", 0.0)
        vcoord = p.get("t", ri / max(nrings - 1, 1))
        if roll:
            R = rot_axis(tang, roll)
            side, other = R @ side, R @ other
        # half-extents, per side of the ring's depth axis: the +other half
        # takes wtop/dtop, the -other half wbot/dbot (each defaulting to the
        # symmetric w/d), which is what makes keels and trapezoid chests
        # expressible without leaving the ring language.
        w_hi = p.get("wtop", p["w"]) / 2.0
        w_lo = p.get("wbot", p["w"]) / 2.0
        d_hi = p.get("dtop", p["d"]) / 2.0
        d_lo = p.get("dbot", p["d"]) / 2.0
        if max(w_hi, w_lo, d_hi, d_lo) < 1e-6:
            return [c], True, vcoord
        pts = []
        for (cx, sy) in _superellipse(n_sides, exp):
            wh, dh = (w_hi, d_hi) if sy >= 0 else (w_lo, d_lo)
            pts.append(c + side * (cx * wh) + other * (sy * dh))
        return pts, False, vcoord

    geom = [ring_geometry(ri) for ri in range(nrings)]
    cache = {}

    def vert(ri, k, mat):
        """Vertex at ring `ri`, column `k` in 0..n_sides, for one material.

        Column n_sides is column 0 again in space but u=1 in the chart. A
        closed loop cannot be unwrapped without a seam: sharing that column
        would make the wrap quad run backwards across the entire chart, and
        in the atlas it smears its material over the whole cell — erasing
        every band rasterized before it, or taking the cell if it is the
        band.
        """
        pts, collapsed, vcoord = geom[ri]
        key = (ri, -1 if collapsed else k, mat)
        if key in cache:
            return cache[key]
        pt = pts[0] if collapsed else pts[k % n_sides]
        sk = skins[ri]
        sk = sk(pt) if callable(sk) else sk
        uv = (0.5, vcoord) if collapsed else (k / n_sides, vcoord)
        cache[key] = out.add_vert(pt, sk, uv=uv)
        return cache[key]

    roles = []

    def tri(a, b, c, mat, role):
        out.add_tri(a, b, c, mat)
        roles.append(role)

    def band_material(ri, k):
        """Material of the quad between side index k and k+1 of band ri."""
        return _arc_material(arcs[ri], mats[ri], 360.0 * (k + 1) / n_sides)

    for ri in range(nrings - 1):
        lo_flat = geom[ri][1]
        hi_flat = geom[ri + 1][1]
        if lo_flat and hi_flat:
            continue
        for k in range(n_sides):
            k2 = k + 1          # unwrapped: the last column is the seam copy
            mat = band_material(ri, k)
            a, b = vert(ri, k, mat), vert(ri, k2, mat)
            c, d = vert(ri + 1, k2, mat), vert(ri + 1, k, mat)
            # A collapsed ring (a `tip`) contributes one triangle per column,
            # not a quad with a zero-area half.
            if lo_flat:
                tri(a, c, d, mat, "side")
            elif hi_flat:
                tri(a, b, c, mat, "side")
            else:
                tri(a, b, c, mat, "side")
                tri(a, c, d, mat, "side")

    def cap(ri, center, outward, style, role):
        # A cap takes its own ring's material (the documented per-ring cap
        # override), not the material of the band leading into it.
        pts, collapsed, _ = geom[ri]
        if style == "none" or collapsed:
            return
        raise_amt = 0.0
        if style in ("dome", "point"):
            # apex raised along tangent by a fraction of the mean radius
            r = float(np.mean([np.linalg.norm(p - center) for p in pts]))
            raise_amt = r * (0.45 if style == "dome" else 1.0)
        apex = center + outward * raise_amt
        apex_v = 0.0 if role == "start_cap" else 1.0
        sk = skins[ri]
        sk = sk(apex) if callable(sk) else sk
        apex_ids = {}
        for k in range(n_sides):
            k2 = k + 1
            mat = band_material(ri, k)
            if mat not in apex_ids:
                apex_ids[mat] = out.add_vert(apex, sk, uv=(0.5, apex_v))
            cid = apex_ids[mat]
            if role == "start_cap":
                # Ring vertices are CCW when viewed along +tangent. The start
                # cap must therefore reverse that order to face -tangent.
                tri(vert(ri, k2, mat), vert(ri, k, mat), cid, mat, role)
            else:
                # The end cap faces +tangent and keeps the ring order.
                tri(vert(ri, k, mat), vert(ri, k2, mat), cid, mat, role)

    # start cap faces along -tangent, end cap along +tangent
    s_tang = np.asarray(axes[0][2], dtype=float)
    e_tang = np.asarray(axes[-1][2], dtype=float)
    cap(0, centers[0], -s_tang, cap_start, "start_cap")
    cap(nrings - 1, centers[-1], e_tang, cap_end, "end_cap")
    return roles


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
    ref = _frame_ref(part)
    part_arcs = part.get("material_arc")
    centers, axes, params, mats, skins, arcs = [], [], [], [], [], []
    tangents = []
    for r in rings:
        t = r["t"]
        c = sampler.point(t)
        tang = sampler.tangent(t)
        tangents.append(tang)
        side, other = _frame(tang, ref)
        roll = r.get("roll", 0.0)
        if roll:
            # Roll first, then offset: `fwd`/`side` name the axes of the
            # section the author is looking at, not the unrolled frame.
            R = rot_axis(tang, roll)
            side, other = R @ side, R @ other
        c = c + other * r.get("fwd", 0.0) + side * r.get("side", 0.0)
        if "up" in r:
            c = c + np.array([0.0, r["up"], 0.0])
        exp = SHAPE_EXP.get(r.get("shape", ""), default_exp)
        w = r["w"]
        d = r.get("d", w)
        sect = dict(w=w, d=d, exp=exp, roll=0.0, t=t)
        for key in ("wtop", "wbot", "dtop", "dbot"):
            if key in r:
                sect[key] = r[key]
        if r.get("tip"):
            sect = dict(w=0.0, d=0.0, exp=exp, roll=0.0, t=t)
        centers.append(c)
        axes.append((side, other, tang))
        params.append(sect)
        mat_name = r.get("material", base_mat)
        mats.append(out.material(mat_name, mat_rgb(model, mat_name)))
        if part.get("double_sided"):
            out.double_sided_materials.add(mat_name)
        arcs.append(_resolve_arcs(out, model, r.get("material_arc", part_arcs),
                                  part.get("double_sided")))
        base_skin = skin_for(t)
        if "skin" in r or "skin" in part:
            skins.append(_explicit_skin(bones, r.get("skin") or part["skin"],
                                        suffix, part["name"]))
        elif "follow" in r:
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
            # The falloff is lateral (world x), so it must be measured against
            # the ring's widest horizontal extent: on a flat sheet `w` is the
            # thickness, and dividing by it saturates the term at 1.0.
            half_w = max(w, d, 1e-6) / 2.0

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
        rmax = max(_section_extent(params[i]), _section_extent(params[i + 1])) / 2.0
        if rmax > reach:
            out.warnings.append(
                "loft %r folds between rings %.2f and %.2f (path bends %.0f\u00b0 "
                "while the ring radius %.3f exceeds the bend reach %.3f) — the "
                "surface doubles back and looks like missing faces; split the "
                "loft at the joint or shrink the rings"
                % (part["name"], rings[i]["t"], rings[i + 1]["t"],
                   math.degrees(theta), rmax, reach))

    _check_frame_ref(out, part, tangents, ref)

    v0 = len(out.verts)
    t0 = len(out.tris)
    roles = _emit_tube(out, centers, axes, params, mats, skins, n_sides,
                       part.get("cap_start", "flat"),
                       part.get("cap_end", "flat"), arcs=arcs)
    if reflect:
        _reflect_range(out, v0, suffix)
        refl = np.array([-1.0, 1.0, 1.0])
        orient_centers = [c * refl for c in centers]
        orient_axes = [tuple(v * refl for v in ax) for ax in axes]
    else:
        orient_centers, orient_axes = centers, axes
    _orient_tube_components(out, t0, len(out.tris), roles,
                            orient_centers, orient_axes,
                            part["name"] + (".r" if reflect else suffix))
    out.part_ranges[part["name"] + (".r" if reflect else suffix)] = (v0, len(out.verts))


def build_sweep(out, model, bones, part, suffix="", reflect=False):
    n_sides = max(6, int(part.get("sides", STYLE_SIDES.get(model.style, 8)) - 2))
    base_mat = part.get("material") or "default"
    mat = out.material(base_mat, mat_rgb(model, base_mat))
    if part.get("double_sided"):
        out.double_sided_materials.add(base_mat)
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
    ref = _frame_ref(part)
    side, other = _frame(d, ref)
    _check_frame_ref(out, part, [d], ref)
    tang = d.copy()
    centers, axes, params, mats, skins, arcs = [], [], [], [], [], []
    part_arcs = _resolve_arcs(out, model, part.get("material_arc"),
                              part.get("double_sided"))
    skin = ( _explicit_skin(bones, part["skin"], suffix, part["name"])
             if "skin" in part else [(host.name, 1.0)])

    total_len = sum(sg["len"] for sg in part["segs"]) or 1.0
    acc_len = [0.0]

    def push(c, r, seg=None):
        seg = seg or {}
        centers.append(c.copy())
        axes.append((side.copy(), other.copy(), tang.copy()))
        params.append(dict(w=2 * r, d=2 * r, exp=2.0, t=acc_len[0] / total_len))
        if "material" in seg:
            if part.get("double_sided"):
                out.double_sided_materials.add(seg["material"])
            mats.append(out.material(seg["material"],
                                     mat_rgb(model, seg["material"])))
        else:
            mats.append(mat)
        skins.append(_explicit_skin(bones, seg["skin"], suffix, part["name"])
                     if "skin" in seg else skin)
        arcs.append(part_arcs)

    Y = np.array([0.0, 1.0, 0.0])
    Z = np.array([0.0, 0.0, 1.0])
    segs = part["segs"]
    pos = origin.copy()
    push(pos, segs[0]["r"], segs[0])
    for si, seg in enumerate(segs):
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
        acc_len[0] += seg["len"]
        r = 0.0 if seg.get("tip") else seg["r"]
        # a ring carries the material/skin of the band that starts at it, so
        # `material=` on a seg colors that seg, not the one before it
        push(pos, r, segs[si + 1] if si + 1 < len(segs) else seg)

    v0 = len(out.verts)
    t0 = len(out.tris)
    roles = _emit_tube(
        out, centers, axes, params, mats, skins, n_sides,
        part.get("cap_start", "flat"),
        "none" if part["segs"][-1].get("tip") else part.get("cap_end", "dome"),
        arcs=arcs)
    if reflect:
        _reflect_range(out, v0, suffix)
        refl = np.array([-1.0, 1.0, 1.0])
        orient_centers = [c * refl for c in centers]
        orient_axes = [tuple(v * refl for v in ax) for ax in axes]
    else:
        orient_centers, orient_axes = centers, axes
    _orient_tube_components(out, t0, len(out.tris), roles,
                            orient_centers, orient_axes,
                            part["name"] + (".r" if reflect else suffix))
    out.part_ranges[part["name"] + (".r" if reflect else suffix)] = (v0, len(out.verts))


def _blend_skin(a, b, f, limit=4):
    """Blend two skin lists, keeping the `limit` strongest influences."""
    if f <= 0.0:
        return list(a)
    if f >= 1.0:
        return list(b)
    acc = {}
    for name, w in a:
        acc[name] = acc.get(name, 0.0) + w * (1.0 - f)
    for name, w in b:
        acc[name] = acc.get(name, 0.0) + w * f
    kept = sorted(acc.items(), key=lambda nw: -nw[1])[:limit]
    total = sum(w for _, w in kept) or 1.0
    return [(n, w / total) for n, w in kept]


class _Rib:
    """One spar of a web: a path plus the bones it is skinned to."""

    def __init__(self, sampler, chain, lo, hi, override=None):
        self.sampler = sampler
        self.chain = chain
        self.lo = lo
        self.hi = hi
        self.override = override

    def at(self, v):
        x = self.lo + float(np.clip(v, 0.0, 1.0)) * (self.hi - self.lo)
        skin = self.override or _chain_skin(self.sampler, self.chain, x)
        return self.sampler.point(x), skin


def build_web(out, model, bones, part, suffix="", reflect=False):
    """A membrane stretched across a fan of ribs.

    Tubes cannot express a surface that spans several bones — wings, frills,
    fins, webbed feet, capes, sails. A web samples each rib (a bone chain,
    optionally rooted at a shared anchor), lofts the surface between adjacent
    ribs as one grid, and blends the ribs' skin weights across it, so the
    membrane deforms with every bone it touches and shares its boundary edges
    with its neighbours by construction.
    """
    ribs = part.get("ribs") or []
    if len(ribs) < 2:
        raise WamError("web %r needs at least 2 rib lines" % part["name"])
    base_mat = part.get("material") or "default"
    mat = out.material(base_mat, mat_rgb(model, base_mat))
    thickness = float(part.get("thickness", 0.0))
    if thickness <= 0.0 or part.get("double_sided"):
        # A bare membrane has no inside; it must render from both faces.
        out.double_sided_materials.add(base_mat)
    steps = max(2, int(part.get("steps", 6)))
    usteps = max(1, int(part.get("usteps", 2)))
    scallop = 0.0
    if part.get("trailing", "straight") == "scallop":
        scallop = 0.2
    scallop = float(part.get("scallop", scallop))

    anchor_bone = anchor_pt = None
    if "anchor" in part:
        anchor_bone = resolve_bone_ref(bones, part["anchor"][0], suffix)
        anchor_pt = (anchor_bone.point_at(part["anchor"][1])
                     + np.array(part.get("offset", (0, 0, 0)), dtype=float))

    def make_rib(rib):
        chain = chain_bones(bones, rib["chain"][0], rib["chain"][1], suffix)
        head0 = chain[0].head
        src_bone, src_pt = anchor_bone, anchor_pt
        if "from" in rib:
            src_bone = resolve_bone_ref(bones, rib["from"][0], suffix)
            src_pt = src_bone.point_at(rib["from"][1])
        pts, blist = [], []
        if src_pt is not None and np.linalg.norm(head0 - src_pt) > 1e-6:
            pts.append(src_pt)
            blist.append(src_bone or chain[0])
        pts.append(head0)
        for b in chain:
            pts.append(b.tail)
            blist.append(b)
        override = (_explicit_skin(bones, rib["skin"], suffix, part["name"])
                    if "skin" in rib else
                    (_explicit_skin(bones, part["skin"], suffix, part["name"])
                     if "skin" in part else None))
        return _Rib(PathSampler(pts), blist, rib.get("start", 0.0),
                    1.0 - rib.get("inset", 0.0), override)

    spars = [make_rib(r) for r in ribs]
    ncols = (len(spars) - 1) * usteps + 1
    nrows = steps + 1

    pts = [[None] * ncols for _ in range(nrows)]
    skins = [[None] * ncols for _ in range(nrows)]
    for ci in range(ncols):
        u = ci / float(usteps)
        i = min(int(math.floor(u)), len(spars) - 2)
        f = u - i
        # The trailing edge bows back toward the anchor between ribs and
        # touches each rib's tip exactly. The bow grows with v so it vanishes
        # at the root: a uniform dip would push interior rows backwards faster
        # than neighbouring ribs pull them apart, folding the surface where
        # the fan is narrowest.
        bow = scallop * (4.0 * f * (1.0 - f))
        for rj in range(nrows):
            v = rj / float(steps)
            v *= 1.0 - bow * v
            pa, ska = spars[i].at(v)
            pb, skb = spars[i + 1].at(v)
            pts[rj][ci] = pa * (1.0 - f) + pb * f
            skins[rj][ci] = _blend_skin(ska, skb, f)

    # surface normal per grid point, for the thick variant and for winding
    def grid_normal(rj, ci):
        r0, r1 = max(rj - 1, 0), min(rj + 1, nrows - 1)
        c0, c1 = max(ci - 1, 0), min(ci + 1, ncols - 1)
        du = pts[rj][c1] - pts[rj][c0]
        dv = pts[r1][ci] - pts[r0][ci]
        n = np.cross(du, dv)
        ln = float(np.linalg.norm(n))
        return n / ln if ln > 1e-12 else np.array([0.0, 1.0, 0.0])

    v0 = len(out.verts)
    t0 = len(out.tris)

    def sheet(offset_sign):
        """Emit one layer of grid vertices; returns the id grid."""
        ids = [[None] * ncols for _ in range(nrows)]
        for rj in range(nrows):
            for ci in range(ncols):
                p = pts[rj][ci]
                if offset_sign:
                    p = p + grid_normal(rj, ci) * (offset_sign * thickness / 2.0)
                # ribs that share an anchor land on the same point; reuse the
                # vertex so the root of the fan is a seam, not a crack
                if ci and np.linalg.norm(p - out.verts[ids[rj][ci - 1]]) < 1e-7:
                    ids[rj][ci] = ids[rj][ci - 1]
                    continue
                ids[rj][ci] = out.add_vert(
                    p, skins[rj][ci],
                    uv=(ci / max(ncols - 1, 1), rj / max(nrows - 1, 1)))
        return ids

    def tri(a, b, c):
        # the fan root is a genuine singularity: ribs that share an anchor
        # meet there, so the first row of faces can pinch to nothing
        n = np.cross(out.verts[b] - out.verts[a], out.verts[c] - out.verts[a])
        if float(n @ n) < 1e-20:
            return
        out.add_tri(a, b, c, mat)

    def quad(a, b, c, d):
        """Emit a quad, degrading to a triangle where an edge collapsed."""
        corners = [a, b, c, d]
        uniq = [x for i, x in enumerate(corners) if x != corners[i - 1]]
        if len(uniq) == 4:
            tri(a, b, c)
            tri(a, c, d)
        elif len(uniq) == 3:
            tri(uniq[0], uniq[1], uniq[2])

    front = sheet(1 if thickness > 0 else 0)
    for rj in range(nrows - 1):
        for ci in range(ncols - 1):
            quad(front[rj][ci], front[rj][ci + 1],
                 front[rj + 1][ci + 1], front[rj + 1][ci])

    if thickness > 0:
        back = sheet(-1)
        for rj in range(nrows - 1):
            for ci in range(ncols - 1):
                quad(back[rj][ci], back[rj + 1][ci],
                     back[rj + 1][ci + 1], back[rj][ci + 1])
        # rim: walk the grid boundary once, front layer to back layer
        loop = ([(0, ci) for ci in range(ncols - 1)]
                + [(rj, ncols - 1) for rj in range(nrows - 1)]
                + [(nrows - 1, ci) for ci in range(ncols - 1, 0, -1)]
                + [(rj, 0) for rj in range(nrows - 1, 0, -1)])
        for (r0, c0), (r1, c1) in zip(loop, loop[1:] + loop[:1]):
            quad(front[r0][c0], back[r0][c0], back[r1][c1], front[r1][c1])

    if reflect:
        _reflect_range(out, v0, suffix)
    if thickness > 0:
        # a slab encloses a volume: orient it the same way as any other solid
        _fix_winding(out, t0, len(out.tris))
    else:
        _fix_sheet_winding(out, v0, len(out.verts), t0, len(out.tris))
    out.part_ranges[part["name"] + (".r" if reflect else suffix)] = (v0, len(out.verts))


def _fix_sheet_winding(out, v0, v1, t0, t1):
    """Point a web's faces away from the model's centerline.

    A membrane has no enclosed volume to orient against, so the only stable
    reference is which way it faces. Mirroring reflects vertices without
    reversing triangles, so without this the `.r` copy of a wing would be
    wound inside out relative to its twin, and the two would shade
    differently. Double-sided materials make the choice cosmetic; consistency
    across the mirrored pair is not.
    """
    tris = list(range(t0, t1))
    if not tris:
        return
    verts = np.array([out.verts[i] for i in range(v0, v1)])
    center = verts.mean(axis=0)
    axis = np.array([center[0], 0.0, 0.0])
    if np.linalg.norm(axis) < 1e-9:
        axis = np.array([0.0, 1.0, 0.0])
    axis = axis / np.linalg.norm(axis)
    vote = 0.0
    for ti in tris:
        i, j, k = out.tris[ti]
        n = np.cross(out.verts[j] - out.verts[i], out.verts[k] - out.verts[i])
        vote += float(n @ axis)
    if vote < 0.0:
        for ti in tris:
            i, j, k = out.tris[ti]
            out.tris[ti] = (i, k, j)


def _reflect_range(out, v0, suffix):
    """Reflect verts added since v0 across X=0 and remap skin .l -> .r."""
    for i in range(v0, len(out.verts)):
        out.verts[i] = out.verts[i] * np.array([-1.0, 1.0, 1.0])
        out.skin[i] = [(n[:-2] + ".r" if n.endswith(".l") else n, w)
                       for (n, w) in out.skin[i]]


def build_attach(out, model, bones, part, suffix="", reflect=False):
    prim = part.get("prim", "sphere")
    base_mat = part.get("material") or "default"
    mat = out.material(base_mat, mat_rgb(model, base_mat))
    if part.get("double_sided"):
        out.double_sided_materials.add(base_mat)
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
        top = out.add_vert(origin + np.array([0, h / 2, 0]), skin, uv=(0.5, 1.0))
        bot = out.add_vert(origin - np.array([0, h / 2, 0]), skin, uv=(0.5, 0.0))
        for li in range(1, lats):
            th = math.pi * li / lats
            row = []
            # lons + 1 columns: the last repeats the first in space but
            # carries u=1, so the wrap quad does not run backwards across
            # the whole texture chart (see _emit_tube).
            for lo in range(lons + 1):
                ph = 2 * math.pi * (lo % lons) / lons
                p = origin + np.array([math.sin(th) * math.cos(ph) * w / 2,
                                       math.cos(th) * h / 2,
                                       math.sin(th) * math.sin(ph) * d / 2])
                row.append(out.add_vert(p, skin,
                                        uv=(lo / lons, 1.0 - li / lats)))
            ids.append(row)
        for lo in range(lons):
            lo2 = lo + 1
            out.add_tri(top, ids[0][lo2], ids[0][lo], mat)
            out.add_tri(bot, ids[-1][lo], ids[-1][lo2], mat)
        for a, b in zip(ids, ids[1:]):
            for lo in range(lons):
                lo2 = lo + 1
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
        box_uv = [(0.0, 1.0), (0.33, 1.0), (0.66, 1.0), (1.0, 1.0),
                  (0.0, 0.0), (0.33, 0.0), (0.66, 0.0), (1.0, 0.0)]
        ids = [out.add_vert(p, skin, uv=box_uv[i]) for i, p in enumerate(c)]
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
    """Orient each closed connected component by signed volume."""
    if t1 <= t0:
        return
    for comp in _triangle_components(out, t0, t1):
        vs = np.array([out.verts[i] for ti in comp for i in out.tris[ti]])
        if not len(vs):
            continue
        centroid = vs.mean(axis=0)
        vol = 0.0
        for ti in comp:
            i, j, k = out.tris[ti]
            a = out.verts[i] - centroid
            b = out.verts[j] - centroid
            c = out.verts[k] - centroid
            vol += float(a @ np.cross(b, c))
        if vol < 0.0:
            for ti in comp:
                i, j, k = out.tris[ti]
                out.tris[ti] = (i, k, j)


def build(model, bones):
    out = MeshOut()
    builders = {"loft": build_loft, "sweep": build_sweep,
                "attach": build_attach, "web": build_web}
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
        if g is not None and part["kind"] == "web":
            raise WamError("web %r cannot live in a group: a membrane is "
                           "defined by the bones it spans, and a group is a "
                           "rigid frame with none" % part["name"])
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
