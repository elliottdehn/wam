"""Skeleton solver: turns parsed bone directives into world-space bones.

Coordinate system: +Y up, +Z forward (the character faces +Z), +X = the
character's left. Mirroring reflects across the X=0 plane.

Angles: pitch is a right-hand rotation about +X (top tips toward +Z/forward),
yaw is about +Y (tips toward the character's left).  All lengths are fractions
of the model height.
"""
import math
import numpy as np

from .parser import WamError

BASE_DIRS = {
    "up": (0, 1, 0), "down": (0, -1, 0),
    "fwd": (0, 0, 1), "back": (0, 0, -1),
    "side": (1, 0, 0), "in": (-1, 0, 0),
}


def rot_x(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_z(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def rot_axis(axis, deg):
    axis = np.asarray(axis, dtype=float)
    n = np.linalg.norm(axis)
    if n < 1e-9:
        return np.eye(3)
    x, y, z = axis / n
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    C = 1 - c
    return np.array([
        [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
        [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
        [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


class Bone:
    def __init__(self, name, parent, head, direction, length):
        self.name = name
        self.parent = parent          # Bone or None
        self.head = np.asarray(head, dtype=float)
        self.dir = np.asarray(direction, dtype=float)
        self.len = float(length)
        self.children = []

    @property
    def tail(self):
        return self.head + self.dir * self.len

    def point_at(self, t):
        return self.head + self.dir * self.len * t


def resolve_dir(spec, pitch=0.0, yaw=0.0, tilt=0.0):
    if spec is None:
        raise WamError("bone needs a dir= (up/down/fwd/back/side/in)")
    if spec not in BASE_DIRS:
        raise WamError("unknown dir %r" % spec)
    v = np.array(BASE_DIRS[spec], dtype=float)
    v = rot_y(yaw) @ rot_x(pitch) @ rot_z(tilt) @ v
    return v / np.linalg.norm(v)


def solve(model):
    """Return dict name -> Bone (mirror blocks expanded to .l / .r)."""
    bones = {}
    order = []

    def flip(v):
        return np.array([-v[0], v[1], v[2]])

    def find_parent(ref, suffix):
        if ref is None:
            return None
        for candidate in (ref + suffix, ref):
            if candidate in bones:
                return bones[candidate]
        raise WamError("bone parent %r not defined (must appear earlier)" % ref)

    def make(b, suffix, mirrored):
        name = b["name"] + suffix
        if name in bones:
            raise WamError("duplicate bone %r" % name)
        if b.get("parent") is None:  # root
            bone = Bone(name, None, b["root_pos"], (0, 1, 0), b.get("len", 0.0))
        else:
            parent = find_parent(b["parent"], suffix)
            at = b.get("at", 1.0)
            head = parent.point_at(at) if parent.len > 0 else parent.head.copy()
            # sugar offsets: side -> +X, up -> +Y, fwd -> +Z
            off = np.array(b.get("offset", (0, 0, 0)), dtype=float)
            off[0] += b.get("side", 0.0)
            off[1] += b.get("up", 0.0)
            off[2] += b.get("fwd", 0.0)
            d = resolve_dir(b.get("dir") or "up", b.get("pitch", 0.0), b.get("yaw", 0.0), b.get("tilt", 0.0))
            if mirrored:
                off = flip(off)
                d = flip(d)
            bone = Bone(name, parent, head + off, d, b["len"])
            parent.children.append(bone)
        bones[name] = bone
        order.append(bone)
        return bone

    for b in model.bones:
        if b.get("mirror"):
            make(b, ".l", mirrored=False)
            make(b, ".r", mirrored=True)
        else:
            make(b, "", mirrored=False)

    return bones, order


def resolve_bone_ref(bones, ref, suffix=""):
    """Resolve a bone reference, preferring the suffixed (mirrored) variant."""
    if suffix:
        cand = ref if ref.endswith((".l", ".r")) else ref + suffix
        if cand in bones:
            return bones[cand]
    if ref in bones:
        return bones[ref]
    raise WamError("unknown bone %r" % ref)


def chain_bones(bones, first_ref, last_ref, suffix=""):
    """Walk from last up parents to first; return the chain in order."""
    first = resolve_bone_ref(bones, first_ref, suffix)
    last = resolve_bone_ref(bones, last_ref, suffix)
    chain = [last]
    cur = last
    while cur is not first:
        cur = cur.parent
        if cur is None:
            raise WamError("bones %s..%s are not an ancestor chain" %
                           (first_ref, last_ref))
        chain.append(cur)
    return list(reversed(chain))
