
"""glTF 2.0 export: skinned mesh + bone nodes + animations, embedded buffer."""
import base64
import json
import math

import numpy as np

from .skeleton import rot_axis


def euler_to_quat(pitch, yaw, roll, bone_axis):
    """Compose rotations: yaw about +Y, pitch about +X, roll about bone axis.

    Returns (x, y, z, w).
    """
    R = rot_axis((0, 1, 0), yaw) @ rot_axis((1, 0, 0), pitch) @ rot_axis(bone_axis, roll)
    return mat_to_quat(R)


def mat_to_quat(R):
    t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return (x, y, z, w)


class BufferBuilder:
    def __init__(self):
        self.data = bytearray()
        self.views = []
        self.accessors = []

    def add(self, arr, target=None, comp_type=None, acc_type=None, normalized=False):
        arr = np.ascontiguousarray(arr)
        while len(self.data) % 4:
            self.data.append(0)
        off = len(self.data)
        self.data.extend(arr.tobytes())
        view = dict(buffer=0, byteOffset=off, byteLength=arr.nbytes)
        if target:
            view["target"] = target
        self.views.append(view)
        acc = dict(bufferView=len(self.views) - 1, componentType=comp_type,
                   count=arr.shape[0], type=acc_type)
        if acc_type == "VEC3" and comp_type == 5126:
            acc["min"] = [float(v) for v in arr.min(axis=0)]
            acc["max"] = [float(v) for v in arr.max(axis=0)]
        if acc_type == "SCALAR" and comp_type == 5126:
            acc["min"] = [float(arr.min())]
            acc["max"] = [float(arr.max())]
        self.accessors.append(acc)
        return len(self.accessors) - 1


def _vertex_normals(vertices, triangles, groups=None):
    """Area-weighted vertex normals from the final, corrected triangle order."""
    from .render import weld_groups
    N = np.zeros_like(vertices, dtype=np.float64)
    if len(triangles):
        a = vertices[triangles[:, 0]]
        b = vertices[triangles[:, 1]]
        c = vertices[triangles[:, 2]]
        fn = np.cross(b - a, c - a)
        good = np.linalg.norm(fn, axis=1) > 1e-14
        fn = fn[good]
        good_tris = triangles[good]
        # accumulate across texture/material seams, which duplicate vertices
        # at identical positions (see render.weld_groups)
        inv = weld_groups(vertices, groups)
        acc = np.zeros((int(inv.max()) + 1, 3)) if len(inv) else np.zeros((0, 3))
        for i in range(3):
            np.add.at(acc, inv[good_tris[:, i]], fn)
        N = acc[inv]
    ln = np.linalg.norm(N, axis=1, keepdims=True)
    orphan = ln[:, 0] < 1e-12
    ln[orphan] = 1.0
    N = N / ln
    # glTF requires normalized normals. Isolated/fully-degenerate vertices are
    # not referenced by a useful face, but a deterministic unit fallback keeps
    # validators and importers happy.
    N[orphan] = (0.0, 1.0, 0.0)
    return N.astype(np.float32)


def export(path, model, bones_dict, bone_order, mesh, anim_tracks, scale, vert_colors=None, uv=None, tex_png=None):
    """anim_tracks: list of dicts:
       {name, dur, loop, bones: {bone_name: [(t_sec, quat_xyzw), ...]}}"""
    V, T, M = mesh.arrays()
    V = V * scale

    bb = BufferBuilder()
    joint_index = {b.name: i for i, b in enumerate(bone_order)}

    # skin data
    JOINTS = np.zeros((len(V), 4), dtype=np.uint16)
    WEIGHTS = np.zeros((len(V), 4), dtype=np.float32)
    for vi, sk in enumerate(mesh.skin):
        # glTF carries four influences per vertex; normalize over the four
        # that survive so a truncated blend still sums to one.
        sk = sorted(sk, key=lambda nw: -nw[1])[:4]
        total = sum(w for _, w in sk) or 1.0
        for si, (bn, w) in enumerate(sk):
            JOINTS[vi, si] = joint_index[bn]
            WEIGHTS[vi, si] = w / total

    # Mesh generation performs all reflection and winding repair before export.
    # Recompute normals here from that final triangle order so Godot receives
    # normals consistent with backface culling.
    N = _vertex_normals(V, T, getattr(mesh, "shade_group", None))

    pos_acc = bb.add(V.astype(np.float32), 34962, 5126, "VEC3")
    nrm_acc = bb.add(N, 34962, 5126, "VEC3")
    j_acc = bb.add(JOINTS, 34962, 5123, "VEC4")
    w_acc = bb.add(WEIGHTS, 34962, 5126, "VEC4")
    col_acc = None
    if vert_colors is not None and tex_png is None:
        col_acc = bb.add(np.asarray(vert_colors, dtype=np.float32), 34962, 5126, "VEC3")
    uv_acc = None
    if uv is not None and tex_png is not None:
        uv_acc = bb.add(np.asarray(uv, dtype=np.float32), 34962, 5126, "VEC2")

    # primitives per material
    primitives = []
    for mi, (mname, rgb) in enumerate(mesh.materials):
        idx = T[M == mi].astype(np.uint32).reshape(-1)
        if len(idx) == 0:
            continue
        i_acc = bb.add(idx, 34963, 5125, "SCALAR")
        attrs = dict(POSITION=pos_acc, NORMAL=nrm_acc,
                     JOINTS_0=j_acc, WEIGHTS_0=w_acc)
        if col_acc is not None:
            attrs["COLOR_0"] = col_acc
        if uv_acc is not None:
            attrs["TEXCOORD_0"] = uv_acc
        primitives.append(dict(attributes=attrs, indices=i_acc,
                               material=len(primitives)))

    materials = []
    double_sided = set(getattr(mesh, "double_sided_materials", ()))
    double_sided.update(getattr(model, "double_sided_materials", ()))
    used = [mi for mi, _ in enumerate(mesh.materials)
            if (M == mi).any()]
    for mi in used:
        mname, rgb = mesh.materials[mi]
        textured = vert_colors is not None or tex_png is not None
        base = [1.0, 1.0, 1.0, 1.0] if textured else [rgb[0], rgb[1], rgb[2], 1.0]
        pbr = dict(baseColorFactor=base, metallicFactor=0.0, roughnessFactor=0.9)
        if tex_png is not None:
            pbr["baseColorTexture"] = dict(index=0)
        material = dict(name=mname, pbrMetallicRoughness=pbr)
        if mname in double_sided:
            material["doubleSided"] = True
        materials.append(material)

    # nodes: mesh node + bone nodes
    nodes = [dict(name="mesh", mesh=0, skin=0)]
    node_of_bone = {}
    for b in bone_order:
        node_of_bone[b.name] = len(nodes)
        parent_head = b.parent.head if b.parent else np.zeros(3)
        tr = (b.head - parent_head) * scale
        nodes.append(dict(name=b.name, translation=[float(x) for x in tr]))
    for b in bone_order:
        if b.parent is not None:
            pn = nodes[node_of_bone[b.parent.name]]
            pn.setdefault("children", []).append(node_of_bone[b.name])

    root_bones = [node_of_bone[b.name] for b in bone_order if b.parent is None]
    scene_nodes = [0] + root_bones

    # inverse bind matrices: translate(-head)
    ibms = np.zeros((len(bone_order), 4, 4), dtype=np.float32)
    for i, b in enumerate(bone_order):
        m = np.eye(4, dtype=np.float32)
        m[3, 0:3] = -b.head * scale  # column-major: translation in last row when flattened
        ibms[i] = m
    ibm_acc = bb.add(ibms.reshape(len(bone_order), 16), None, 5126, "MAT4")

    skin = dict(joints=[node_of_bone[b.name] for b in bone_order],
                inverseBindMatrices=ibm_acc)

    animations = []
    for tr in anim_tracks:
        samplers, channels = [], []
        for bn, keys in tr["bones"].items():
            times = np.array([k[0] for k in keys], dtype=np.float32)
            quats = np.array([k[1] for k in keys], dtype=np.float32)
            t_acc = bb.add(times, None, 5126, "SCALAR")
            q_acc = bb.add(quats, None, 5126, "VEC4")
            samplers.append(dict(input=t_acc, output=q_acc, interpolation="LINEAR"))
            channels.append(dict(sampler=len(samplers) - 1,
                                 target=dict(node=node_of_bone[bn], path="rotation")))
        if channels:
            animations.append(dict(name=tr["name"], samplers=samplers, channels=channels))

    gltf = dict(
        asset=dict(version="2.0", generator="wam-compiler"),
        scene=0,
        scenes=[dict(nodes=scene_nodes)],
        nodes=nodes,
        meshes=[dict(primitives=primitives)],
        skins=[skin],
        materials=materials,
        buffers=[dict(byteLength=len(bb.data),
                      uri="data:application/octet-stream;base64,"
                          + base64.b64encode(bytes(bb.data)).decode())],
        bufferViews=bb.views,
        accessors=bb.accessors,
    )
    if animations:
        gltf["animations"] = animations
    if tex_png is not None:
        gltf["images"] = [dict(uri="data:image/png;base64,"
                               + base64.b64encode(tex_png).decode())]
        gltf["samplers"] = [dict(magFilter=9729, minFilter=9729,
                                 wrapS=33071, wrapT=33071)]
        gltf["textures"] = [dict(source=0, sampler=0)]

    with open(path, "w") as f:
        json.dump(gltf, f)
    return path
