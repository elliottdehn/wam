
"""Semantic lint: catches the mistakes that make models look broken."""
import numpy as np

from . import animation as wanim
from . import checks as wchecks


def _crossings(points, A, B, C):
    """How many of triangles ABC each point's +X ray crosses (Möller–Trumbore)."""
    e1, e2 = B - A, C - A
    d = np.array([1.0, 0.0, 0.0])
    h = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, h)
    live = np.abs(det) > 1e-12
    if not live.any():
        return np.zeros(len(points), dtype=int)
    e1, e2, A2, h, det = e1[live], e2[live], A[live], h[live], det[live]
    inv = 1.0 / det
    out = np.zeros(len(points), dtype=int)
    for pi, p in enumerate(points):
        s = p - A2
        u = inv * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, e1)
        v = inv * q[:, 0]                          # d · q with d = +X
        t = inv * np.einsum("ij,ij->i", e2, q)
        hit = (u >= 0) & (u <= 1) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
        out[pi] = int(hit.sum())
    return out


def _buried_parts(V, T, ranges, samples=24):
    """Parts whose geometry lies entirely inside another part.

    `on=` exists precisely so attachments sit on a surface, and an emblem or
    a spike that sank inside its host compiles perfectly cleanly — nothing
    else in the lint can see it. Containment is tested against one part at a
    time, so something hidden by the *union* of several parts still slips
    through.
    """
    found = []
    if not len(T):
        return found
    boxes = {}
    for name, (v0, v1) in ranges.items():
        if v1 - v0 >= 4:
            boxes[name] = (V[v0:v1].min(axis=0), V[v0:v1].max(axis=0))
    tri_owner = {}
    for name, (v0, v1) in ranges.items():
        sel = (T[:, 0] >= v0) & (T[:, 0] < v1)
        if sel.any():
            tri_owner[name] = T[sel]
    for a, (alo, ahi) in boxes.items():
        for b, (blo, bhi) in boxes.items():
            if a == b or b not in tri_owner:
                continue
            if not (np.all(alo >= blo - 1e-9) and np.all(ahi <= bhi + 1e-9)):
                continue                      # bbox not contained: cheap out
            v0, v1 = ranges[a]
            pts = V[v0:v1]
            if len(pts) > samples:
                pts = pts[:: len(pts) // samples]
            tb = tri_owner[b]
            n = _crossings(pts, V[tb[:, 0]], V[tb[:, 1]], V[tb[:, 2]])
            if (n % 2 == 1).mean() > 0.95:    # odd crossings = inside
                found.append((a, b))
                break
    return found


def _islands(V, T, ranges, touch):
    """Group parts into clumps of geometry that actually touch each other.

    A part attached to nothing is one of the few errors that survives every
    other check: it is skinned to a real bone, it moves with the animation,
    it never intersects anything, and it renders perfectly — floating an inch
    off the shoulder it was meant to sit on. Proximity to a *bone* cannot see
    it either, because the bone runs right through where the part is hovering.

    Surfaces are compared face-to-face rather than vertex-to-vertex: a
    low-poly plate resting flat on an arm has no vertex anywhere near it and
    would otherwise read as detached.
    """
    from . import checks as wchecks
    keys = [k for k in ranges if ranges[k][1] > ranges[k][0]]
    if len(keys) < 2:
        return []
    tris = np.asarray(T, dtype=int)

    def surf(k):
        v0, v1 = ranges[k]
        sel = tris[((tris >= v0) & (tris < v1)).all(axis=1)] - v0 \
            if len(tris) else np.zeros((0, 3), dtype=int)
        return V[v0:v1], sel

    box = {k: (V[slice(*ranges[k])].min(axis=0), V[slice(*ranges[k])].max(axis=0))
           for k in keys}
    parent = {k: k for k in keys}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, a in enumerate(keys):
        alo, ahi = box[a]
        for b in keys[i + 1:]:
            if find(a) == find(b):
                continue
            blo, bhi = box[b]
            # cheap reject first: the full surface test is far too costly to
            # run on every pair in a 40-part model
            if np.any(blo - ahi > touch) or np.any(alo - bhi > touch):
                continue
            av, at = surf(a)
            bv, bt = surf(b)
            if wchecks.surface_distance(av, at, bv, bt) <= touch:
                parent[find(a)] = find(b)

    groups = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    return list(groups.values())


def lint(model, bones, mesh):
    warnings = list(getattr(model, "warnings", []))
    warnings += list(getattr(mesh, "warnings", []))
    infos = []
    V, T, M = mesh.arrays()
    if len(V) == 0:
        return ["model produced no geometry"], infos

    height = V[:, 1].max() - min(V[:, 1].min(), 0)

    # 1. bones with no geometry nearby
    covered = set()
    for sk in mesh.skin:
        for bn, w in sk:
            if w > 0.05:
                covered.add(bn)
    for name, b in bones.items():
        if b.len > 0.01 and name not in covered:
            mid = (b.head + b.tail) / 2
            d = np.linalg.norm(V - mid, axis=1).min()
            if d > 0.06:
                warnings.append("bone %r has no geometry attached or near it "
                                "(nearest vertex %.2f away)" % (name, d))

    # 2. feet below / above ground
    ymin = V[:, 1].min()
    if ymin < -0.02:
        warnings.append("geometry dips %.3f below the ground plane (y=0) — "
                        "legs too long or root too low" % -ymin)
    elif ymin > 0.02:
        warnings.append("lowest geometry floats %.3f above ground (y=0) — "
                        "legs too short or root too high" % ymin)

    # 3. symmetry, per part rather than per silhouette.
    #
    # Comparing the whole model's left and right extents is unusable the
    # moment a character carries a sword in one hand and a shield on the
    # other: nothing but a bare figure can pass, and tuning a prop's spin
    # against a crossguard's length to silence it is worse than the warning.
    # Only a part that straddles the centerline is claiming to be symmetric —
    # a one-sided part and a rigid `group` prop are the author's business.
    in_group = {p["name"] for p in model.parts if p.get("group")}
    for pname, (v0, v1) in mesh.part_ranges.items():
        if v1 - v0 < 4 or pname.rsplit(".", 1)[0] in in_group:
            continue
        if pname.endswith((".l", ".r")):
            continue                      # generated by reflection; exact
        chunk = V[v0:v1]
        xlo, xhi = chunk[:, 0].min(), chunk[:, 0].max()
        if xlo > -0.01 or xhi < 0.01:
            continue                      # sits to one side: intentional
        off = abs(xhi + xlo)
        if off > 0.02:
            warnings.append(
                "part %r straddles the centerline but is lopsided (extends "
                "%.3f left, %.3f right) — a central part should be symmetric; "
                "check its ring `side=` offsets, or move it fully to one side"
                % (pname, xhi, -xlo))

    # 4. proportions report
    total_h = V[:, 1].max() - ymin
    span = V[:, 0].max() - V[:, 0].min()
    depth = V[:, 2].max() - V[:, 2].min()
    infos.append("bbox: height %.2f, width %.2f, depth %.2f (of model height)"
                 % (total_h, span, depth))
    head_parts = [k for k in mesh.part_ranges if "head" in k]
    if head_parts:
        v0, v1 = mesh.part_ranges[head_parts[0]]
        hh = V[v0:v1, 1].max() - V[v0:v1, 1].min()
        if hh > 1e-6:
            infos.append("figure is %.1f head-heights tall" % (total_h / hh))

    # 5. part origin closer to another bone than its host: wrong-binding smell
    def seg_dist(p, bone):
        a0, d, L = bone.head, bone.dir, bone.len
        t = float(np.clip(np.dot(p - a0, d), 0, max(L, 1e-9)))
        return float(np.linalg.norm(p - (a0 + d * t)))

    def part_bone_set(part_key):
        """Every bone a part is skinned to, over either mirrored half."""
        names = set()
        for key in (part_key, part_key + ".l", part_key + ".r"):
            if key not in mesh.part_ranges:
                continue
            v0, v1 = mesh.part_ranges[key]
            for i in range(v0, v1):
                names.update(bn for bn, w in mesh.skin[i] if w > 0.05)
        return names

    for part in model.parts:
        if "bone" not in part:
            continue
        host_ref = part["bone"]
        if part.get("on"):
            # `on=` snaps the origin onto another part's surface, so its raw
            # distance to any bone says nothing — a tentacle passing nearby
            # would "win" an eye socket. The question worth asking is whether
            # the attachment rides a bone its base is actually built on. A
            # base spanning a chain has many, and a spike near its far end
            # rightly hosts on the last of them.
            base_bones = part_bone_set(part["on"])
            hb = bones.get(host_ref) or bones.get(host_ref + ".l")
            if base_bones and hb is not None:
                # Membership, not proximity: the host has to be one of the
                # bones that actually drives the surface. A neighbouring bone
                # is not good enough — that is precisely the attachment that
                # slides off when the two bones diverge.
                ok = any(bones.get(bn) is hb for bn in base_bones)
                if not ok:
                    warnings.append(
                        "part %r sits on %r but is hosted on %r, which does "
                        "not drive any of %r — the attachment will slide off "
                        "its base as soon as either moves"
                        % (part["name"], part["on"], host_ref, part["on"]))
            continue
        hb = bones.get(host_ref) or bones.get(host_ref + ".l")
        if hb is None:
            continue
        origin = hb.point_at(part.get("at", 1.0)) + np.array(part.get("offset", (0, 0, 0)))
        d_host = seg_dist(origin, hb)
        best_name, d_best = None, 1e9
        for name, ob in bones.items():
            if ob.len < 1e-6:
                continue
            dd = seg_dist(origin, ob)
            if dd < d_best:
                best_name, d_best = name, dd
        if (best_name and bones[best_name] is not hb
                and d_host > 0.08 and d_host > 1.6 * d_best):
            warnings.append(
                "part %r is hosted on %r but its origin sits nearer to %r "
                "(%.3f vs %.3f) — it will be rigidly skinned to %r; host it on "
                "the bone it visually attaches to"
                % (part["name"], host_ref, best_name, d_best, d_host, host_ref))

    # 6. degenerate triangles
    if len(T):
        a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        areas = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
        ndeg = int((areas < 1e-10).sum())
        if ndeg:
            warnings.append("%d degenerate (zero-area) triangles — every "
                            "generator emits fans and strips that collapse "
                            "cleanly, so this means overlapping ring centers "
                            "or a zero-length path segment" % ndeg)

        # Shared manifold edges must be traversed in opposite directions by
        # their two incident triangles. Same-direction pairs are a precise
        # indicator of inconsistent winding and become holes under Godot's
        # default backface culling.
        edges = {}
        for ti, tri in enumerate(T):
            for u, v in ((int(tri[0]), int(tri[1])),
                         (int(tri[1]), int(tri[2])),
                         (int(tri[2]), int(tri[0]))):
                if u == v:
                    continue
                key = (u, v) if u < v else (v, u)
                direction = 1 if (u, v) == key else -1
                edges.setdefault(key, []).append((ti, direction))
        inconsistent = sum(1 for uses in edges.values()
                           if len(uses) == 2 and uses[0][1] == uses[1][1])
        nonmanifold = sum(1 for uses in edges.values() if len(uses) > 2)
        if inconsistent:
            warnings.append("%d shared edges have inconsistent triangle winding"
                            % inconsistent)
        if nonmanifold:
            warnings.append("%d non-manifold edges are used by more than two triangles"
                            % nonmanifold)

    # 5b. sweeps: say which way the bend actually went
    def _dir_words(v):
        v = np.asarray(v, dtype=float)
        n = np.linalg.norm(v)
        if n < 1e-9:
            return "nowhere"
        v = v / n
        names = []
        for comp, (pos, neg) in zip(v, (("left", "right"), ("up", "down"),
                                        ("fwd", "back"))):
            if abs(comp) > 0.35:
                names.append(pos if comp > 0 else neg)
        return "-".join(names) or "fwd"

    # Grouped: a model with twenty claws should not spend twenty lines
    # saying the same thing about all of them.
    groups = {}
    for key, (d0, d1, p0, p1) in sorted(getattr(mesh, "sweep_paths", {}).items()):
        turn = np.degrees(np.arccos(float(np.clip(np.dot(
            d0 / np.linalg.norm(d0), d1 / np.linalg.norm(d1)), -1, 1))))
        if turn < 15:
            continue
        sig = (_dir_words(d0), _dir_words(d1), round(turn / 10.0),
               round(float(p1[1] - p0[1]), 2))
        groups.setdefault(sig, []).append(key)
    for (a, b, turn10, rise), keys in sorted(groups.items(),
                                             key=lambda kv: -len(kv[1])):
        shown = ", ".join(keys[:3]) + (", ..." if len(keys) > 3 else "")
        count = "" if len(keys) == 1 else " (%d sweeps)" % len(keys)
        infos.append("sweep %s%s leaves %s and ends up pointing %s (~%d° of "
                     "bend, tip %+.2f in y)"
                     % (shown, count, a, b, turn10 * 10, rise))

    # 5b2. `on=` pressing silently re-aims a part onto the surface normal.
    # A small turn is the point of the feature; a large one means the authored
    # `dir=` was overridden wholesale, which the author should know about.
    turned = sorted(((deg, key) for key, deg in getattr(mesh, "pressed", {}).items()
                     if deg > 20.0), reverse=True)
    if turned:
        shown = ", ".join("%s %.0f\u00b0" % (k, d) for d, k in turned[:4])
        infos.append("on= pressed %d part(s) more than 20\u00b0 off their "
                     "authored dir (%s%s) — that is the surface deciding the "
                     "angle; add press=off to keep the authored aim"
                     % (len(turned), shown,
                        ", ..." if len(turned) > 4 else ""))

    # 5c. a multi-bone loft that never blends binds rigidly per segment
    for part in model.parts:
        if part.get("kind") != "loft" or "chain" not in part:
            continue
        for suffix in ("", ".l"):
            key = part["name"] + suffix
            if key not in mesh.part_ranges:
                continue
            v0, v1 = mesh.part_ranges[key]
            names = set()
            for i in range(v0, v1):
                names.update(n for n, w in mesh.skin[i] if w > 0.01)
            most = max((len(mesh.skin[i]) for i in range(v0, v1)), default=0)
            if len(names) > 1 and most < 2:
                warnings.append(
                    "loft %r spans %d bones but no vertex blends between them "
                    "— it will crease rigidly at each joint; remove the "
                    "`skin=` overrides that pin its rings"
                    % (key, len(names)))
            break

    # 5d. a mirrored limb that crosses the centreline is a sign error.
    # Nothing else notices: both copies are built correctly, they just point
    # inward and pass through each other in front of the body.
    for pname, (v0, v1) in mesh.part_ranges.items():
        if not pname.endswith(".l") or v1 - v0 < 4:
            continue
        chunk = V[v0:v1]
        over = float(-chunk[:, 0].min())
        reach = float(chunk[:, 0].max())
        if over > 0.02 and over > 0.25 * max(reach, 1e-6):
            warnings.append(
                "mirrored part %r reaches %.3f across the centreline into its "
                "twin's side — a mirrored limb should stay on its own half, so "
                "a `yaw`/`side` sign is probably inverted and the pair now "
                "crosses in front of the body" % (pname, over))

    # 5e. self-intersection, always on. `noclip` in `checks` is for pairs you
    # want held to a tighter bar; a model should not have to ask whether its
    # own parts pass through each other.
    author_sweeps = any(c.get("kind") == "noclip"
                        for c in getattr(model, "checks", ()))
    try:
        if author_sweeps:
            raise StopIteration            # the author set their own bar
        env, mscalars, mfuncs, mnoclip = wchecks.namespace(model, bones, mesh, V)
        fails, _ = mnoclip(dict(kind="noclip", subject=None, against=None,
                                strict=False, anim=None, depth=0.02,
                                exempt=[], line_no=0))
        for f in fails[:6]:
            warnings.append("parts intersect — %s" % f)
    except Exception:
        pass

    # 5f. animation channels are deltas about each bone's own head, so they
    # compound down a chain: six "modest" thirty-degree bends land the tip a
    # hundred and eighty degrees from rest and fold the limb through itself.
    # Nothing in the source hints at it, so say what actually happened.
    for anim in getattr(model, "anims", ()):
        worst = (0.0, None, 0.0)
        moved = 0.0
        for i in range(8):
            ph = i / 8.0
            try:
                rots = wanim.anim_rotations_at(model, bones, anim, ph)
                G = wanim.global_transforms(bones, list(bones.values()), rots)
            except Exception:
                break
            for name, b in bones.items():
                if b.len <= 1e-6:
                    continue
                d0 = b.dir / max(np.linalg.norm(b.dir), 1e-12)
                d1 = G[name][:3, :3] @ d0
                n = np.linalg.norm(d1)
                if n < 1e-12:
                    continue
                turn = np.degrees(np.arccos(np.clip(float(d0 @ (d1 / n)), -1, 1)))
                moved = max(moved, turn)
                authored = abs(sum(
                    max(abs(v) for _, v in ch["keys"]) for ch in anim["channels"]
                    if ch["bone"] == name)) or 0.0
                if turn > worst[0]:
                    worst = (turn, name, authored)
        if worst[0] > 120.0:
            warnings.append(
                "anim %r turns bone %r %.0f\u00b0 away from rest (its own "
                "channels only ask for %.0f\u00b0) — rotations accumulate "
                "down a chain, so per-bone values add up; the limb is very "
                "likely folding through itself"
                % (anim["name"], worst[1], worst[0], worst[2]))
        elif moved < 1.0 and anim["channels"]:
            warnings.append(
                "anim %r moves nothing — every channel resolves to no "
                "rotation, so the clip plays as a still frame"
                % anim["name"])

    # 6a2. geometry that touches nothing else
    islands = _islands(V, T, mesh.part_ranges, touch=0.01 * max(height, 1e-6))
    if len(islands) > 1:
        # Which clump is "the model" is decided by the root bone, not by size.
        # Sorting by vertex count picks whichever clump happens to be densest,
        # and two floating motes out-vertex a plain torso — at which point the
        # lint accuses the body of floating away from the decoration.
        depth = {}
        for name, bn in bones.items():
            d, cur = 0, bn
            while cur.parent is not None:
                d += 1
                cur = cur.parent
            depth[name] = d
        shallowest = {}
        for k, (v0, v1) in mesh.part_ranges.items():
            ds = [depth[nm] for i in range(v0, v1)
                  for nm, w in mesh.skin[i] if nm in depth and w > 0.0]
            shallowest[k] = min(ds) if ds else 99

        def anchored(g):
            # nearest-to-the-root wins, then size. Depth rather than root
            # weight because a root bone frequently carries no geometry of its
            # own, and rather than size because two floating motes out-vertex
            # a plain torso — which had this lint accusing the body of
            # floating away from the decoration.
            return (-min(shallowest[k] for k in g),
                    sum(mesh.part_ranges[k][1] - mesh.part_ranges[k][0] for k in g))
        islands.sort(key=anchored, reverse=True)
        for group in islands[1:]:
            names = ", ".join(repr(k) for k in sorted(group)[:4]) + \
                (" (+%d more)" % (len(group) - 4) if len(group) > 4 else "")
            warnings.append(
                "%s %s attached to nothing — %s separated from every other "
                "part of the model, so %s float%s free. Anything meant to sit "
                "on the body has to reach it: grow it, move it, or place it "
                "with on=<host part>"
                % ("part" if len(group) == 1 else "parts", names,
                   "it is" if len(group) == 1 else "they are",
                   "it" if len(group) == 1 else "they",
                   "s" if len(group) == 1 else ""))

    # 6b. parts swallowed whole by another part
    for a, b in _buried_parts(V, T, mesh.part_ranges):
        warnings.append(
            "part %r is entirely inside %r — it is invisible from every "
            "angle, so it costs triangles and shows nothing; move it out, "
            "grow it past the surface, or place it with on=%s"
            % (a, b, b.rsplit(".", 1)[0]))

    double_sided = sorted(getattr(mesh, "double_sided_materials", ()))
    if double_sided:
        infos.append("double-sided materials: %s" % ", ".join(double_sided))

    # 7. authored measurement checks
    fails, measurements = wchecks.evaluate(model, bones, mesh, V)
    warnings.extend("check failed — %s" % f for f in fails)
    infos.extend("check: %s" % m for m in measurements)

    infos.append("%d vertices, %d triangles, %d materials"
                 % (len(V), len(T), len(mesh.materials)))
    return warnings, infos
