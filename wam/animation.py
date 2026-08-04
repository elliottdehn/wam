"""Animation resolution: poses -> per-bone rotation tracks, CPU skinning."""
import fnmatch

import numpy as np

from .parser import WamError
from .skeleton import rot_axis
from .gltf import mat_to_quat


def _match_bones(bones, pattern):
    names = list(bones.keys())
    if pattern in bones:
        return [pattern]
    hits = fnmatch.filter(names, pattern)
    if not hits:
        raise WamError("pose refers to unknown bone(s) %r" % pattern)
    return hits


def _mirror_rot(rot):
    """Mirror a rotation across X=0: pitch keeps sign; yaw, roll, tilt flip."""
    out = dict(rot)
    for k in ("yaw", "roll", "tilt"):
        if k in out:
            out[k] = -out[k]
    return out


def _swap_side(name):
    if name.endswith(".l"):
        return name[:-2] + ".r"
    if name.endswith(".r"):
        return name[:-2] + ".l"
    return name


def resolve_pose(model, bones, pose_name):
    """Return {bone_name: rot-dict}."""
    if pose_name == "rest":
        return {}
    if pose_name not in model.poses:
        raise WamError("unknown pose %r" % pose_name)
    pose = model.poses[pose_name]
    if pose.get("mirror_of"):
        src = resolve_pose(model, bones, pose["mirror_of"])
        return {_swap_side(bn): _mirror_rot(r) for bn, r in src.items()}
    out = {}
    for pat, rot in pose["bones"].items():
        for bn in _match_bones(bones, pat):
            out[bn] = rot
    return out


def rot_matrix_for(bone, rot):
    R = np.eye(3)
    if rot.get("roll"):
        R = rot_axis(bone.dir, rot["roll"]) @ R
    if rot.get("tilt"):
        R = rot_axis((0, 0, 1), rot["tilt"]) @ R
    if rot.get("pitch"):
        R = rot_axis((1, 0, 0), rot["pitch"]) @ R
    if rot.get("yaw"):
        R = rot_axis((0, 1, 0), rot["yaw"]) @ R
    return R


def _lerp_channel(keys, t):
    """keys: [(t, value)] with t in 0..1; linear, assumes sorted."""
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / max(t1 - t0, 1e-9)
            return v0 * (1 - f) + v1 * f
    return keys[-1][1]


def anim_rotations_at(model, bones, anim, phase):
    """All bone rotation dicts at a phase (0..1) of an anim."""
    rots = {}

    # pose keys: interpolate between consecutive poses channel-wise
    if anim["keys"]:
        keys = sorted(anim["keys"], key=lambda k: k[0])
        poses = [(t, resolve_pose(model, bones, p)) for t, p in keys]
        # gather all bones touched
        touched = set()
        for _, p in poses:
            touched.update(p.keys())
        for bn in touched:
            for ch in ("pitch", "yaw", "roll", "tilt"):
                series = [(t, p.get(bn, {}).get(ch, 0.0)) for t, p in poses]
                v = _lerp_channel(series, phase)
                if abs(v) > 1e-9:
                    rots.setdefault(bn, {})[ch] = v

    for ch in anim["channels"]:
        for bn in _match_bones(bones, ch["bone"]):
            keys = sorted(ch["keys"], key=lambda k: k[0])
            v = _lerp_channel(keys, phase)
            rots.setdefault(bn, {})[ch["chan"]] = rots.setdefault(bn, {}).get(ch["chan"], 0.0) + v

    if anim.get("mirrorphase") is not None:
        off = anim["mirrorphase"]
        mirrored = {}
        base = dict(rots)
        # left-side rotations get mirrored to right side at phase+off
        ph2 = (phase + off) % 1.0 if anim["loop"] else min(phase + off, 1.0)
        rots2 = anim_rotations_at(model, bones,
                                  dict(anim, mirrorphase=None), ph2)
        for bn, r in rots2.items():
            if bn.endswith(".l"):
                mirrored[_swap_side(bn)] = _mirror_rot(r)
        for bn, r in mirrored.items():
            if bn not in base:
                rots[bn] = r
    return rots


def global_transforms(bones_dict, bone_order, rots):
    """Compose per-bone global transforms G = G_parent * T(head) R T(-head)."""
    G = {}
    for b in bone_order:
        R = rot_matrix_for(b, rots.get(b.name, {}))
        # local affine about head
        A = np.eye(4)
        A[:3, :3] = R
        A[:3, 3] = b.head - R @ b.head
        if b.parent is not None:
            G[b.name] = G[b.parent.name] @ A
        else:
            sh = rots.get(b.name, {}).get("shift")
            if sh is not None:
                A[:3, 3] += np.asarray(sh)
            G[b.name] = A
    return G


def skin_verts(mesh, bones_dict, bone_order, rots):
    """CPU-skin the mesh for a set of bone rotations; returns new verts."""
    G = global_transforms(bones_dict, bone_order, rots)
    V, T, M = mesh.arrays()
    out = np.zeros_like(V)
    Vh = np.concatenate([V, np.ones((len(V), 1))], axis=1)
    # group verts by identical skin signature for speed
    groups = {}
    for vi, sk in enumerate(mesh.skin):
        key = tuple(sk)
        groups.setdefault(key, []).append(vi)
    for key, vis in groups.items():
        A = np.zeros((4, 4))
        total = sum(w for _, w in key) or 1.0
        for bn, w in key:
            A += G[bn] * (w / total)
        out[vis] = (Vh[vis] @ A.T)[:, :3]
    return out


def gltf_tracks(model, bones_dict, bone_order, n_samples=24):
    """Sample every anim into quaternion keyframes for glTF export."""
    tracks = []
    for anim in model.anims:
        dur = anim["dur"]
        bones_keys = {}
        for i in range(n_samples + 1):
            ph = i / n_samples
            rots = anim_rotations_at(model, bones_dict, anim, ph % 1.0 if anim["loop"] else ph)
            for b in bone_order:
                r = rots.get(b.name)
                R = rot_matrix_for(b, r) if r else np.eye(3)
                q = mat_to_quat(R)
                bones_keys.setdefault(b.name, []).append((ph * dur, q))
        # drop identity-only tracks
        keep = {}
        for bn, keys in bones_keys.items():
            if any(abs(q[3] - 1.0) > 1e-6 or abs(q[0]) + abs(q[1]) + abs(q[2]) > 1e-6
                   for _, q in keys):
                keep[bn] = keys
        tracks.append(dict(name=anim["name"], dur=dur, loop=anim["loop"], bones=keep))
    return tracks
