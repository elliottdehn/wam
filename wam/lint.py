
"""Semantic lint: catches the mistakes that make models look broken."""
import ast

import numpy as np


class _Ref:
    """An unresolved name in a check expression (a bone or a part)."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


class _CheckError(Exception):
    pass


def _measure_env(model, bones, mesh, V):
    """Names and functions available inside `checks` expressions."""
    ranges = mesh.part_ranges

    def bone(ref, want="head"):
        name = ref.name if isinstance(ref, _Ref) else str(ref)
        for suffix in ("head", "tail", "mid"):
            if name.endswith("." + suffix):
                name, want = name[:-len(suffix) - 1], suffix
                break
        b = bones.get(name) or bones.get(name + ".l")
        if b is None:
            raise _CheckError("no bone named %r" % name)
        return b, want

    def point(ref):
        name = ref.name if isinstance(ref, _Ref) else str(ref)
        base = name.rsplit(".", 1)[0] if name.rsplit(".", 1)[-1] in (
            "head", "tail", "mid") else name
        if bones.get(base) or bones.get(base + ".l"):
            b, want = bone(ref)
            return {"head": b.head, "tail": b.tail,
                    "mid": (b.head + b.tail) / 2.0}[want]
        for key in (name, name + ".l"):
            if key in ranges:
                v0, v1 = ranges[key]
                chunk = V[v0:v1]
                return (chunk.min(axis=0) + chunk.max(axis=0)) / 2.0
        raise _CheckError("%r is neither a bone nor a part" % name)

    def part_bbox(ref):
        name = ref.name if isinstance(ref, _Ref) else str(ref)
        for key in (name, name + ".l"):
            if key in ranges:
                v0, v1 = ranges[key]
                chunk = V[v0:v1]
                return chunk.min(axis=0), chunk.max(axis=0)
        raise _CheckError("no part named %r" % name)

    def axis_size(ref, i):
        lo, hi = part_bbox(ref)
        return float(hi[i] - lo[i])

    def bone_len(ref):
        b, _ = bone(ref)
        if b.len <= 0:
            raise _CheckError("bone %r has zero length" % b.name)
        return float(b.len)

    ymin = float(V[:, 1].min())
    scalars = {
        "height": float(V[:, 1].max() - ymin),
        "width": float(V[:, 0].max() - V[:, 0].min()),
        "depth": float(V[:, 2].max() - V[:, 2].min()),
        "ground": ymin,
        "top": float(V[:, 1].max()),
        "pi": np.pi,
    }
    funcs = {
        "dist": lambda a, b: float(np.linalg.norm(point(a) - point(b))),
        "len": bone_len,
        "x": lambda r: float(point(r)[0]),
        "y": lambda r: float(point(r)[1]),
        "z": lambda r: float(point(r)[2]),
        "width": lambda r: axis_size(r, 0),
        "height": lambda r: axis_size(r, 1),
        "depth": lambda r: axis_size(r, 2),
        "span": lambda r: max(axis_size(r, i) for i in range(3)),
        "abs": lambda v: abs(_number(v, "abs")),
        "min": lambda *vs: min(_number(v, "min") for v in vs),
        "max": lambda *vs: max(_number(v, "max") for v in vs),
    }
    return scalars, funcs


def _number(v, where):
    if isinstance(v, _Ref):
        raise _CheckError("%r is a name, not a measurement — wrap it in a "
                          "function like len() or y()" % v.name)
    return float(v)


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
        raise _CheckError("unsupported name in expression")
    parts.append(node.id)
    return ".".join(reversed(parts))


def _eval_node(node, scalars, funcs):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, scalars, funcs)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise _CheckError("only numbers are allowed as literals")
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _number(_eval_node(node.operand, scalars, funcs), "-")
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        a = _number(_eval_node(node.left, scalars, funcs), "operator")
        b = _number(_eval_node(node.right, scalars, funcs), "operator")
        if isinstance(node.op, ast.Div) and abs(b) < 1e-12:
            raise _CheckError("division by zero")
        return _BINOPS[type(node.op)](a, b)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in funcs:
            raise _CheckError("unknown function in expression")
        args = [_eval_node(a, scalars, funcs) for a in node.args]
        return funcs[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in scalars:
            return scalars[node.id]
        return _Ref(node.id)
    if isinstance(node, ast.Attribute):
        return _Ref(_dotted(node))
    raise _CheckError("unsupported expression")


def eval_checks(model, bones, mesh, V):
    """Run the `checks` section. Returns (failures, measurements)."""
    failures, measurements = [], []
    checks = getattr(model, "checks", ())
    if not checks:
        return failures, measurements
    scalars, funcs = _measure_env(model, bones, mesh, V)
    for c in checks:
        try:
            tree = ast.parse(c["expr"], mode="eval")
            value = _number(_eval_node(tree, scalars, funcs), "assert")
        except _CheckError as e:
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
            ok = c["lo"] <= value <= c["hi"]
            want = "in %.4g..%.4g" % (c["lo"], c["hi"])
        elif c["op"] == "==":
            ok = abs(value - c["val"]) <= c["tol"]
            want = ("== %.4g +- %.4g" % (c["val"], c["tol"]) if c["tol"]
                    else "== %.4g" % c["val"])
        else:
            ok = {"<": value < c["val"], ">": value > c["val"],
                  "<=": value <= c["val"], ">=": value >= c["val"]}[c["op"]]
            want = "%s %.4g" % (c["op"], c["val"])
        if not ok:
            failures.append("line %d: %s = %.4f, expected %s"
                            % (c["line_no"], c["expr"], value, want))
        else:
            measurements.append("%s = %.4f (%s, ok)" % (c["expr"], value, want))
    return failures, measurements


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

    double_sided = sorted(getattr(mesh, "double_sided_materials", ()))
    if double_sided:
        infos.append("double-sided materials: %s" % ", ".join(double_sided))

    # 7. authored measurement checks
    fails, measurements = eval_checks(model, bones, mesh, V)
    warnings.extend("check failed — %s" % f for f in fails)
    infos.extend("check: %s" % m for m in measurements)

    infos.append("%d vertices, %d triangles, %d materials"
                 % (len(V), len(T), len(mesh.materials)))
    return warnings, infos
