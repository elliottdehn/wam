
"""Mesh generation: lofts, sweeps, and attachments -> triangles + skin weights.

All geometry is generated in world (rest-pose) space, in model-height units;
the compiler scales to meters at export time.
"""
import math
import numpy as np

from .parser import WamError
from .skeleton import (BASE_DIRS, bone_relative_dir, resolve_dir, rot_axis, chain_bones,
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
        # realized (start dir, tip dir, start point, tip point) per sweep, so
        # the lint can say which way a bend actually went
        self.sweep_paths = {}
        # part key -> degrees the `on=` press turned it off its authored aim
        self.pressed = {}
        self.rests = {}
        self.sections = {}   # part -> most concave unit outline it used
        self.snapped = {}    # part -> (host key, snap point, surface normal)
        self.end_rings = {}  # part -> [vertex id per column] at its last ring
        self.continues = {}  # part -> the part it grows out of
        self.shade_group = []

    def material(self, name, rgb):
        if name not in self._mat_index:
            self._mat_index[name] = len(self.materials)
            self.materials.append((name, rgb))
        return self._mat_index[name]

    def add_vert(self, p, skin, uv=(0.5, 0.5)):
        self.verts.append(np.asarray(p, dtype=float))
        self.skin.append(skin)
        self.uvs.append((float(uv[0]), float(uv[1])))
        self.shade_group.append(0)      # 0 = weld with anything coincident
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


def _record_press(out, part, suffix, reflect, before, after):
    """Note how far the press turned a part off its authored aim."""
    a = np.asarray(before, dtype=float)
    b = np.asarray(after, dtype=float)
    cos = float(np.clip((a @ b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-12),
                        -1.0, 1.0))
    key = part["name"] + (".r" if reflect else suffix)
    # Only worth reporting if the author actually stated an aim. A part with
    # no `dir=` has nothing to be turned away *from*, so the surface deciding
    # is the whole point — reporting it there fires on every eye ever placed
    # and reads as a fault.
    if part.get("dir"):
        out.pressed[key] = math.degrees(math.acos(cos))
    return b


def around_origin(part, host, at, out, suffix, reflect):
    """Start point for `around=<deg>`: polar placement on a host surface.

    Detail belongs *somewhere on a surface* — an eye on the side of a skull,
    a horn above the brow, a spine along a back — and the only way to say that
    was an offset in bone space, which is a guess. Guessing put the dragon's
    eyes inside its head and a spine inside a neck. This names the angle
    around the bone instead and lets `on=` find the surface from outside.
    """
    ang = math.radians(float(part["around"]))
    side, other = _frame(np.asarray(host.dir, dtype=float))
    if reflect:
        ang = -ang
    radial = side * math.cos(ang) + other * math.sin(ang)
    ref = part.get("on")
    reach = 1.0
    if ref:
        for cand in (ref + suffix, ref):
            if cand in out.part_ranges:
                v0, v1 = out.part_ranges[cand]
                pts = np.array(out.verts[v0:v1], dtype=float)
                reach = float(np.linalg.norm(pts - host.point_at(at), axis=1).max())
                break
    return host.point_at(at) + radial * reach * 1.05


def part_dir_on_bone(part, host, reflect=False):
    """A part's aim, allowed to be stated relative to the bone it sits on.

    `dir=` on a sub-part is a *world* axis, and a detail placed partway along
    an angled bone therefore does not follow it: a jaw written `dir=fwd` on a
    head that points forward-and-down runs off at an angle to its own skull,
    and the mouth diverges from the face. Composition already solves this for
    grafted models with `along` / `across=` / `aim=`; this is the same
    vocabulary one level down.
    """
    spec = {}
    for k in ("along", "against"):
        if part.get(k):
            spec[k] = True
    for k in ("across", "aim"):
        if part.get(k) is not None:
            spec[k] = part[k]
    if spec:
        d = bone_relative_dir(host, spec, what="dir")
        if reflect:
            d = np.array([-d[0], d[1], d[2]])
        # world pitch/yaw/tilt stay available as deviations from that
        for axis, key in (((1.0, 0, 0), "pitch"), ((0, 1.0, 0), "yaw"),
                          ((0, 0, 1.0), "tilt")):
            if part.get(key):
                d = rot_axis(np.array(axis), float(part[key])) @ d
        return d / max(np.linalg.norm(d), 1e-12)
    return resolve_dir(part.get("dir", "up"), part.get("pitch", 0.0),
                       part.get("yaw", 0.0), part.get("tilt", 0.0))


def _pressed_dir(part, normal):
    """The authored aim, carried into a frame where `dir` is the normal.

    With no pitch/yaw/tilt this is exactly the surface normal; the authored
    angles then read as deviations from it, the same way aiming a `group`
    works. That keeps `dir=up pitch=15` meaning "15 degrees off square"
    instead of 15 degrees off the world axis it was never about.
    """
    name = part.get("dir", "up")
    base = np.array(BASE_DIRS[name], dtype=float) if name in BASE_DIRS \
        else np.array([0.0, 1.0, 0.0])
    aim = resolve_dir(name, part.get("pitch", 0.0), part.get("yaw", 0.0),
                      part.get("tilt", 0.0))
    return _align_rotation(base, normal) @ aim


def _align_rotation(src, dst):
    """Rotation taking unit vector `src` onto unit vector `dst`."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    src = src / max(np.linalg.norm(src), 1e-12)
    dst = dst / max(np.linalg.norm(dst), 1e-12)
    axis = np.cross(src, dst)
    c = float(np.clip(src @ dst, -1.0, 1.0))
    if np.linalg.norm(axis) < 1e-9:
        if c > 0:
            return np.eye(3)
        # antiparallel: any perpendicular axis will do
        perp = np.array([1.0, 0.0, 0.0])
        if abs(src[0]) > 0.9:
            perp = np.array([0.0, 1.0, 0.0])
        axis = np.cross(src, perp)
    return rot_axis(axis, math.degrees(math.acos(c)))


def snap_on(out, part, origin, suffix, default_inset=0.012, footprint=0.0):
    """Place a part against the surface named by `on=`.

    Returns (origin, normal). The origin is snapped to the closest point on
    that surface and sunk in by `inset`; the normal is the outward direction
    of the surface there, which the caller uses to *press* the part flat
    against it — a vertical horn on a sloped shoulder tilts to stand square
    on the slope rather than cutting into it on one side and floating on the
    other. `press=off` on the part keeps the authored world direction.

    The normal is averaged over the triangles the part's base actually
    covers, weighted by proximity, so a base spanning several facets of a
    low-poly surface stands on their average rather than snapping to
    whichever one it happened to land on.
    """
    ref = part.get("on")
    if not ref:
        return origin, None
    key = None
    for cand in (ref + suffix, ref):
        if cand in out.part_ranges:
            key = cand
            break
    if key is None:
        raise WamError("part %r: on=%r not found (the base part must be "
                       "defined earlier in the file)" % (part["name"], ref))
    v0, v1 = out.part_ranges[key]
    origin = np.asarray(origin, dtype=float)
    best, best_d, hits = None, 1e18, []
    for (i, j, k) in out.tris:
        if not (v0 <= i < v1):
            continue
        a, b, c = out.verts[i], out.verts[j], out.verts[k]
        p = _closest_point_tri(origin, a, b, c)
        d = float(np.dot(p - origin, p - origin))
        fn = np.cross(b - a, c - a)          # faces are wound outward already
        hits.append((d, p, fn))
        if d < best_d:
            best, best_d = p, d
    if best is None:
        return origin, None

    radius = max(footprint, 1e-6)
    nrm = np.zeros(3)
    for d, p, fn in hits:
        if float(np.dot(p - best, p - best)) <= radius * radius:
            nrm = nrm + fn                   # area-weighted by construction
    if np.linalg.norm(nrm) < 1e-12:          # footprint smaller than a facet
        nrm = min(hits, key=lambda h: h[0])[2]
    n = float(np.linalg.norm(nrm))
    normal = nrm / n if n > 1e-12 else None

    inset = part.get("inset", default_inset)
    if normal is None:
        return best, None
    out.snapped[part["name"] + suffix] = (key, best, normal)
    return best - normal * inset, normal


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


def _superellipse(n_sides, exp, span=None):
    """Unit cross-section points (cos-like, sin-like) for a superellipse.

    `span=(lo, hi)` as fractions of the way around opens the section into a
    strip: it yields `n_sides + 1` points with distinct ends rather than
    `n_sides` points that wrap, which is what turns a closed tube into an
    open shell.
    """
    pts = []
    count = n_sides if span is None else n_sides + 1
    for i in range(count):
        f = (i + 0.5) / n_sides if span is None else \
            span[0] + (span[1] - span[0]) * i / n_sides
        phi = 2 * math.pi * f
        c, s = math.cos(phi), math.sin(phi)
        pts.append((math.copysign(abs(c) ** (2.0 / exp), c),
                    math.copysign(abs(s) ** (2.0 / exp), s)))
    return pts


def profile_unit(pts, mirror, n_sides, span=None):
    """A named outline as `n_sides` unit points, matching `_superellipse`.

    Two things have to line up with the built-in sections or profiles cannot
    share the ring vocabulary: the points span [-1, 1] on both axes (so `w`
    and `d` keep meaning full width and depth), and they run counter-clockwise
    so caps and winding come out the same way.

    Resampling is by **arc length**, not by casting rays from the centre. A
    radial parameterisation would phase-match the superellipse exactly and is
    tempting for that reason, but it cannot represent any outline where a ray
    crosses the boundary twice — which is precisely the crescents and hooks
    that profiles exist to make possible.
    """
    pts = [(float(x), float(y)) for x, y in pts]
    if mirror:
        # author the +x half; the other half is its reflection, walked back
        # so the closed loop stays a single non-self-crossing outline
        pts = pts + [(-x, y) for x, y in reversed(pts)
                     if abs(x) > 1e-9]
    if len(pts) < 3:
        raise WamError("profile needs at least 3 points")

    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    sx = max((max(xs) - min(xs)) / 2.0, 1e-9)
    sy = max((max(ys) - min(ys)) / 2.0, 1e-9)
    pts = [((x - cx) / sx, (y - cy) / sy) for x, y in pts]

    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1]
               - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts)))
    if area < 0:
        pts = pts[::-1]                      # force counter-clockwise

    # start at the point nearest +x, so two profiles blend corner-to-corner
    start = max(range(len(pts)), key=lambda i: pts[i][0] - abs(pts[i][1]) * 1e-6)
    pts = pts[start:] + pts[:start]

    loop = pts + [pts[0]]
    seg = [math.hypot(loop[i + 1][0] - loop[i][0], loop[i + 1][1] - loop[i][1])
           for i in range(len(pts))]
    total = sum(seg) or 1.0
    # arc position of each authored vertex, for corner snapping below
    corner_at, run = [], 0.0
    for i in range(len(pts)):
        corner_at.append(run)
        run += seg[i]

    if span is None:
        targets = [total * (i + 0.5) / n_sides for i in range(n_sides)]
    else:
        targets = [total * (span[0] + (span[1] - span[0]) * i / n_sides)
                   for i in range(n_sides + 1)]
    # An evenly spaced walk lands on a corner only by luck, and a profile's
    # corners are the whole reason it was drawn — a notched blade resampled
    # off its notch is just a lumpy oval. Each authored vertex therefore
    # claims its nearest sample, provided no two fight over the same one.
    if len(pts) <= n_sides and span is None:
        taken = {}
        for ci, ca in enumerate(corner_at):
            best = min(range(n_sides), key=lambda i: abs(targets[i] - ca))
            if best not in taken:
                taken[best] = ca
        for i, ca in taken.items():
            targets[i] = ca

    out, j, run = [], 0, 0.0
    for target in targets:
        j, run = 0, 0.0
        while j < len(seg) - 1 and run + seg[j] < target:
            run += seg[j]
            j += 1
        f = (target - run) / max(seg[j], 1e-12)
        a, b = loop[j], loop[j + 1]
        out.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    return out


def section_concavity(unit):
    """How far a unit outline departs from its own convex hull, 0..1.

    A profile is normalised to span `w` and `d`, which is what lets it share
    every ring key — and it also means the bounding box is identical whatever
    the outline is, so no existing measure can tell a crescent from a circle.
    The one thing a profile adds that a superellipse cannot express is
    *concavity*, so that is what there has to be a number for: 0 for anything
    convex, rising as the outline bites into itself.
    """
    pts = [(float(x), float(y)) for x, y in unit]
    if len(pts) < 3:
        return 0.0

    def area(poly):
        return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                       - poly[(i + 1) % len(poly)][0] * poly[i][1]
                       for i in range(len(poly)))) / 2.0

    def hull(ps):
        ps = sorted(set(ps))
        if len(ps) < 3:
            return ps

        def half(seq):
            out = []
            for q in seq:
                while len(out) >= 2 and \
                        (out[-1][0] - out[-2][0]) * (q[1] - out[-2][1]) - \
                        (out[-1][1] - out[-2][1]) * (q[0] - out[-2][0]) <= 0:
                    out.pop()
                out.append(q)
            return out
        return half(ps)[:-1] + half(ps[::-1])[:-1]

    h = area(hull(pts))
    return 0.0 if h < 1e-12 else max(0.0, 1.0 - area(pts) / h)


def _respan_unit(unit, span, n_sides):
    """Take an already-sampled outline and re-cut it to an open span.

    Profiles are resampled by arc length, so an arc of one is the same walk
    restricted to part of the perimeter — done here rather than at parse time
    because `span` may differ per ring while the profile is shared.
    """
    loop = list(unit) + [unit[0]]
    seg = [math.hypot(loop[i + 1][0] - loop[i][0], loop[i + 1][1] - loop[i][1])
           for i in range(len(unit))]
    total = sum(seg) or 1.0
    out = []
    for i in range(n_sides + 1):
        target = total * (span[0] + (span[1] - span[0]) * i / n_sides)
        run, j = 0.0, 0
        while j < len(seg) - 1 and run + seg[j] < target:
            run += seg[j]
            j += 1
        f = (target - run) / max(seg[j], 1e-12)
        a, b = loop[j], loop[j + 1]
        out.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    return out


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
            out["span"] = a.get("span")
            ua, ub = a.get("unit"), b.get("unit")
            if ua is not None or ub is not None:
                ua = ua if ua is not None else ub
                ub = ub if ub is not None else ua
                out["unit"] = [(pa[0] * (1 - f) + pb[0] * f,
                                pa[1] * (1 - f) + pb[1] * f)
                               for pa, pb in zip(ua, ub)]
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


def _rings_at_joints(rings, sampler):
    """Add a ring wherever the chain bends, if the author left one out.

    Skin weights blend only near a joint, so a ring straddling two bones with
    nothing at the joint between them binds rigidly to one of them: the tube
    creases at the bend and the second bone moves nothing. Placing a ring at
    each joint is a continuous decision about vertices, which is the
    compiler's job — the interpolated section is exactly what the surface
    passed through anyway, so the silhouette does not change.
    """
    if len(sampler.seg_lens) < 2:
        return rings
    out = list(rings)
    for cut in np.cumsum(sampler.seg_lens[:-1]) / sampler.total:
        cut = float(cut)
        if any(abs(r["t"] - cut) < 0.04 for r in out):
            continue
        r = _lerp_rings(sorted(out, key=lambda x: x["t"]), cut)
        r["t"] = cut
        r.pop("tip", None)
        out.append(r)
    return sorted(out, key=lambda r: r["t"])


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
               cap_start, cap_end, arcs=None, openings=None, seam=None,
               part_key=None):
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
    openings = openings or []

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
        span = p.get("span")
        unit = p.get("unit")
        if span is not None and unit is not None:
            unit = _respan_unit(unit, span, n_sides)
        elif unit is None:
            unit = _superellipse(n_sides, exp, span)
        pts = []
        for (cx, sy) in unit:
            wh, dh = (w_hi, d_hi) if sy >= 0 else (w_lo, d_lo)
            pts.append(c + side * (cx * wh) + other * (sy * dh))
        return pts, False, vcoord

    geom = [ring_geometry(ri) for ri in range(nrings)]
    cache = {}

    def vert(ri, k, mat):
        """Vertex at ring `ri`, column `k` in 0..n_sides, for one material.

        When `seam` is given, ring 0 does not create vertices at all — it
        returns the ones another part already placed. Two lofts that merely
        *touch* leave a rim in one plane (which z-fights) or a hairline gap
        (which you can see through); sharing the vertices makes them one
        surface, so there is no junction left to go wrong.

        Column n_sides is column 0 again in space but u=1 in the chart. A
        closed loop cannot be unwrapped without a seam: sharing that column
        would make the wrap quad run backwards across the entire chart, and
        in the atlas it smears its material over the whole cell — erasing
        every band rasterized before it, or taking the cell if it is the
        band.
        """
        if seam is not None and ri == 0:
            return seam[k % len(seam)]
        pts, collapsed, vcoord = geom[ri]
        key = (ri, -1 if collapsed else k, mat)
        if key in cache:
            return cache[key]
        pt = pts[0] if collapsed else pts[k if len(pts) > n_sides
                                          else k % n_sides]
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

    def in_opening(ri, k):
        """Is the quad at band `ri`, column `k` inside a declared window?"""
        if not openings:
            return False
        ta = params[ri].get("t", ri / max(nrings - 1, 1))
        tb = params[ri + 1].get("t", (ri + 1) / max(nrings - 1, 1))
        tm = 0.5 * (ta + tb)
        # the quad spans ring points k..k+1, so its angle is the same one
        # `band_material` uses — a half-column offset here puts every window
        # off-centre by 360/(2*sides) degrees
        am = 360.0 * (k + 1) / n_sides
        for o in openings:
            if not (o["t"][0] <= tm <= o["t"][1]):
                continue
            lo, hi = o["arc"]
            a = am
            while a < lo:
                a += 360.0
            if lo <= a <= hi:
                return True
        return False

    for ri in range(nrings - 1):
        lo_flat = geom[ri][1]
        hi_flat = geom[ri + 1][1]
        if lo_flat and hi_flat:
            continue
        for k in range(n_sides):
            if in_opening(ri, k):
                continue
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

    def record_end_ring(key):
        """Remember the last ring's vertices so a part can continue from it."""
        if nrings < 2:
            return
        ri = nrings - 1
        if geom[ri][1]:
            return                      # collapsed to a point: nothing to join
        out.end_rings[key] = [vert(ri, k, band_material(ri - 1, k % n_sides))
                              for k in range(n_sides)]

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
    if part_key is not None:
        record_end_ring(part_key)
    return roles


def _shape_resolver(model, part, n_sides):
    """`shape=` -> (superellipse exponent, outline points or None).

    Shared by every generator with a cross-section. Living inside one of them
    is how `shape=` came to be honoured by `loft` and silently dropped by
    `sweep`: the key parses on any part, so a profiled horn compiled without a
    word and came out a plain cone.
    """
    profiles = getattr(model, "profiles", {}) or {}

    def shape_of(name, fallback_exp=2.0, fallback_unit=None):
        if not name:
            return fallback_exp, fallback_unit
        if name in SHAPE_EXP:
            return SHAPE_EXP[name], None
        if name in profiles:
            pr = profiles[name]
            return fallback_exp, profile_unit(pr["points"], pr["mirror"], n_sides)
        raise WamError(
            "part %r: shape=%r is neither a built-in (%s) nor a declared "
            "profile (%s)"
            % (part["name"], name, ", ".join(sorted(SHAPE_EXP)),
               ", ".join(sorted(profiles)) or "none"))
    return shape_of


def build_loft(out, model, bones, part, suffix="", reflect=False):
    n_sides = int(part.get("sides", STYLE_SIDES.get(model.style, 8)))
    profiles = getattr(model, "profiles", {}) or {}

    def shape_of(name, fallback_exp, fallback_unit):
        """Resolve a `shape=` to either a superellipse exponent or an outline."""
        if not name:
            return fallback_exp, fallback_unit
        if name in SHAPE_EXP:
            return SHAPE_EXP[name], None
        if name in profiles:
            pr = profiles[name]
            return fallback_exp, profile_unit(pr["points"], pr["mirror"], n_sides)
        raise WamError(
            "part %r: shape=%r is neither a built-in (%s) nor a declared "
            "profile (%s)"
            % (part["name"], name, ", ".join(sorted(SHAPE_EXP)),
               ", ".join(sorted(profiles)) or "none"))

    default_span = part.get("arc")

    def span_of(r):
        sp = r.get("arc", default_span)
        return None if sp is None else (sp[0] / 360.0, sp[1] / 360.0)

    default_exp, default_unit = shape_of(part.get("shape"), 2.0, None)
    base_mat = part.get("material") or "default"
    rings = sorted(part["rings"], key=lambda r: r["t"])
    if len(rings) < 2:
        raise WamError("loft %r needs at least 2 rings" % part["name"])

    if "chain" in part:
        chain = chain_bones(bones, part["chain"][0], part["chain"][1], suffix)
        pts = [chain[0].head] + [b.tail for b in chain]
        # `offset=` used to apply only to the single-bone form, so on a chain
        # it parsed, sat in the part's own key set, and moved nothing at all.
        off = np.array(part.get("offset", (0.0, 0.0, 0.0)), dtype=float)
        if reflect:
            off = np.array([-off[0], off[1], off[2]])
        if np.linalg.norm(off) > 0:
            pts = [p + off for p in pts]
        sampler = PathSampler(pts)
        skin_for = lambda t: _chain_skin(sampler, chain, t)
        rings = _rings_at_joints(rings, sampler)
    elif "bone" in part:
        host = resolve_bone_ref(bones, part["bone"], suffix)
        at = part.get("at", 1.0)
        if part.get("around") is not None:
            origin = around_origin(part, host, at, out, suffix, reflect)
        else:
            origin = host.point_at(at)
        origin = origin + np.array(part.get("offset", (0, 0, 0)), dtype=float)
        first_w = part["rings"][0].get("w", 0.05) if part["rings"] else 0.05
        origin, normal = snap_on(out, part, origin, suffix,
                                 default_inset=max(0.012, first_w * 0.25),
                                 footprint=first_w * 0.6)
        d = part_dir_on_bone(part, host, reflect)
        if normal is not None and part.get("press", True):
            d = _record_press(out, part, suffix, reflect, d,
                              _pressed_dir(part, normal))
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
        exp, unit = shape_of(r.get("shape"), default_exp, default_unit)
        span = span_of(r)
        w = r["w"]
        d = r.get("d", w)
        sect = dict(w=w, d=d, exp=exp, roll=0.0, t=t, span=span)
        if unit is not None:
            sect["unit"] = unit
            key = part["name"] + (".r" if reflect else suffix)
            out.sections[key] = max(
                [out.sections.get(key, unit), unit], key=section_concavity)
        sect["shape_name"] = r.get("shape") or part.get("shape")
        for key in ("wtop", "wbot", "dtop", "dbot"):
            if key in r:
                sect[key] = r[key]
        if r.get("tip"):
            sect = dict(w=0.0, d=0.0, exp=exp, roll=0.0, t=t, span=span)
            if unit is not None:
                sect["unit"] = unit
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
                "loft %r folds between rings %.2f and %.2f: the path bends "
                "%.0f\u00b0 and those rings are only %.3f apart, which gives a "
                "bend reach of %.3f against a ring radius of %.3f. **The "
                "spacing is usually the problem, not the width** — a ring "
                "sitting close to another (often one auto-inserted at a joint) "
                "folds at any radius, and moving it toward the middle of the "
                "bone fixes it while letting the section get wider, not "
                "narrower."
                % (part["name"], rings[i]["t"], rings[i + 1]["t"],
                   math.degrees(theta), L, reach, rmax))

    _check_frame_ref(out, part, tangents, ref)

    v0 = len(out.verts)
    t0 = len(out.tris)
    key_self = part["name"] + (".r" if reflect else suffix)
    seam = None
    if part.get("continues"):
        ref = part["continues"]
        src = None
        for cand in (ref + (".r" if reflect else suffix), ref):
            if cand in out.end_rings:
                src = cand
                break
        if src is None:
            raise WamError(
                "loft %r: continues=%r — no part by that name has been built "
                "yet with an open end to grow from (it must appear earlier in "
                "the file, and must not be capped at that end)"
                % (key_self, ref))
        out.continues[key_self] = src
        seam = out.end_rings[src]
        if len(seam) != n_sides:
            raise WamError(
                "loft %r: continues=%r, but that part has %d sides and this "
                "one has %d — a shared ring needs the same count on both"
                % (key_self, src, len(seam), n_sides))
    roles = _emit_tube(out, centers, axes, params, mats, skins, n_sides,
                       "none" if seam is not None else part.get("cap_start", "flat"),
                       part.get("cap_end", "flat"), arcs=arcs,
                       openings=part.get("openings"), seam=seam,
                       part_key=key_self)
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
    sweep_exp, sweep_unit = _shape_resolver(model, part, n_sides)(part.get("shape"))
    base_mat = part.get("material") or "default"
    mat = out.material(base_mat, mat_rgb(model, base_mat))
    if part.get("double_sided"):
        out.double_sided_materials.add(base_mat)
    if not part["segs"]:
        raise WamError("sweep %r has no segs" % part["name"])
    # A sweep may ride a bone chain: the swept curve is still authored with
    # segments, but its rings bind along the chain by arc length, so the
    # shape you tuned animates instead of being welded to one bone.
    chain = chain_sampler = None
    if "chain" in part:
        chain = chain_bones(bones, part["chain"][0], part["chain"][1], suffix)
        chain_sampler = PathSampler([chain[0].head] + [b.tail for b in chain])
    if part.get("bone") is not None:
        host = resolve_bone_ref(bones, part["bone"], suffix)
        origin = host.point_at(part.get("at", 1.0))
    elif chain is not None:
        host = chain[0]
        origin = host.point_at(part.get("at", 0.0))
    else:
        raise WamError("sweep %r needs bone= or bones=a..b" % part["name"])
    origin = origin + np.array(part.get("offset", (0, 0, 0)), dtype=float)
    origin, normal = snap_on(out, part, origin, suffix,
                             default_inset=max(0.012, part["segs"][0]["r"] * 0.8),
                             footprint=part["segs"][0]["r"] * 1.2)
    if "dir" in part or chain is None:
        d = resolve_dir(part.get("dir", "up"), part.get("pitch", 0.0),
                        part.get("yaw", 0.0), part.get("tilt", 0.0))
    else:
        d = chain[0].dir / np.linalg.norm(chain[0].dir)
    if normal is not None and part.get("press", True):
        d = _record_press(out, part, suffix, reflect, d,
                          _pressed_dir(part, normal))

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
        sect = dict(w=2 * r, d=2 * r, exp=sweep_exp,
                    t=acc_len[0] / total_len)
        if sweep_unit is not None:
            sect["unit"] = sweep_unit
        params.append(sect)
        if "material" in seg:
            if part.get("double_sided"):
                out.double_sided_materials.add(seg["material"])
            mats.append(out.material(seg["material"],
                                     mat_rgb(model, seg["material"])))
        else:
            mats.append(mat)
        if "skin" in seg:
            skins.append(_explicit_skin(bones, seg["skin"], suffix, part["name"]))
        elif chain is not None:
            skins.append(_chain_skin(chain_sampler, chain,
                                     acc_len[0] / total_len))
        else:
            skins.append(skin)
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
        # `down=`/`back=` are the negations of `up=`/`fwd=`, so a bend never
        # has to be expressed as a negative number whose sign you guessed
        rise = seg.get("up", 0.0) - seg.get("down", 0.0)
        ahead = seg.get("fwd", 0.0) - seg.get("back", 0.0)
        if rise:
            axis = np.cross(tang, Y)
            R = rot_axis(axis, rise) @ R
        if ahead:
            axis = np.cross(tang, Z)
            R = rot_axis(axis, ahead) @ R
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
    key = part["name"] + (".r" if reflect else suffix)
    out.part_ranges[key] = (v0, len(out.verts))
    # record where the bend actually took the tip, so the lint can report it:
    # a sweep's bend axis is not visible in the source, and until now the
    # render was the only way to find out which way it went
    refl = np.array([-1.0, 1.0, 1.0]) if reflect else np.array([1.0, 1.0, 1.0])
    out.sweep_paths[key] = (np.asarray(axes[0][2]) * refl,
                            np.asarray(axes[-1][2]) * refl,
                            np.asarray(centers[0]) * refl,
                            np.asarray(centers[-1]) * refl)


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

    # A membrane is exactly the polygon its ribs bound, and the classic
    # failure is an outline that never closes: the leading rib starts at the
    # body, every other rib starts at one hub, so the trailing boundary ends
    # at the hub instead of returning to the body. That is a splayed hand —
    # no membrane exists behind the limb, and no amount of tuning rib lengths
    # or sweep angles fixes it, because the shape is wrong, not the numbers.
    starts = [np.asarray(sp.at(0.0)[0], dtype=float) for sp in spars]
    pts = np.array(out.verts[v0:len(out.verts)]) if len(out.verts) > v0 else None
    if len(spars) >= 4 and pts is not None and len(pts):
        size = float(np.max(pts.max(axis=0) - pts.min(axis=0)))
        gap = float(np.linalg.norm(starts[0] - starts[-1]))
        if size > 0.35 and gap > 0.35 * size:
            out.warnings.append(
                "web %r: the outline does not close — the first rib begins "
                "%.2f from where the last one does, %.0f%% of the membrane's "
                "own size, so the trailing edge ends at the hub instead of "
                "coming back toward the body. That is a splayed hand, not a "
                "wing; anchor the trailing ribs further up the leading edge "
                "with from=<elbow> and close to the body"
                % (part["name"] + (".r" if reflect else suffix),
                   gap, 100.0 * gap / size))

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
    if part.get("around") is not None:
        origin = around_origin(part, host, at, out, suffix, reflect)
    else:
        origin = host.point_at(at)
    origin = origin + np.array(part.get("offset", (0, 0, 0)), dtype=float)
    size = part.get("size", 0.05)
    origin, normal = snap_on(
        out, part, origin, suffix,
        footprint=max(part.get("w", size), part.get("d", size)) * 0.6)
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

    # Press the prim flat against the surface it was placed on. A prim's mass
    # hangs *below* its origin (the box's top face is the origin, so a hoof
    # sits under a leg), so it is -Y that has to follow the outward normal —
    # aligning +Y would bury the whole block in the surface instead.
    if normal is not None and part.get("press", True):
        _record_press(out, part, suffix, reflect,
                      np.array([0.0, -1.0, 0.0]), normal)
        R = _align_rotation(np.array([0.0, -1.0, 0.0]), normal)
        for i in range(v0, len(out.verts)):
            out.verts[i] = R @ (out.verts[i] - origin) + origin
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


def _thicken(out, key, thickness):
    """Give an open surface a wall, so its cut edges read as plate.

    An `arc=` or `opening` leaves a zero-thickness surface: correct as a
    surface, and it reads as paper the moment you can see the cut. The shell
    is duplicated inward along its own normals and every boundary edge is
    bridged, which turns the cut into a rim.

    Boundaries are found after welding by **position**, not by vertex index.
    A lofted tube is unwrapped along a UV seam that duplicates a whole column,
    and those duplicates look exactly like an open edge to an index-based
    test — bridging them would build a wall straight through the middle of an
    otherwise closed surface.
    """
    from .render import weld_groups
    v0, v1 = out.part_ranges[key]
    idx = [i for i, t in enumerate(out.tris)
           if all(v0 <= c < v1 for c in t)]
    if not idx:
        return 0
    P = np.array([out.verts[i] for i in range(v0, v1)], dtype=float)
    T = np.array([[c - v0 for c in out.tris[i]] for i in idx], dtype=int)

    inv = weld_groups(P)
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    fn = np.cross(b - a, c - a)
    acc = np.zeros((int(inv.max()) + 1, 3))
    for k in range(3):
        np.add.at(acc, inv[T[:, k]], fn)
    n = acc / np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-12)
    N = n[inv]

    edge = {}
    for t in T:
        for k in range(3):
            u, w = inv[t[k]], inv[t[(k + 1) % 3]]
            edge[(min(u, w), max(u, w))] = edge.get((min(u, w), max(u, w)), 0) + 1
    border = {e for e, cnt in edge.items() if cnt == 1}
    if not border:
        return 0

    inner = {}
    for i in range(len(P)):
        gi = v0 + i
        inner[gi] = out.add_vert(P[i] - N[i] * thickness,
                                 list(out.skin[gi]), uv=tuple(out.uvs[gi]))
    for i in idx:
        x, y, z = out.tris[i]
        out.add_tri(inner[z], inner[y], inner[x], out.tri_mat[i])

    made = 0
    for i in idx:
        t = out.tris[i]
        for k in range(3):
            g0, g1 = t[k], t[(k + 1) % 3]
            u, w = inv[g0 - v0], inv[g1 - v0]
            if (min(u, w), max(u, w)) not in border:
                continue
            # wound to oppose both neighbours: g1->g0 matches the outer
            # face's g0->g1, and inner[g0]->inner[g1] matches the inner
            # face's reversed inner[g1]->inner[g0]
            out.add_tri(g1, g0, inner[g0], out.tri_mat[i])
            out.add_tri(g1, inner[g0], inner[g1], out.tri_mat[i])
            made += 1
    out.part_ranges[key] = (v0, len(out.verts))
    return made


def _facet(out, key):
    """Give a part its own hard-edged shading by splitting its vertices.

    Normals are accumulated over *coincident* vertices so that a texture seam
    or a material band changes a surface's colour without creasing it. That is
    the right default and it is exactly what makes a faceted look impossible
    to ask for: every attempt to hand a face its own normal gets welded back
    into the smooth average. So each triangle is given private copies of its
    corners, tagged with a shading group of its own, and the welding pass is
    told to keep them apart.
    """
    v0, v1 = out.part_ranges[key]
    tris = [(i, t) for i, t in enumerate(out.tris)
            if v0 <= t[0] < v1 and v0 <= t[1] < v1 and v0 <= t[2] < v1]
    if not tris:
        return
    for ti, (a, b, c) in tris:
        group = len(out.verts) + 1          # unique per triangle
        new = []
        for vi in (a, b, c):
            new.append(len(out.verts))
            out.verts.append(np.array(out.verts[vi], dtype=float))
            out.skin.append(list(out.skin[vi]))
            out.uvs.append(tuple(out.uvs[vi]))
            out.shade_group.append(group)
        out.tris[ti] = (new[0], new[1], new[2])
    out.part_ranges[key] = (v0, len(out.verts))


def _lay_against(out, part, key, suffix, reflect):
    """Slide a finished part until it rests on another one.

    The standoff between a cape and a back, or a shield and a forearm, is not
    a number the author knows — it is whatever makes the two surfaces meet.
    Written as `fwd=-0.135` it is a guess that silently stops being true when
    either side changes shape, and a garment floating a centimetre clear of
    the body passes every placement, coverage and intersection check there is.
    """
    ref = part["rest"]
    layer = float(part.get("layer", 0.008))
    tgt = None
    for cand in (ref + (".r" if reflect else suffix), ref):
        if cand in out.part_ranges:
            tgt = cand
            break
    if tgt is None:
        raise WamError(
            "part %r: rest=%r names no part built before it — the surface "
            "to rest on has to already exist" % (key, ref))
    if tgt == key:
        raise WamError("part %r cannot rest against itself" % key)
    a0, a1 = out.part_ranges[key]
    b0, b1 = out.part_ranges[tgt]
    P = np.array(out.verts[a0:a1], dtype=float)
    T = np.array(out.verts[b0:b1], dtype=float)
    if not len(P) or not len(T):
        return

    if part.get("push"):
        if part["push"] not in BASE_DIRS:
            raise WamError("part %r: push=%r is not a direction (%s)"
                           % (key, part["push"], ", ".join(sorted(BASE_DIRS))))
        u = np.array(BASE_DIRS[part["push"]], dtype=float)
        if reflect:
            u = np.array([-u[0], u[1], u[2]])
    else:
        u = P.mean(axis=0) - T.mean(axis=0)
        n = np.linalg.norm(u)
        if n < 1e-9:
            raise WamError(
                "part %r: rest=%r, but the two share a centre so there is "
                "no side to rest on — give push=<direction>" % (key, tgt))
        u = u / n
    u = u / max(np.linalg.norm(u), 1e-12)

    if not part.get("push"):
        # A skirt surrounds the hips; a cape hangs behind a back. Only the
        # second can rest on a side, and the difference shows up as the push
        # direction coming out parallel to the part's own long axis — at
        # which point sliding it "clear" walks it down the body to the ankles.
        ext = P.max(axis=0) - P.min(axis=0)
        axis = np.zeros(3)
        axis[int(np.argmax(ext))] = 1.0
        if abs(float(np.dot(u, axis))) > 0.9:
            raise WamError(
                "part %r: rest=%r, but the two are coaxial — %r surrounds "
                "%r rather than sitting on one side of it, so there is no "
                "direction to slide it that would make it 'rest'. A garment "
                "that wraps clears its body by being wider, not by being "
                "moved: size its rings past the part underneath. Use "
                "push=<direction> only if you really did mean to slide it."
                % (key, tgt, key, tgt))

    from . import checks as _checks
    tris = np.asarray(out.tris, dtype=int)

    def sub(v0, v1):
        sel = tris[((tris >= v0) & (tris < v1)).all(axis=1)] - v0 \
            if len(tris) else np.zeros((0, 3), dtype=int)
        return sel

    shift = _checks.rest_shift(P, sub(a0, a1), T, sub(b0, b1), u, layer)
    for i in range(a0, a1):
        out.verts[i] = out.verts[i] + u * shift
    out.rests[key] = (tgt, shift, u.copy())


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
        rel = bone_relative_dir(host, g, "group %r" % g["name"])
        if rel is not None:
            # stated relative to the host bone, so it survives a repose
            R = _align_rotation(np.array([0.0, 1.0, 0.0]), rel)
            R = rot_axis(rel, g.get("spin", 0.0)) @ R
            for k, fn in (("pitch", rot_x), ("yaw", rot_y), ("tilt", rot_z)):
                if g.get(k):
                    R = fn(g[k]) @ R
        elif g.get("dir"):
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
            if part.get("kind") == "loft" and part.get("thickness"):
                key = part["name"] + (".r" if reflect else suffix)
                if not _thicken(out, key, float(part["thickness"])):
                    out.warnings.append(
                        "part %r has thickness= but no open edge to wall in — "
                        "a closed surface has no cut to show, so it only "
                        "doubled the triangles. Give it an arc= or an "
                        "opening=, or drop the thickness" % key)
            if part.get("faceted", getattr(model, "shading", "smooth") == "faceted"):
                _facet(out, part["name"] + (".r" if reflect else suffix))
            if part.get("rest"):
                _lay_against(out, part,
                             part["name"] + (".r" if reflect else suffix),
                             suffix, reflect)
    finalize_group()
    return out
