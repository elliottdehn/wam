"""Tiny software rasterizer: perspective camera, Gouraud shading, PNG out."""
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


def vertex_normals(V, T):
    N = np.zeros_like(V)
    if len(T) == 0:
        return N
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    fn = np.cross(b - a, c - a)  # area-weighted
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


def render_view(V, T, tri_mat, mat_colors, yaw_deg=0.0, pitch_deg=10.0,
                width=480, height=600, fov_deg=28.0, bg=(0.92, 0.92, 0.94),
                ground_y=None, margin=1.12, vert_colors=None,
                uv=None, tex=None, sky=None, fog=None,
                eye=None, look=None, detail=None, detail_scale=180.0,
                dist=None, center=None, fit="extents"):
    """Render one view: orbit camera by default, or first-person when
    eye=(x,y,z) and look=(x,y,z) are given.

    Pass `dist`/`center` (see `fit_distance`) to hold the framing constant
    across a set of views — otherwise each panel fits itself and the same
    model silently renders at a different scale in every panel.

    `fit="sphere"` restores the old bounding-sphere framing, for scene
    renders whose `margin` was hand-tuned against it.
    """
    V = np.asarray(V, dtype=np.float64)
    N = vertex_normals(V, T)

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
                dmix = 0.55 * dsamp(detail_scale, 0.0) +                        0.45 * dsamp(detail_scale * 0.27, 0.37)
                base_px = base_px * (0.82 + 0.36 * dmix)
            px = base_px * shi[..., None]
        elif vert_colors is not None:
            ca, cb, cc = vert_colors[ia], vert_colors[ib], vert_colors[ic]
            px = (w0[..., None] * ca + w1[..., None] * cb + w2[..., None] * cc) * shi[..., None]
        else:
            color = np.asarray(mat_colors[tri_mat[ti]])
            px = color[None, None, :] * shi[..., None]
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
