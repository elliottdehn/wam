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


def _point_tri_dist(P, A, B, C):
    """Distance from each point in P to the nearest of triangles (A,B,C)."""
    P = np.asarray(P, float)[:, None, :]
    AB, AC = B - A, C - A
    AP = P - A
    d1 = (AB * AP).sum(-1)
    d2 = (AC * AP).sum(-1)
    BP = P - B
    d3 = (AB * BP).sum(-1)
    d4 = (AC * BP).sum(-1)
    CP = P - C
    d5 = (AB * CP).sum(-1)
    d6 = (AC * CP).sum(-1)
    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    denom = va + vb + vc
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(np.abs(denom) > 1e-20, vb / denom, 0.0)
        w = np.where(np.abs(denom) > 1e-20, vc / denom, 0.0)
        # region tests, resolved in the order of Ericson's closest-point
        s_ab = np.clip(np.where(np.abs(d1 - d3) > 1e-20,
                                d1 / np.where(np.abs(d1 - d3) > 1e-20, d1 - d3, 1.0),
                                0.0), 0, 1)
        s_ac = np.clip(np.where(np.abs(d2 - d6) > 1e-20,
                                d2 / np.where(np.abs(d2 - d6) > 1e-20, d2 - d6, 1.0),
                                0.0), 0, 1)
        bc_d = (d4 - d3) - (d6 - d5)
        s_bc = np.clip(np.where(np.abs(bc_d) > 1e-20,
                                (d4 - d3) / np.where(np.abs(bc_d) > 1e-20, bc_d, 1.0),
                                0.0), 0, 1)
    Q = A + AB * v[..., None] + AC * w[..., None]
    Q = np.where(((vc <= 0) & (d1 >= 0) & (d3 <= 0))[..., None],
                 A + AB * s_ab[..., None], Q)
    Q = np.where(((vb <= 0) & (d2 >= 0) & (d6 <= 0))[..., None],
                 A + AC * s_ac[..., None], Q)
    Q = np.where(((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0))[..., None],
                 B + (C - B) * s_bc[..., None], Q)
    Q = np.where(((d1 <= 0) & (d2 <= 0))[..., None], A, Q)
    Q = np.where(((d3 >= 0) & (d4 <= d3))[..., None], B, Q)
    Q = np.where(((d6 >= 0) & (d5 <= d6))[..., None], C, Q)
    return np.linalg.norm(P - Q, axis=-1).min(axis=1)


def surface_distance(Va, Ta, Vb, Tb):
    """Closest approach between two *surfaces*, not their vertex clouds.

    Vertex-to-vertex distance overestimates whenever a face is larger than
    the gap being measured: a low-poly shield board resting flat on a forearm
    has no vertex anywhere near the arm, so it reads 0.084 apart while the
    surfaces are touching. Anything asking "do these meet?" has to sample the
    faces, not the corners.
    """
    Va, Vb = np.asarray(Va, float), np.asarray(Vb, float)
    if not len(Va) or not len(Vb):
        return float("inf")
    best = float("inf")
    if len(Tb):
        A, B, C = Vb[Tb[:, 0]], Vb[Tb[:, 1]], Vb[Tb[:, 2]]
        best = min(best, float(_point_tri_dist(Va, A, B, C).min()))
    if len(Ta):
        A, B, C = Va[Ta[:, 0]], Va[Ta[:, 1]], Va[Ta[:, 2]]
        best = min(best, float(_point_tri_dist(Vb, A, B, C).min()))
    return best


def rest_shift(PV, PT, TV, TT, u, layer, iters=34):
    """How far to slide P along u so it rests on T with `layer` clearance.

    Solved on the real separation rather than on extremes along the axis:
    picking the furthest vertex of each along u is only right for two
    parallel slabs, and a shield strapped to a forearm is a flat plate beside
    a tilted cone — that solve clears the shoulder and leaves the shield
    hanging 0.07 out from the arm it is strapped to.
    """
    PV, TV = np.asarray(PV, float), np.asarray(TV, float)
    u = np.asarray(u, float)
    u = u / max(np.linalg.norm(u), 1e-12)
    span = float(max((PV.max(0) - PV.min(0)).max(),
                     (TV.max(0) - TV.min(0)).max(),
                     np.linalg.norm(PV.mean(0) - TV.mean(0)))) * 2.0 + 1.0

    def clear(t):
        return surface_distance(PV + u * t, PT, TV, TT) > layer

    lo, hi = -span, span          # lo: overlapping, hi: comfortably clear
    if not clear(hi):
        return 0.0
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if clear(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _min_pair_distance(A, B):
    """Closest approach between two point sets, in chunks to bound memory."""
    A, B = _subsample(A), _subsample(B)
    best = float("inf")
    for i in range(0, len(A), 128):
        chunk = A[i:i + 128]
        d = np.linalg.norm(chunk[:, None, :] - B[None, :, :], axis=2)
        best = min(best, float(d.min()))
    return best


def boundary_loops(V, T):
    """Ordered vertex-index loops along a surface's open boundary."""
    V, T = np.asarray(V, float), np.asarray(T, int)
    if not len(T):
        return []
    edges = {}
    for a, b, c in T:
        for e in ((a, b), (b, c), (c, a)):
            k = tuple(sorted(e))
            edges[k] = edges.get(k, 0) + 1
    border = [e for e, n in edges.items() if n == 1]
    if not border:
        return []
    adj = {}
    for a, b in border:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, loops = set(), []
    for start in adj:
        if start in seen:
            continue
        loop, cur, prev = [start], start, None
        seen.add(start)
        while True:
            nxt = [n for n in adj[cur] if n != prev and n not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            seen.add(cur)
            loop.append(cur)
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def bone_fit_leak(bones, body_pts, body_skin, garment_pts,
                  bands=12, sectors=16, reach=3.0):
    """Does the body outgrow the garment around it, measured against bones.

    Every earlier attempt asked a containment question — is this point inside
    that surface — and containment is the wrong tool, because a garment is not
    a closed volume. Cloth has mouths the body is *meant* to leave through, so
    ray parity reports the legs below a hem as a leak of near-constant size
    however the garment is cut, and gating that away by "how much is enclosed"
    inverts the test: the worse a garment splits, the less of the body it
    contains, so the severe cases go quiet.

    Bones make the question well posed without any of that. A worn thing wraps
    the limb or trunk it is worn on, so at each position along a bone both the
    body and the cloth have a radius about it, and fitting means the cloth's
    is the larger. Openings need no special case: at a hem the leg is well
    inside the cloth's radius and scores nothing, while a thigh broader than
    the waistband scores exactly its overhang. It makes no assumption about
    the garment being open, closed, a tube, a sheet or a cap.
    """
    worst = 0.0
    body_pts = np.asarray(body_pts, float)
    garment_pts = np.asarray(garment_pts, float)
    if not len(body_pts) or not len(garment_pts):
        return 0.0
    by_bone = {}
    for i, sk in enumerate(body_skin):
        if not sk:
            continue
        name = max(sk, key=lambda nw: nw[1])[0]
        by_bone.setdefault(name, []).append(i)

    for name, ids in by_bone.items():
        b = bones.get(name)
        if b is None or b.len <= 1e-9:
            continue
        o = np.asarray(b.head, float)
        u = np.asarray(b.dir, float)
        u = u / max(np.linalg.norm(u), 1e-12)
        e1, e2 = _frame_perp(u)

        def polar(X):
            d = X - o
            t = d @ u
            r = d - np.outer(t, u)
            return t, np.hypot(r @ e1, r @ e2), np.arctan2(r @ e2, r @ e1)

        gt, gr, ga = polar(garment_pts)
        near = np.abs(gt - b.len * 0.5) <= b.len * reach
        if not near.any():
            continue
        gt, gr, ga = gt[near], gr[near], ga[near]
        lo, hi = gt.min(), gt.max()
        span = max(float(hi - lo), 1e-9)

        pt, pr, pa = polar(body_pts[ids])
        keep = (pt >= lo) & (pt <= hi)
        if not keep.any():
            continue

        gb = np.clip(((gt - lo) / span * bands).astype(int), 0, bands - 1)
        gs = np.clip(((ga + np.pi) / (2 * np.pi) * sectors).astype(int), 0, sectors - 1)
        grid = np.full((bands, sectors), -1.0)
        np.maximum.at(grid, (gb, gs), gr)
        for row in grid:                       # a sector with no cloth in this
            seen = row[row >= 0]               # band inherits the band's reach
            if len(seen):
                row[row < 0] = seen.mean()

        pb = np.clip(((pt[keep] - lo) / span * bands).astype(int), 0, bands - 1)
        ps = np.clip(((pa[keep] + np.pi) / (2 * np.pi) * sectors).astype(int), 0, sectors - 1)
        cloth = grid[pb, ps]
        live = cloth >= 0
        if live.any():
            worst = max(worst, float(np.max(pr[keep][live] - cloth[live])))
    return max(worst, 0.0)


def _frame_perp(u):
    """Two unit vectors completing an orthonormal frame with u."""
    a = np.array([1.0, 0.0, 0.0])
    if abs(float(np.dot(a, u))) > 0.9:
        a = np.array([0.0, 1.0, 0.0])
    e1 = a - u * float(np.dot(a, u))
    e1 = e1 / np.linalg.norm(e1)
    return e1, np.cross(u, e1)


def boundary_verts(V, T):
    """Vertex positions on a surface's open boundary loops."""
    V, T = np.asarray(V, float), np.asarray(T, int)
    if not len(T):
        return V[:0]
    edges = {}
    for a, b, c in T:
        for e in ((a, b), (b, c), (c, a)):
            k = tuple(sorted(e))
            edges[k] = edges.get(k, 0) + 1
    idx = sorted({i for e, n in edges.items() if n == 1 for i in e})
    return V[idx] if idx else V[:0]


def seal(V, T):
    """Close a surface's open boundaries so inside/outside means something.

    Cloth is authored open — `cap start=none end=none` is what makes a skirt a
    skirt rather than a bag. But every containment test here is ray parity,
    and parity through an open tube is undefined: a ray can leave through the
    hem without ever crossing a face. The failure is not a wrong number, it is
    an unstable one — the reported leak of a skirt that provably clears the
    body *grew* from 0.012 to 0.050 as the skirt was widened.

    Each boundary loop is fanned from its own centroid, which is exactly the
    lid the author left off and nothing more.
    """
    V, T = np.asarray(V, float), np.asarray(T, int)
    if not len(T):
        return V, T
    edges = {}
    for a, b, c in T:
        for e in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted(e))] = edges.get(tuple(sorted(e)), 0) + 1
    border = [e for e, n in edges.items() if n == 1]
    if not border:
        return V, T
    adj = {}
    for a, b in border:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, loops = set(), []
    for start in adj:
        if start in seen:
            continue
        loop, cur, prev = [start], start, None
        seen.add(start)
        while True:
            nxt = [n for n in adj[cur] if n != prev and n not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            seen.add(cur)
            loop.append(cur)
        if len(loop) >= 3:
            loops.append(loop)
    if not loops:
        return V, T
    V2, T2 = list(V), list(T)
    for loop in loops:
        ci = len(V2)
        V2.append(V[loop].mean(axis=0))
        for i in range(len(loop)):
            T2.append([ci, loop[i], loop[(i + 1) % len(loop)]])
    return np.asarray(V2, float), np.asarray(T2, int)


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


def _crossing_depth(P0, P1, A, B, C, chunk=64, collect=False):
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
    points = []
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
        if collect:
            mi, ti = np.nonzero(hit)
            points.append(p0[mi] + d[mi] * t[hit][:, None])
        # Signed height of each endpoint over each pierced triangle's plane.
        # Faces are wound outward, so the endpoint with negative height is
        # the buried one and its depth is how far it sits behind the surface.
        nlen = np.sqrt(np.einsum("tj,tj->t", nrm, nrm))
        nlen = np.where(nlen < 1e-18, 1.0, nlen)
        h0 = (np.einsum("mj,tj->mt", p0, nrm) - plane_d[None, :]) / nlen
        h1 = (np.einsum("mj,tj->mt", p1, nrm) - plane_d[None, :]) / nlen
        depth = np.maximum(-np.minimum(h0, h1), 0.0)
        best = max(best, float(depth[hit].max()))
    if collect:
        return best, (np.concatenate(points) if points else np.zeros((0, 3)))
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
        markers = getattr(self.model, "markers", None) or {}
        if name in markers:
            return np.asarray(markers[name], dtype=float)
        if self.is_bone(name):
            b, want = self.bone(ref)
            return {"head": b.head, "tail": b.tail,
                    "mid": (b.head + b.tail) / 2.0}[want]
        for key in (name, name + ".l"):
            if key in self.ranges:
                chunk = self.part_verts(ref)
                return (chunk.min(axis=0) + chunk.max(axis=0)) / 2.0
        raise CheckError("%r is not a marker, a bone or a part" % name)

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

    def sealed_tri_verts(self, ref, V=None):
        """`tri_verts`, but with any open boundary closed off first.

        Containment questions must be asked of a closed surface. Cloth is not
        one, so the lid the author deliberately left off is added back here
        and nowhere else — the rendered geometry is untouched.
        """
        V = self.V if V is None else V
        t = self.part_tris(ref)
        if not len(t):
            return V[:0], V[:0], V[:0]
        idx = np.unique(t)
        remap = {int(o): i for i, o in enumerate(idx)}
        local = np.vectorize(remap.__getitem__)(t)
        SV, ST = seal(V[idx], local)
        return SV[ST[:, 0]], SV[ST[:, 1]], SV[ST[:, 2]]

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

    def covers(outer, inner):
        """Fraction of `inner`'s surface that lies inside `outer`.

        `clip` reads 0 both when two parts are cleanly apart and when one has
        swallowed the other whole, which is exactly ambiguous for armour: a
        cuirass that fits and a cuirass buried inside a fattened torso both
        measure 0. This asks the question fit actually poses — how much of
        the body is *contained* — and separates the two.
        """
        pts = env.part_verts_in(inner)
        if len(pts) > _CLIP_SAMPLES:
            pts = pts[:: (len(pts) + _CLIP_SAMPLES - 1) // _CLIP_SAMPLES]
        A, B, C = env.sealed_tri_verts(outer)
        if not len(A) or not len(pts):
            return 0.0
        return float(points_inside(pts, A, B, C).mean())

    def leak(outer, inner):
        """How far `inner` pokes out through `outer` where `outer` covers it.

        A garment is supposed to leave the body sticking out of its openings —
        legs below a hem, a head above a collar — so `covers` cannot tell a
        correct skirt from one a thigh is bursting through. This asks the
        narrower question: within the span the garment actually occupies, how
        far outside its surface does the body get? Legs below the hem are out
        of that span and ignored; a thigh through the cloth is not.
        """
        # deliberately not subsampled to _CLIP_SAMPLES: this is a worst-case
        # measure, and dropping half the vertices drops the one poking out
        pts = _subsample(env.part_verts_in(inner))
        ov = env.part_verts_in(outer)
        A, B, C = env.sealed_tri_verts(outer)
        if not len(A) or not len(pts):
            return 0.0
        lo, hi = ov.min(axis=0), ov.max(axis=0)
        within = pts[np.all((pts >= lo) & (pts <= hi), axis=1)]
        if not len(within):
            return 0.0
        outside = within[~points_inside(within, A, B, C)]
        if not len(outside):
            return 0.0
        return max(_dist_to_surface(p, A, B, C) for p in outside)

    def concavity(part):
        """How far a part's cross-section bites into itself, 0..1.

        The one property a `profile` adds that no superellipse has. Profiles
        are normalised to span `w` and `d`, so every bounding-box measure is
        identical by construction and nothing else can tell a crescent from a
        circle — which left the feature most able to change a silhouette as
        the one an author could not stop from regressing.
        """
        from .mesh import section_concavity
        unit = (getattr(env.mesh, "sections", {}) or {}).get(_name_of(part))
        return 0.0 if unit is None else section_concavity(unit)

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

    def _planted(part, anim):
        """Centroids over one stance: the longest unbroken run of contact.

        Every low frame is not a stance frame — a foot descending at the end
        of swing is near the ground and still travelling forward, which is
        correct footfall, not sliding. Taking the longest contiguous run
        (wrapping, since a cycle loops) isolates the interval the foot is
        actually planted.
        """
        v0, v1 = env.part_range(part)
        frames, _ = env.posed(anim)
        lows = [float(f[v0:v1, 1].min()) for f in frames]
        floor = min(lows)
        tol = max(0.02, (max(lows) - floor) * 0.15)
        n = len(frames)
        down = [lo <= floor + tol for lo in lows]
        if all(down):
            run = list(range(n))
        else:
            best, cur = [], []
            for k in range(2 * n):
                i = k % n
                if down[i] and (len(cur) < n):
                    cur.append(i)
                    if len(cur) > len(best):
                        best = list(cur)
                else:
                    cur = []
            run = best
        return [(i, frames[i][v0:v1].mean(axis=0)) for i in run]

    def slide(part, anim):
        """How far a planted part travels *forward* — the moonwalk measure.

        There is no root translation channel, so a looping gait is authored
        in place: the planted foot has to travel a full stride backwards
        through stance, and that is correct, not a bug. Total travel is
        therefore unbounded below by anything an author can fix — what
        distinguishes a good gait from a moonwalk is *direction*. This sums
        the forward (+Z) steps a part makes while it is on the ground, so a
        correctly timed gait reads 0 and a leg driven backwards through its
        own stance reads the distance it dragged.
        """
        planted = _planted(part, anim)
        total = 0.0
        for (_, ca), (_, cb) in zip(planted, planted[1:]):
            total += max(0.0, float(cb[2] - ca[2]))
        return total

    def drift(part, anim):
        """Total horizontal travel of a part while planted, unsigned.

        For an in-place cycle this is roughly the stride length and is
        supposed to be large; it is worth checking only once a model drives
        its root with `shift=` pose keys.
        """
        planted = _planted(part, anim)
        if len(planted) < 2:
            return 0.0
        c = np.array([[p[0], p[2]] for _, p in planted])
        return float(np.linalg.norm(c.max(axis=0) - c.min(axis=0)))

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
        "closer": lambda a, b, c: float(
            np.linalg.norm(env.point(a) - env.point(c))
            - np.linalg.norm(env.point(a) - env.point(b))),
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
        "length": lambda r: axis_size(r, 2),
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
        "concavity": concavity,
        "clip": clip,
        "covers": covers,
        "leak": leak,
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
        "drift": drift,
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
        # Sharing a joint is NOT on its own a licence to interpenetrate. A
        # thigh entering a hip and a cape hanging through a chest both share
        # a bone; what separates them is *where* they overlap — the thigh
        # only at its root, the cape along its whole length. So the joint
        # exemption is applied through the rooted test below rather than as
        # a blanket subtraction, which used to hide every cloth-through-body
        # bug in the language.
        # Sharing a joint forgives a thigh entering a hip, and it used to
        # forgive a cape hanging straight through a chest. What separates
        # them is shape, not kinship: cloth is a *sheet* — one small extent
        # and two large ones — while body masses are blobs and limbs are
        # rods. A sheet passing through anything is a clipping bug, so pairs
        # involving one keep no joint exemption.
        if not spec.get("strict"):
            pairs -= {p for p in _soft_pairs(env)
                      if not any(_is_sheet(env, n) for n in p)}
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
        if worst > tol and not spec.get("strict") and not explicit:
            if (_rooted_overlap(env, _Ref(a), _Ref(b))
                    or _rooted_overlap(env, _Ref(b), _Ref(a))):
                continue
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


def _is_sheet(env, name):
    """Is this part cloth-like — a broad thin sheet rather than a rod or mass?

    Judged from the *authored* cross-sections, not the bounding box: a curved
    tentacle has a fat bbox and is still a rod, so geometry alone misreads it
    as a sheet and strips exemptions from every digit and feeler in the model.
    A ring that is many times wider than deep is cloth; a `web` without
    thickness is cloth by definition.
    """
    base = name
    if base.endswith((".l", ".r")):
        base = base[:-2]
    for part in getattr(env.model, "parts", ()):
        if part.get("name") != base:
            continue
        if part.get("kind") == "web":
            return float(part.get("thickness", 0.0)) <= 0.02
        ratios = []
        for r in part.get("rings", ()):
            w = float(r.get("w", 0.0))
            d = float(r.get("d", w))
            lo, hi = min(w, d), max(w, d)
            if hi > 1e-9:
                ratios.append(hi / max(lo, 1e-9))
        if ratios:
            ratios.sort()
            return ratios[len(ratios) // 2] > 3.5
        if part.get("kind") == "attach":
            size = float(part.get("size", 0.05))
            e = sorted(float(part.get(k, size)) for k in ("w", "d", "h"))
            return e[2] > 1e-9 and e[0] / e[2] < 0.3
        return False
    return False


def _rooted_overlap(env, a, b, V=None, span=0.35):
    """Is a's intersection with b confined to where a begins?

    A limb rooted in the body mass it grows out of overlaps only near its
    start; a spar passing through a torso crosses it mid-span. That is the
    same "the construct means this" logic already applied to `on=`, and it
    is what separates most structural overlap from a real defect.
    """
    pts = env.part_verts_in(a, V)
    if len(pts) < 2:
        return False
    axis = pts[-1] - pts[0]
    length = float(np.linalg.norm(axis))
    if length < 1e-9:
        return False
    axis = axis / length
    ae0, ae1 = env.part_edges(a, V)
    _, hits = _crossing_depth(ae0, ae1, *env.tri_verts(b, V), collect=True)
    if not len(hits):
        return False
    along = (hits - pts[0]) @ axis / length
    return bool(along.max() < span)


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


def namespace(model, bones, mesh, V):
    """(env, scalars, funcs, noclip) for one compiled model.

    Exposed so a `.wamset` `every` block can run ordinary model-level checks
    inside each member's own namespace, rather than re-implementing one.
    """
    env = Env(model, bones, mesh, V)
    funcs = build_functions(env)
    scalars = build_scalars(env, funcs)
    return env, scalars, funcs, (lambda spec: run_noclip(env, spec, funcs))


def evaluate(model, bones, mesh, V):
    """Run a model's `checks` section. Returns (failures, measurements)."""
    checks = getattr(model, "checks", ())
    if not checks:
        return [], []
    _, scalars, funcs, noclip = namespace(model, bones, mesh, V)
    return run_checks(checks, scalars, funcs, noclip=noclip)


def run_checks(checks, scalars, funcs, noclip=None):
    """Evaluate parsed checks against a namespace. (failures, measurements).

    The same loop serves a single model and a cross-model set file, so an
    assertion means exactly the same thing at both levels.
    """
    failures, measurements = [], []

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
            if noclip is None:
                failures.append("line %d: noclip only applies within a model"
                                % c["line_no"])
                continue
            try:
                fails, notes = noclip(c)
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
