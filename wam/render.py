"""Tiny software rasterizer: perspective camera, Gouraud shading, PNG out."""
import functools
import math
import struct
import zlib

import numpy as np


def png_bytes(img):
    """Encode (H,W,3) float image to PNG bytes."""
    import io
    buf = io.BytesIO()
    _write_png_fh(buf, img)
    return buf.getvalue()


def _write_png_fh(f, img):
    h, w, _ = img.shape
    data = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    raw = b"".join(b"\x00" + data[y].tobytes() for y in range(h))

    def chunk(tag, payload):
        c = struct.pack(">I", len(payload)) + tag + payload
        return c + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    f.write(b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def write_png(path, img):
    """img: (H,W,3) float 0..1"""
    h, w, _ = img.shape
    data = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    raw = b"".join(b"\x00" + data[y].tobytes() for y in range(h))

    def chunk(tag, payload):
        c = struct.pack(">I", len(payload)) + tag + payload
        return c + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def quiet_fp(fn):
    """Silence numpy's floating-point warnings inside the rasterizer.

    macOS Accelerate raises stale divide-by-zero / overflow / invalid flags on
    ordinary matmuls — reproducible with `np.random.rand(2633, 3) @
    np.random.rand(3, 3).T` and unrelated to any model data. Left alone it
    emits a dozen lines per compile and buries the lint output, which is the
    part anyone actually needs to read.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            return fn(*args, **kwargs)
    return wrapper


@quiet_fp
def weld_groups(V, groups=None):
    """Map each vertex to a representative index shared by coincident ones.

    Texture charts and material bands both split vertices that sit at the
    same point, which would otherwise shade as a hard crease: each copy sees
    only the faces on its own side of the seam. Normals are accumulated over
    the welded set so a seam changes the color of a surface, never its
    shading.

    `groups` opts a part out of that. Smoothing is the right default, and it
    is also what makes the faceted look unaskable-for: every attempt to give
    a face its own normal is welded straight back into the average. Vertices
    carrying different group ids never weld, so a part split per-triangle
    keeps its hard edges.
    """
    if not len(V):
        return np.zeros(0, dtype=np.int64)
    key = np.round(np.asarray(V, dtype=float), 6) + 0.0
    if groups is not None and len(groups) == len(V):
        key = np.column_stack([key, np.asarray(groups, dtype=float)])
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1)


@quiet_fp
def vertex_normals(V, T, weld=True, groups=None):
    N = np.zeros_like(V)
    if len(T) == 0:
        return N
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    fn = np.cross(b - a, c - a)  # area-weighted
    if weld:
        inv = weld_groups(V, groups)
        acc = np.zeros((int(inv.max()) + 1, 3))
        for i in range(3):
            np.add.at(acc, inv[T[:, i]], fn)
        N = acc[inv]
    else:
        for i in range(3):
            np.add.at(N, T[:, i], fn)
    lens = np.linalg.norm(N, axis=1, keepdims=True)
    lens[lens < 1e-12] = 1.0
    return N / lens


def orbit_basis(yaw_deg=0.0, pitch_deg=10.0):
    """Camera rotation for the orbit camera at a given yaw/pitch."""
    ya = math.radians(yaw_deg)
    pa = math.radians(pitch_deg)
    Ry = np.array([[math.cos(ya), 0, -math.sin(ya)],
                   [0, 1, 0],
                   [math.sin(ya), 0, math.cos(ya)]])
    Rx = np.array([[1, 0, 0],
                   [0, math.cos(pa), -math.sin(pa)],
                   [0, math.sin(pa), math.cos(pa)]])
    return Rx @ Ry


@quiet_fp
def fit_distance(V, center, R, fov_deg=28.0, aspect=1.0, margin=1.12):
    """Camera distance that just contains the model in *this* view.

    Fitting the 3D bounding sphere (the obvious thing) wastes the frame on
    anything elongated — a dragon renders tiny because its sphere is far
    bigger than any silhouette — and ignores the horizontal field of view
    entirely, so a model wider than it is tall runs off the sides of a
    portrait canvas. This solves the projection instead: for every vertex,
    the distance at which it lands inside the frame, horizontally *and*
    vertically, given the canvas aspect.
    """
    V = np.asarray(V, dtype=np.float64)
    if not len(V):
        return 1.0
    Vc = (V - np.asarray(center, dtype=float)) @ R.T
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    need_x = Vc[:, 2] + np.abs(Vc[:, 0]) * margin * f / aspect
    need_y = Vc[:, 2] + np.abs(Vc[:, 1]) * margin * f
    return float(max(need_x.max(), need_y.max(), 1e-6))


@quiet_fp
def render_view(V, T, tri_mat, mat_colors, yaw_deg=0.0, pitch_deg=10.0,
                width=480, height=600, fov_deg=28.0, bg=(0.92, 0.92, 0.94),
                ground_y=None, margin=1.12, vert_colors=None,
                uv=None, tex=None, sky=None, fog=None,
                eye=None, look=None, detail=None, detail_scale=180.0,
                dist=None, center=None, fit="extents", shade_group=None,
                mat_pbr=None):
    """Render one view: orbit camera by default, or first-person when
    eye=(x,y,z) and look=(x,y,z) are given.

    Pass `dist`/`center` (see `fit_distance`) to hold the framing constant
    across a set of views — otherwise each panel fits itself and the same
    model silently renders at a different scale in every panel.

    `fit="sphere"` restores the old bounding-sphere framing, for scene
    renders whose `margin` was hand-tuned against it.
    """
    V = np.asarray(V, dtype=np.float64)
    N = vertex_normals(V, T, groups=shade_group)

    if eye is not None:
        eye = np.asarray(eye, dtype=float)
        f = np.asarray(look, dtype=float) - eye
        f = f / np.linalg.norm(f)
        upv = np.array([0.0, 1.0, 0.0])
        r = np.cross(f, upv)
        r = r / max(np.linalg.norm(r), 1e-9)
        u2 = np.cross(r, f)
        R = np.stack([r, u2, -f])
        Vc = (V - eye) @ R.T
        Nc = N @ R.T
    else:
        if center is None:
            center = (V.min(axis=0) + V.max(axis=0)) / 2
        center = np.asarray(center, dtype=float)
        R = orbit_basis(yaw_deg, pitch_deg)
        if dist is None and fit == "sphere":
            radius = np.linalg.norm(V - center, axis=1).max()
            dist = radius * margin / math.tan(math.radians(fov_deg) / 2)
        elif dist is None:
            dist = fit_distance(V, center, R, fov_deg, width / height, margin)
        Vc = (V - center) @ R.T
        Vc[:, 2] -= dist          # camera at origin looking down -Z
        Nc = N @ R.T

    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    aspect = width / height
    z = -Vc[:, 2]
    z[z < 1e-6] = 1e-6
    sx = (Vc[:, 0] * f / aspect / z * 0.5 + 0.5) * width
    sy = (0.5 - Vc[:, 1] * f / z * 0.5) * height

    # lighting: fixed world-space sun + soft fill (matches the viewer)
    L1 = np.array([-0.45, 0.85, 0.40])
    L1 /= np.linalg.norm(L1)
    L2 = np.array([0.55, 0.15, -0.60])
    L2 /= np.linalg.norm(L2)
    lam1 = np.clip(N @ L1, 0, None)
    lam2 = np.clip(N @ L2, 0, None)
    shade = 0.34 + 0.60 * lam1 + 0.16 * lam2

    # Specular is computed only for materials that actually declare metal or
    # rough. A setting the author cannot see in the sheet is a trap, but so is
    # silently restyling every model that never asked for it — so the default
    # path is left bit-for-bit as it was.
    ndh = None
    if mat_pbr is not None and any(mp is not None for mp in mat_pbr):
        if eye is not None:
            cam = np.asarray(eye, dtype=float)
        else:
            cam = np.asarray(center, dtype=float) + np.array([0.0, 0.0, dist]) @ R
        view = cam[None, :] - V
        view /= np.maximum(np.linalg.norm(view, axis=1, keepdims=True), 1e-9)
        H = L1[None, :] + view
        H /= np.maximum(np.linalg.norm(H, axis=1, keepdims=True), 1e-9)
        ndh = np.clip(np.einsum("ij,ij->i", N, H), 0.0, None)
        Hv = H
        # A metal has no diffuse, so with one sharp light and nothing to
        # reflect it renders black — which is physically defensible and
        # useless. Real engines make metal read through the environment, so
        # stand in a cheap hemisphere: bright above, dim below, sampled along
        # the reflected view. This is what carries the metal look; the
        # highlight only adds the glint on top.
        refl = view - 2.0 * np.einsum("ij,ij->i", view, N)[:, None] * N
        env = 0.45 + 0.55 * np.clip(-refl[:, 1] * 0.5 + 0.5, 0.0, 1.0)

    img = np.ones((height, width, 3))
    if sky is not None:
        top = np.array(sky[0])
        horizon = np.array(sky[1])
        tgrad = np.linspace(0.0, 1.0, height)[:, None, None] ** 1.6
        img[:] = top[None, None, :] * (1 - tgrad) + horizon[None, None, :] * tgrad
    else:
        img[:] = np.array(bg)
        grad = np.linspace(1.03, 0.93, height)[:, None, None]
        img *= grad
    zbuf = np.full((height, width), np.inf)

    order = None
    if len(T):
        a2, b2, c2 = T[:, 0], T[:, 1], T[:, 2]
        # backface culling in screen space
        ax, ay = sx[a2], sy[a2]
        bx, by = sx[b2], sy[b2]
        cx, cy = sx[c2], sy[c2]
        area2 = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        keep = np.abs(area2) > 1e-9
        order = np.nonzero(keep)[0]

    near_clip = 0.15 if eye is not None else 1e-6
    for ti in order if order is not None else []:
        ia, ib, ic = T[ti]
        zs = np.array([z[ia], z[ib], z[ic]])
        if zs.min() < near_clip:
            continue          # crosses the near plane: skip (no clipping)
        xs = np.array([sx[ia], sx[ib], sx[ic]])
        ys_ = np.array([sy[ia], sy[ib], sy[ic]])
        sh = np.array([shade[ia], shade[ib], shade[ic]])
        x0, x1 = int(max(0, math.floor(xs.min()))), int(min(width - 1, math.ceil(xs.max())))
        y0, y1 = int(max(0, math.floor(ys_.min()))), int(min(height - 1, math.ceil(ys_.max())))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = ((ys_[1] - ys_[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys_[0] - ys_[2]))
        if abs(d) < 1e-9:
            continue
        w0 = ((ys_[1] - ys_[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys_[2])) / d
        w1 = ((ys_[2] - ys_[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys_[2])) / d
        w2 = 1 - w0 - w1
        mask = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not mask.any():
            continue
        # perspective-correct-ish depth
        zi = 1.0 / (w0 / zs[0] + w1 / zs[1] + w2 / zs[2])
        zwin = zbuf[y0:y1 + 1, x0:x1 + 1]
        upd = mask & (zi < zwin)
        if not upd.any():
            continue
        shi = w0 * sh[0] + w1 * sh[1] + w2 * sh[2]
        # Each branch produces the *unlit* base colour; shading is applied once
        # afterwards. Folding the lighting into each branch instead is how
        # metal and roughness came to work only on untextured models — the
        # specular lived in the flat-colour arm and every textured or
        # vertex-coloured model took an earlier one and never reached it.
        if uv is not None and tex is not None:
            uu = w0 * uv[ia, 0] + w1 * uv[ib, 0] + w2 * uv[ic, 0]
            vv = w0 * uv[ia, 1] + w1 * uv[ib, 1] + w2 * uv[ic, 1]
            th, tw = tex.shape[:2]
            txi = np.clip((uu * tw).astype(int), 0, tw - 1)
            tyi = np.clip((vv * th).astype(int), 0, th - 1)
            base_px = tex[tyi, txi]
            if detail is not None:
                dh, dw = detail.shape[:2]

                def dsamp(scale, phase):
                    du = np.abs(((uu * scale + phase) % 2.0) - 1.0)
                    dv = np.abs(((vv * scale + phase) % 2.0) - 1.0)
                    dxi = np.clip((du * (dw - 1)).astype(int), 0, dw - 1)
                    dyi = np.clip((dv * (dh - 1)).astype(int), 0, dh - 1)
                    return detail[dyi, dxi]
                dmix = 0.55 * dsamp(detail_scale, 0.0) + \
                    0.45 * dsamp(detail_scale * 0.27, 0.37)
                base_px = base_px * (0.82 + 0.36 * dmix)
        elif vert_colors is not None:
            ca, cb, cc = vert_colors[ia], vert_colors[ib], vert_colors[ic]
            base_px = (w0[..., None] * ca + w1[..., None] * cb
                       + w2[..., None] * cc)
        else:
            base_px = np.asarray(mat_colors[tri_mat[ti]])[None, None, :]

        mi = tri_mat[ti]
        props = mat_pbr[mi] if (mat_pbr is not None and mi < len(mat_pbr)) else None
        if props is None or ndh is None:
            px = base_px * shi[..., None]
        else:
            metal, rough = props
            power = 2.0 + 512.0 * (1.0 - rough) ** 3
            gain = (1.0 - rough) ** 2 * (0.35 + 0.65 * metal)
            # N·H is raised to the power *per pixel*. Interpolating the lobe
            # across a triangle's corners loses it entirely on low-poly
            # geometry: at rough=0.1 the exponent is ~375, no vertex ever sits
            # on the highlight, and roughness silently does nothing.
            nn = (w0[..., None] * N[ia] + w1[..., None] * N[ib]
                  + w2[..., None] * N[ic])
            nn /= np.maximum(np.linalg.norm(nn, axis=-1, keepdims=True), 1e-9)
            hh = (w0[..., None] * Hv[ia] + w1[..., None] * Hv[ib]
                  + w2[..., None] * Hv[ic])
            hh /= np.maximum(np.linalg.norm(hh, axis=-1, keepdims=True), 1e-9)
            sp = np.clip((nn * hh).sum(axis=-1), 0.0, None) ** power
            ev = w0 * env[ia] + w1 * env[ib] + w2 * env[ic]
            # dielectric keeps its diffuse and takes a white glint; metal
            # trades diffuse for a tinted reflection of the sky
            lit = shi * (1.0 - metal) + ev * metal
            tint = base_px if metal > 0.5 else np.ones(3)[None, None, :]
            px = base_px * lit[..., None] + tint * (gain * sp)[..., None]
        if fog is not None:
            f = np.clip((zi - fog["start"]) / max(fog["end"] - fog["start"], 1e-6),
                        0, 1) * fog.get("max", 0.85)
            fc = np.array(fog["color"])
            px = px * (1 - f[..., None]) + fc[None, None, :] * f[..., None]
        win = img[y0:y1 + 1, x0:x1 + 1]
        win[upd] = np.clip(px[upd], 0, 1)
        zwin[upd] = zi[upd]

    return img


def hstack_views(images, pad=6, bg=0.85):
    h = max(im.shape[0] for im in images)
    total_w = sum(im.shape[1] for im in images) + pad * (len(images) + 1)
    sheet = np.full((h + 2 * pad, total_w, 3), bg)
    x = pad
    for im in images:
        sheet[pad:pad + im.shape[0], x:x + im.shape[1]] = im
        x += im.shape[1] + pad
    return sheet


def vstack_views(images, pad=6, bg=0.85):
    """Stack rows (each already an hstacked strip) into one sheet."""
    w = max(im.shape[1] for im in images)
    total_h = sum(im.shape[0] for im in images) + pad * (len(images) - 1)
    sheet = np.full((total_h, w, 3), bg)
    y = 0
    for im in images:
        sheet[y:y + im.shape[0], 0:im.shape[1]] = im
        y += im.shape[0] + pad
    return sheet


def grid_views(images, cols, pad=6, bg=0.85):
    rows = (len(images) + cols - 1) // cols
    ch = max(im.shape[0] for im in images)
    cw = max(im.shape[1] for im in images)
    sheet = np.full((rows * ch + pad * (rows + 1), cols * cw + pad * (cols + 1), 3), bg)
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        y = pad + r * (ch + pad)
        x = pad + c * (cw + pad)
        sheet[y:y + im.shape[0], x:x + im.shape[1]] = im
    return sheet
