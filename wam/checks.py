"""The `checks` section: measurements the compiler re-runs on every build.

Proportion, clearance, rig quality, and animation coverage are exactly the
properties that a human (or a model) verifies once, by eye, and then never
re-verifies after the next edit. Everything here exists so a judgement can be
written down as a number and enforced forever after.

Expressions are parsed with `ast` and walked over a whitelist — no builtins,
no attribute access beyond dotted bone names, no calls but the ones below.
"""
import ast

import numpy as np

# Vertex sets are subsampled before any O(n*m) comparison. Parts in this
# language run to a few hundred vertices, so this is rarely reached, and a
# proximity check that is a hair pessimistic beats one nobody runs.
_SAMPLE_CAP = 600
_CLIP_SAMPLES = 60      # clip tests are O(points x triangles), twice
_ANIM_FRAMES = 12


class CheckError(Exception):
    pass


class _Ref:
    """An unresolved name: a bone, a part, or an animation."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


def _name_of(ref):
    return ref.name if isinstance(ref, _Ref) else str(ref)


def _number(v):
    if isinstance(v, _Ref):
        raise CheckError("%r is a name, not a measurement — wrap it in a "
                         "function like len() or y()" % v.name)
    return float(v)


def _subsample(a):
    if len(a) <= _SAMPLE_CAP:
        return a
    return a[:: (len(a) + _SAMPLE_CAP - 1) // _SAMPLE_CAP]


def _min_pair_distance(A, B):
    """Closest approach between two point sets, in chunks to bound memory."""
    A, B = _subsample(A), _subsample(B)
    best = float("inf")
    for i in range(0, len(A), 128):
        chunk = A[i:i + 128]
        d = np.linalg.norm(chunk[:, None, :] - B[None, :, :], axis=2)
        best = min(best, float(d.min()))
    return best


def points_inside(points, A, B, C):
    """Which points lie inside the closed surface of triangles ABC.

    Ray parity along +X: an odd number of crossings means inside.
    """
    n = len(points)
    if not n or not len(A):
        return np.zeros(n, dtype=bool)
    e1, e2 = B - A, C - A
    d = np.array([1.0, 0.0, 0.0])
    h = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, h)
    live = np.abs(det) > 1e-12
    if not live.any():
        return np.zeros(n, dtype=bool)
    e1, e2, A2, h = e1[live], e2[live], A[live], h[live]
    inv = 1.0 / det[live]
    out = np.zeros(n, dtype=bool)
    for pi, p in enumerate(points):
        s = p - A2
        u = inv * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, e1)
        v = inv * q[:, 0]                       # d · q with d = +X
        t = inv * np.einsum("ij,ij->i", e2, q)
        hit = (u >= 0) & (u <= 1) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
        out[pi] = bool(int(hit.sum()) % 2)
    return out


def _crossing_depth(P0, P1, A, B, C, chunk=64):
    """How far segments P0→P1 poke through the surface of triangles ABC.

    Depth is perpendicular to the pierced triangle — how far the buried edge
    endpoint sits behind that surface — so it measures penetration rather
    than the length of whatever edge happened to cross. Working from surface
    crossings rather than volume containment keeps this meaningful for *open*
    parts: a `cap none` kilt or a thickness-free `web` has no inside for a
    containment test to ask about, and cloth is exactly where clipping
    matters most.
    """
    if not len(P0) or not len(A):
        return 0.0
    e1, e2 = B - A, C - A
    nrm = np.cross(e1, e2)
    plane_d = np.einsum("tj,tj->t", A, nrm)
    best = 0.0
    for i in range(0, len(P0), chunk):
        p0, p1 = P0[i:i + chunk], P1[i:i + chunk]
        d = p1 - p0
        h = np.cross(d[:, None, :], e2[None, :, :])
        det = np.einsum("mtj,tj->mt", h, e1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = p0[:, None, :] - A[None, :, :]
        u = inv * np.einsum("mtj,mtj->mt", s, h)
        q = np.cross(s, e1[None, :, :])
        v = inv * np.einsum("mj,mtj->mt", d, q)
        t = inv * np.einsum("tj,mtj->mt", e2, q)
        hit = (ok & (u >= 0) & (u <= 1) & (v >= 0) & (u + v <= 1)
               & (t > 1e-9) & (t < 1 - 1e-9))
        if not hit.any():
            continue
        # Signed height of each endpoint over each pierced triangle's plane.
        # Faces are wound outward, so the endpoint with negative height is
        # the buried one and its depth is how far it sits behind the surface.
        nlen = np.sqrt(np.einsum("tj,tj->t", nrm, nrm))
        nlen = np.where(nlen < 1e-18, 1.0, nlen)
        h0 = (np.einsum("mj,tj->mt", p0, nrm) - plane_d[None, :]) / nlen
        h1 = (np.einsum("mj,tj->mt", p1, nrm) - plane_d[None, :]) / nlen
        depth = np.maximum(-np.minimum(h0, h1), 0.0)
        best = max(best, float(depth[hit].max()))
    return best


def _dist_to_surface(p, A, B, C):
    """Distance from p to the nearest point on a triangle soup."""
    ab, ac, ap = B - A, C - A, p - A
    n = np.cross(ab, ac)
    nn = np.einsum("ij,ij->i", n, n)
    nn = np.where(nn < 1e-18, 1.0, nn)
    # barycentric coordinates of the projection onto each triangle's plane
    d = np.einsum("ij,ij->i", n, ap) / nn
    proj = ap - n * d[:, None]
    d00 = np.einsum("ij,ij->i", ab, ab)
    d01 = np.einsum("ij,ij->i", ab, ac)
    d11 = np.einsum("ij,ij->i", ac, ac)
    d20 = np.einsum("ij,ij->i", proj, ab)
    d21 = np.einsum("ij,ij->i", proj, ac)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) < 1e-18, 1.0, den)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    inside = (v >= 0) & (w >= 0) & (v + w <= 1)
    face = np.abs(d) * np.sqrt(nn)

    def seg(P0, P1):
        e = P1 - P0
        ee = np.einsum("ij,ij->i", e, e)
        ee = np.where(ee < 1e-18, 1.0, ee)
        t = np.clip(np.einsum("ij,ij->i", p - P0, e) / ee, 0.0, 1.0)
        return np.linalg.norm(p - (P0 + e * t[:, None]), axis=1)

    edge = np.minimum(np.minimum(seg(A, B), seg(B, C)), seg(C, A))
    return float(np.where(inside, face, edge).min())


class Env:
    """Everything a check expression can name, bound to one compiled model."""

    def __init__(self, model, bones, mesh, V):
        self.model = model
        self.bones = bones
        self.mesh = mesh
        self.V = V
        self.T = mesh.arrays()[1]
        self.ranges = mesh.part_ranges
        self._posed = {}
        self._edges = {}

    # ---- resolution -----------------------------------------------------

    def bone(self, ref, want="head"):
        name = _name_of(ref)
        for suffix in ("head", "tail", "mid"):
            if name.endswith("." + suffix):
                name, want = name[:-len(suffix) - 1], suffix
                break
        b = self.bones.get(name) or self.bones.get(name + ".l")
        if b is None:
            raise CheckError("no bone named %r" % name)
        return b, want

    def is_bone(self, name):
        base = name
        if name.rsplit(".", 1)[-1] in ("head", "tail", "mid"):
            base = name.rsplit(".", 1)[0]
        return bool(self.bones.get(base) or self.bones.get(base + ".l"))

    def part_range(self, ref):
        name = _name_of(ref)
        for key in (name, name + ".l"):
            if key in self.ranges:
                return self.ranges[key]
        raise CheckError("no part named %r" % name)

    def part_verts(self, ref):
        v0, v1 = self.part_range(ref)
        return self.V[v0:v1]

    def part_verts_in(self, ref, V=None):
        v0, v1 = self.part_range(ref)
        return (self.V if V is None else V)[v0:v1]

    def point(self, ref):
        name = _name_of(ref)
        if self.is_bone(name):
            b, want = self.bone(ref)
            return {"head": b.head, "tail": b.tail,
                    "mid": (b.head + b.tail) / 2.0}[want]
        for key in (name, name + ".l"):
            if key in self.ranges:
                chunk = self.part_verts(ref)
                return (chunk.min(axis=0) + chunk.max(axis=0)) / 2.0
        raise CheckError("%r is neither a bone nor a part" % name)

    def direction(self, ref):
        name = _name_of(ref)
        if not self.is_bone(name):
            raise CheckError("%r is not a bone, so it has no direction — use "
                             "the three-point form angle(a,b,c) for parts"
                             % name)
        b, _ = self.bone(ref)
        if b.len <= 0:
            raise CheckError("bone %r has zero length, so no direction" % b.name)
        return b.dir / np.linalg.norm(b.dir)

    def anim(self, ref):
        name = _name_of(ref)
        for a in self.model.anims:
            if a["name"] == name:
                return a
        raise CheckError("no animation named %r" % name)

    def part_tris(self, ref):
        """Triangle indices belonging to a part (both mirrored halves)."""
        v0, v1 = self.part_range(ref)
        if not len(self.T):
            return self.T
        sel = (self.T[:, 0] >= v0) & (self.T[:, 0] < v1)
        return self.T[sel]

    def tri_verts(self, ref, V=None):
        """(A, B, C) corner arrays for a part's triangles, in some pose."""
        V = self.V if V is None else V
        t = self.part_tris(ref)
        return V[t[:, 0]], V[t[:, 1]], V[t[:, 2]]

    def part_edges(self, ref, V=None):
        """(P0, P1) endpoint arrays for a part's unique triangle edges."""
        key = _name_of(ref)
        if key not in self._edges:
            t = self.part_tris(ref)
            if not len(t):
                self._edges[key] = np.zeros((0, 2), dtype=int)
            else:
                e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
                self._edges[key] = np.unique(np.sort(e, axis=1), axis=0)
        e = self._edges[key]
        Vs = self.V if V is None else V
        return Vs[e[:, 0]], Vs[e[:, 1]]

    def part_bones(self, ref):
        """Bone names a part is meaningfully skinned to."""
        v0, v1 = self.part_range(ref)
        names = set()
        for i in range(v0, v1):
            names.update(n for n, w in self.mesh.skin[i] if w > 0.05)
        return names

    # ---- animation ------------------------------------------------------

    def posed(self, ref):
        """(list of posed vertex arrays, list of bone-transform dicts)."""
        from . import animation as wanim
        anim = self.anim(ref)
        if anim["name"] in self._posed:
            return self._posed[anim["name"]]
        order = list(self.bones.values())
        frames, mats = [], []
        for i in range(_ANIM_FRAMES):
            rots = wanim.anim_rotations_at(self.model, self.bones, anim,
                                           i / _ANIM_FRAMES)
            frames.append(wanim.skin_verts(self.mesh, self.bones, order, rots))
            mats.append(wanim.global_transforms(self.bones, order, rots))
        self._posed[anim["name"]] = (frames, mats)
        return frames, mats


def build_functions(env):
    """The callable vocabulary of the `checks` section."""

    def between(u, v):
        return float(np.degrees(np.arccos(float(np.clip(u @ v, -1.0, 1.0)))))

    def angle(*refs):
        """angle(a,b): between two bone directions.
        angle(a,b,c): at b, in the corner a-b-c. Both unsigned, 0..180."""
        if len(refs) == 2:
            return between(env.direction(refs[0]), env.direction(refs[1]))
        if len(refs) == 3:
            a, b, c = (env.point(r) for r in refs)
            u, v = a - b, c - b
            if np.linalg.norm(u) < 1e-9 or np.linalg.norm(v) < 1e-9:
                raise CheckError("angle() needs three distinct points")
            return between(u / np.linalg.norm(u), v / np.linalg.norm(v))
        raise CheckError("angle() takes two bones or three points")

    def elevation(ref):
        d = env.direction(ref)
        return float(np.degrees(np.arcsin(float(np.clip(d[1], -1.0, 1.0)))))

    def heading(ref):
        d = env.direction(ref)
        if abs(d[0]) < 1e-9 and abs(d[2]) < 1e-9:
            raise CheckError("bone points straight up or down, so its "
                             "heading is undefined")
        return float(np.degrees(np.arctan2(d[0], d[2])))

    def bone_len(ref):
        b, _ = env.bone(ref)
        if b.len <= 0:
            raise CheckError("bone %r has zero length" % b.name)
        return float(b.len)

    def axis_size(ref, i):
        c = env.part_verts(ref)
        return float(c[:, i].max() - c[:, i].min())

    def extreme(ref, i, hi):
        c = env.part_verts(ref)
        return float(c[:, i].max() if hi else c[:, i].min())

    def volume(ref):
        """Enclosed volume of a part, by the divergence theorem.

        Meaningful for the closed solids every generator emits; a `web` with
        no thickness encloses nothing, so it reads near zero.
        """
        tris = env.part_tris(ref)
        if not len(tris):
            return 0.0
        a, b, c = env.V[tris[:, 0]], env.V[tris[:, 1]], env.V[tris[:, 2]]
        return float(abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)

    def area(ref):
        tris = env.part_tris(ref)
        if not len(tris):
            return 0.0
        a, b, c = env.V[tris[:, 0]], env.V[tris[:, 1]], env.V[tris[:, 2]]
        return float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2.0)

    def tri_count(ref):
        return float(len(env.part_tris(ref)))

    def vert_count(ref):
        v0, v1 = env.part_range(ref)
        return float(v1 - v0)

    def gap(a, b, anim=None):
        """Closest approach between two parts' surfaces.

        A proximity measure, not a penetration depth: parts that touch and
        parts that interpenetrate both read 0. Use it for things that must
        stay apart (`gap(sword, thigh.l) > 0.02`), and give it an animation
        to check the whole cycle rather than the rest pose.
        """
        if anim is None:
            return _min_pair_distance(env.part_verts(a), env.part_verts(b))
        a0, a1 = env.part_range(a)
        b0, b1 = env.part_range(b)
        frames, _ = env.posed(anim)
        return min(_min_pair_distance(f[a0:a1], f[b0:b1]) for f in frames)

    def clip_between(a, b, V=None):
        """Deepest interpenetration of two parts in one pose."""
        av, bv = env.part_verts_in(a, V), env.part_verts_in(b, V)
        # cheap reject: separated bounding boxes cannot interpenetrate
        if (np.any(av.max(axis=0) < bv.min(axis=0)) or
                np.any(bv.max(axis=0) < av.min(axis=0))):
            return 0.0
        ae0, ae1 = env.part_edges(a, V)
        be0, be1 = env.part_edges(b, V)
        # Each side reports how far it is buried behind the other's surface,
        # and the shallower reading wins: a long edge crossing a small
        # surface runs far past it without the two parts overlapping deeply,
        # so the larger number describes the edge, not the intersection.
        return min(_crossing_depth(ae0, ae1, *env.tri_verts(b, V)),
                   _crossing_depth(be0, be1, *env.tri_verts(a, V)))

    def clip(a, b, anim=None):
        """How deeply two parts pass through each other.

        `gap()` reads 0 for both touching and interpenetrating, so it cannot
        tell a snug fit from a leg through a kilt — and on a low-poly mesh it
        often reads a comfortable *positive* distance while the two surfaces
        cross, because the nearest vertices are nowhere near the crossing.
        This measures the other side of contact, and is 0 unless the surfaces
        genuinely pass through each other.
        """
        if anim is None:
            return clip_between(a, b)
        frames, _ = env.posed(anim)
        return max(clip_between(a, b, f) for f in frames)

    def asymmetry(ref=None):
        """Worst distance from a part to its own mirror image across X=0.

        Central parts should be near zero; a nonzero reading means a ring
        offset or an attachment that only exists on one side.
        """
        pts = env.part_verts(ref) if ref is not None else env.V
        pts = _subsample(pts)
        mirrored = pts * np.array([-1.0, 1.0, 1.0])
        worst = 0.0
        for i in range(0, len(mirrored), 128):
            chunk = mirrored[i:i + 128]
            d = np.linalg.norm(chunk[:, None, :] - pts[None, :, :], axis=2)
            worst = max(worst, float(d.min(axis=1).max()))
        return worst

    def influences(ref):
        """Most bones driving any one vertex of a part.

        A membrane or a cloth panel that reads 1 here is rigidly welded to a
        single bone and will not deform, however good it looks at rest.
        """
        v0, v1 = env.part_range(ref)
        return float(max((len(env.mesh.skin[i]) for i in range(v0, v1)),
                         default=0))

    def bone_count(ref=None):
        if ref is None:
            return float(len(env.bones))
        v0, v1 = env.part_range(ref)
        names = set()
        for i in range(v0, v1):
            names.update(n for n, w in env.mesh.skin[i] if w > 0.01)
        return float(len(names))

    def weight(part, bone_ref):
        """Strongest influence a given bone has anywhere in a part."""
        v0, v1 = env.part_range(part)
        b, _ = env.bone(bone_ref)
        best = 0.0
        for i in range(v0, v1):
            for n, w in env.mesh.skin[i]:
                if n == b.name:
                    best = max(best, w)
        return float(best)

    def moves(part, anim):
        """Furthest any vertex of a part travels from rest during an anim."""
        v0, v1 = env.part_range(part)
        rest = env.V[v0:v1]
        frames, _ = env.posed(anim)
        return max(float(np.linalg.norm(f[v0:v1] - rest, axis=1).max())
                   for f in frames)

    def travel(bone_ref, anim):
        """Furthest a bone's tail travels from rest during an anim."""
        b, _ = env.bone(bone_ref)
        _, mats = env.posed(anim)
        tail = np.append(b.tail, 1.0)
        return max(float(np.linalg.norm((G[b.name] @ tail)[:3] - b.tail))
                   for G in mats)

    def swing(bone_ref, anim):
        """Widest angular travel of a bone's direction over an anim.

        The positional twin of travel(): a shoulder can swing 40 degrees
        without its head moving at all, and a limb that reads 0 here is
        posed, not animated.
        """
        b, _ = env.bone(bone_ref)
        _, mats = env.posed(anim)
        dirs = []
        for G in mats:
            d = G[b.name][:3, :3] @ b.dir
            n = np.linalg.norm(d)
            dirs.append(d / n if n > 1e-12 else b.dir)
        worst = 0.0
        for i, u in enumerate(dirs):
            for v in dirs[i + 1:]:
                worst = max(worst, between(u, v))
        return worst

    def slide(part, anim):
        """Horizontal drift of a part while it is planted on the ground.

        This is the moonwalk bug made measurable: a contact foot must hold
        still in world space through stance, and a gait that flexes the knee
        during stance drags it instead. Frames are counted as planted when
        the part is within a hair of its own lowest point over the cycle.
        """
        v0, v1 = env.part_range(part)
        frames, _ = env.posed(anim)
        lows = [float(f[v0:v1, 1].min()) for f in frames]
        floor = min(lows)
        tol = max(0.02, (max(lows) - floor) * 0.15)
        planted = [f[v0:v1] for f, lo in zip(frames, lows) if lo <= floor + tol]
        if len(planted) < 2:
            return 0.0
        centers = np.array([[p[:, 0].mean(), p[:, 2].mean()] for p in planted])
        return float(np.linalg.norm(centers.max(axis=0) - centers.min(axis=0)))

    def lowest(anim):
        """Lowest point reached at any moment of an anim (feet through floor)."""
        frames, _ = env.posed(anim)
        return min(float(f[:, 1].min()) for f in frames)

    def highest(anim):
        frames, _ = env.posed(anim)
        return max(float(f[:, 1].max()) for f in frames)

    return {
        # points and distance
        "dist": lambda a, b: float(np.linalg.norm(env.point(a) - env.point(b))),
        "len": bone_len,
        "x": lambda r: float(env.point(r)[0]),
        "y": lambda r: float(env.point(r)[1]),
        "z": lambda r: float(env.point(r)[2]),
        # direction
        "angle": angle,
        "elevation": elevation,
        "heading": heading,
        # part extents
        "width": lambda r: axis_size(r, 0),
        "height": lambda r: axis_size(r, 1),
        "depth": lambda r: axis_size(r, 2),
        "span": lambda r: max(axis_size(r, i) for i in range(3)),
        "xmin": lambda r: extreme(r, 0, False), "xmax": lambda r: extreme(r, 0, True),
        "ymin": lambda r: extreme(r, 1, False), "ymax": lambda r: extreme(r, 1, True),
        "zmin": lambda r: extreme(r, 2, False), "zmax": lambda r: extreme(r, 2, True),
        "bottom": lambda r: extreme(r, 1, False),
        "top": lambda r: extreme(r, 1, True),
        # mass
        "volume": volume,
        "area": area,
        "tris": tri_count,
        "verts": vert_count,
        # clearance and symmetry
        "gap": gap,
        "clip": clip,
        "asymmetry": asymmetry,
        # rig
        "influences": influences,
        "bonecount": bone_count,
        "weight": weight,
        # animation
        "moves": moves,
        "travel": travel,
        "swing": swing,
        "slide": slide,
        "lowest": lowest,
        "highest": highest,
        # arithmetic helpers
        "abs": lambda v: abs(_number(v)),
        "min": lambda *vs: min(_number(v) for v in vs),
        "max": lambda *vs: max(_number(v) for v in vs),
    }


class _Lazy:
    """A global whose value costs enough that it should only be paid for
    when a check actually names it."""

    def __init__(self, fn):
        self.fn = fn
        self._value = None

    def value(self):
        if self._value is None:
            self._value = self.fn()
        return self._value


def _hard_pairs(env):
    """Pairs whose overlap the language itself asks for.

    `on=` is *defined* as sinking a part into the surface it sits on, a group
    prop is one rigid assembly, and the two halves of a mirrored part meet at
    the midline. Flagging these would make the check unusable, and no
    author-visible edit could ever satisfy it.
    """
    allowed = set()
    names = list(env.ranges)
    # the two halves of one mirrored part meet at the midline
    for n in names:
        if n.endswith(".l") and n[:-2] + ".r" in env.ranges:
            allowed.add(frozenset((n, n[:-2] + ".r")))
    # on= is *defined* as sinking a part into the surface it sits on
    for part in env.model.parts:
        base = part.get("on")
        if not base:
            continue
        for sa in ("", ".l", ".r"):
            for sb in ("", ".l", ".r"):
                allowed.add(frozenset((part["name"] + sa, base + sb)))
    # a web is stretched over its ribs, so it passes through the tubes that
    # wrap those same bones by construction
    for part in env.model.parts:
        if part.get("kind") != "web":
            continue
        for sa in ("", ".l", ".r"):
            key = part["name"] + sa
            if key not in env.ranges:
                continue
            web_bones = env.part_bones(key)
            for other in env.ranges:
                if other != key and env.part_bones(other) <= web_bones:
                    allowed.add(frozenset((key, other)))
    # everything authored inside one group is a single rigid assembly
    groups = {}
    for part in env.model.parts:
        g = part.get("group")
        if g is not None:
            groups.setdefault(id(g), []).append(part["name"])
    for members in groups.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                allowed.add(frozenset((a, b)))
    return allowed


def _soft_pairs(env):
    """Pairs that share or neighbour a bone.

    Consecutive body segments have to interpenetrate: a thigh starts inside
    the hip mass, a neck inside the chest. But so does a leg inside a kilt,
    and that one is a bug — so this exemption applies only to the blanket
    `noclip` sweep. Name a subject and it stops applying, which is what makes
    `noclip kilt` the useful form for cloth and props.
    """
    allowed = set()
    names = list(env.ranges)
    bones_of = {n: env.part_bones(n) for n in names}
    parent_of = {n: (b.parent.name if b.parent else None)
                 for n, b in env.bones.items()}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ba, bb = bones_of[a], bones_of[b]
            shared_joint = (
                bool(ba & bb)
                or any(parent_of.get(x) in bb for x in ba)
                or any(parent_of.get(y) in ba for y in bb)
                # siblings: digits off one wrist, a leg and a tail off one
                # pelvis — they all emerge from the same point and overlap
                or bool({parent_of.get(x) for x in ba} &
                        {parent_of.get(y) for y in bb} - {None}))
            if shared_joint:
                allowed.add(frozenset((a, b)))
    return allowed


def run_noclip(env, spec, funcs):
    """Evaluate one `noclip` statement. Returns (failures, measurements)."""
    clip = funcs["clip"]
    names = [n for n, (v0, v1) in env.ranges.items() if v1 - v0 >= 4]
    known = set(names)

    def resolve(listed):
        out = []
        for want in listed:
            hits = [n for n in names if n == want or n == want + ".l"
                    or n == want + ".r"]
            if not hits:
                raise CheckError("no part named %r" % want)
            out.extend(hits)
        return out

    subject = resolve(spec["subject"]) if spec["subject"] else names
    against = resolve(spec["against"]) if spec["against"] else names
    explicit = bool(spec["against"])

    pairs = set()
    for a in subject:
        for b in against:
            if a != b:
                pairs.add(frozenset((a, b)))
    if not explicit:
        # a named pair overrides everything; a named subject still skips the
        # overlaps the language mandates; the blanket sweep also forgives
        # parts that merely share a joint
        pairs -= _hard_pairs(env)
        if not spec.get("strict"):
            pairs -= _soft_pairs(env)
    for ex in spec["exempt"]:
        pairs -= {frozenset(resolve(list(ex)))} if len(ex) == 2 else set()
        pairs = {p for p in pairs
                 if not (len(p) == 2 and {x.rsplit(".", 1)[0] for x in p} == set(ex))}

    anims = []
    if spec["anim"] == "*":
        anims = [a["name"] for a in env.model.anims]
    elif spec["anim"]:
        anims = [spec["anim"]]

    tol = spec["depth"]
    hits = []
    for pair in sorted(pairs, key=lambda p: sorted(p)):
        a, b = sorted(pair)
        worst, where = clip(_Ref(a), _Ref(b)), "at rest"
        for an in anims:
            d = clip(_Ref(a), _Ref(b), _Ref(an))
            if d > worst:
                worst, where = d, "during %s" % an
        if worst > tol:
            hits.append((worst, a, b, where))
    hits.sort(reverse=True)

    failures = ["%r passes %.3f into %r %s (limit %.3f)"
                % (a, d, b, where, tol) for d, a, b, where in hits[:8]]
    if len(hits) > 8:
        failures.append("...and %d more clipping pairs" % (len(hits) - 8))
    scope = "all" if not spec["subject"] else ",".join(spec["subject"])
    measurements = []
    if not hits:
        measurements.append("noclip %s: %d pair(s) clear%s"
                            % (scope, len(pairs),
                               "" if not anims else " across " + ", ".join(anims)))
    return failures, measurements


def build_scalars(env, funcs):
    V = env.V
    ymin = float(V[:, 1].min())
    return {
        "asymmetry": _Lazy(lambda: funcs["asymmetry"]()),
        "height": float(V[:, 1].max() - ymin),
        "width": float(V[:, 0].max() - V[:, 0].min()),
        "depth": float(V[:, 2].max() - V[:, 2].min()),
        "ground": ymin,
        "top": float(V[:, 1].max()),
        "tris": float(len(env.T)),
        "verts": float(len(V)),
        "materials": float(len(env.mesh.materials)),
        "bonecount": float(len(env.bones)),
        "parts": float(len(env.ranges)),
        "anims": float(len(env.model.anims)),
        "pi": float(np.pi),
    }


_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
           ast.Pow: lambda a, b: a ** b}


def _dotted(node):
    """Flatten `thigh.l.tail` (parsed as nested attributes) into a string."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        raise CheckError("unsupported name in expression")
    parts.append(node.id)
    return ".".join(reversed(parts))


def _eval_node(node, scalars, funcs):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, scalars, funcs)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise CheckError("only numbers are allowed as literals")
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _number(_eval_node(node.operand, scalars, funcs))
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        a = _number(_eval_node(node.left, scalars, funcs))
        b = _number(_eval_node(node.right, scalars, funcs))
        if isinstance(node.op, ast.Div) and abs(b) < 1e-12:
            raise CheckError("division by zero")
        return _BINOPS[type(node.op)](a, b)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in funcs:
            name = getattr(node.func, "id", "?")
            raise CheckError("unknown function %r — see the checks section of "
                             "SPEC.md for the vocabulary" % name)
        args = [_eval_node(a, scalars, funcs) for a in node.args]
        try:
            return funcs[node.func.id](*args)
        except TypeError as e:
            raise CheckError("%s(): %s" % (node.func.id, e))
    if isinstance(node, ast.Name):
        if node.id in scalars:
            v = scalars[node.id]
            return v.value() if isinstance(v, _Lazy) else v
        return _Ref(node.id)
    if isinstance(node, ast.Attribute):
        return _Ref(_dotted(node))
    raise CheckError("unsupported expression")


def evaluate(model, bones, mesh, V):
    """Run the `checks` section. Returns (failures, measurements)."""
    failures, measurements = [], []
    checks = getattr(model, "checks", ())
    if not checks:
        return failures, measurements
    env = Env(model, bones, mesh, V)
    funcs = build_functions(env)
    scalars = build_scalars(env, funcs)

    def value_of(expr):
        tree = ast.parse(expr, mode="eval")
        return _number(_eval_node(tree, scalars, funcs))

    def shown(expr, value):
        """`> 0.4` for a literal bound, `> zmax(body) (0.4)` for a computed one."""
        try:
            float(expr)
        except ValueError:
            return "%s (%.4g)" % (expr, value)
        return "%.4g" % value

    for c in checks:
        if c["kind"] == "noclip":
            try:
                fails, notes = run_noclip(env, c, funcs)
            except CheckError as e:
                failures.append("line %d: noclip — %s" % (c["line_no"], e))
                continue
            failures.extend("line %d: %s" % (c["line_no"], f) for f in fails)
            measurements.extend(notes)
            continue
        try:
            value = value_of(c["expr"])
            if c["kind"] == "assert":
                if c["op"] == "in":
                    lo, hi = value_of(c["lo"]), value_of(c["hi"])
                else:
                    bound = value_of(c["val"])
                    tol = value_of(c["tol"]) if c["op"] == "==" else 0.0
        except CheckError as e:
            failures.append("line %d: %s — %s" % (c["line_no"], c["expr"], e))
            continue
        except SyntaxError:
            failures.append("line %d: %s — not a valid expression"
                            % (c["line_no"], c["expr"]))
            continue
        if c["kind"] == "measure":
            measurements.append("%s = %.4f  (%s)" % (c["label"], value, c["expr"]))
            continue
        if c["op"] == "in":
            ok = lo <= value <= hi
            want = "in %s..%s" % (shown(c["lo"], lo), shown(c["hi"], hi))
        elif c["op"] == "==":
            ok = abs(value - bound) <= tol
            want = ("== %s +- %s" % (shown(c["val"], bound), shown(c["tol"], tol))
                    if tol else "== %s" % shown(c["val"], bound))
        else:
            ok = {"<": value < bound, ">": value > bound,
                  "<=": value <= bound, ">=": value >= bound}[c["op"]]
            want = "%s %s" % (c["op"], shown(c["val"], bound))
        if not ok:
            failures.append("line %d: %s = %.4f, expected %s"
                            % (c["line_no"], c["expr"], value, want))
        else:
            measurements.append("%s = %.4f (%s, ok)" % (c["expr"], value, want))
    return failures, measurements
