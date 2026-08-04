"""Semantic lint: catches the mistakes that make models look broken."""
import numpy as np


def lint(model, bones, mesh):
    warnings = list(getattr(mesh, "warnings", []))
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

    # 3. symmetry check: mirrored bounding boxes should match
    left = V[V[:, 0] > 0.01]
    right = V[V[:, 0] < -0.01]
    if len(left) and len(right):
        dw = abs(left[:, 0].max() + right[:, 0].min())
        if dw > 0.01:
            warnings.append("asymmetric silhouette: left extent %.3f vs right %.3f"
                            % (left[:, 0].max(), -right[:, 0].min()))

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

    for part in model.parts:
        if "bone" not in part:
            continue
        host_ref = part["bone"]
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
        if ndeg > len(T) * 0.05:
            warnings.append("%d degenerate triangles (>5%% of mesh)" % ndeg)

    infos.append("%d vertices, %d triangles, %d materials"
                 % (len(V), len(T), len(mesh.materials)))
    return warnings, infos
