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


def render_view(V, T, tri_mat, mat_colors, yaw_deg=0.0, pitch_deg=10.0,
                width=480, height=600, fov_deg=28.0, bg=(0.92, 0.92, 0.94),
                ground_y=None, margin=1.12, vert_colors=None,
                uv=None, tex=None):
    """Render one view. Camera orbits around Y axis; yaw=0 looks at +Z face."""
    V = np.asarray(V, dtype=np.float64)
    N = vertex_normals(V, T)

    center = (V.min(axis=0) + V.max(axis=0)) / 2
    radius = np.linalg.norm(V - center, axis=1).max()
    if ground_y is not None:
        # keep the ground line stable across views
        center = center.copy()

    # world -> camera: rotate so camera looks down -Z at the model
    ya = math.radians(yaw_deg)
    pa = math.radians(pitch_deg)
    Ry = np.array([[math.cos(ya), 0, -math.sin(ya)],
                   [0, 1, 0],
                   [math.sin(ya), 0, math.cos(ya)]])
    Rx = np.array([[1, 0, 0],
                   [0, math.cos(pa), -math.sin(pa)],
                   [0, math.sin(pa), math.cos(pa)]])
    R = Rx @ Ry

    dist = radius * margin / math.tan(math.radians(fov_deg) / 2)
    Vc = (V - center) @ R.T
    Vc[:, 2] -= dist          # camera at origin looking down -Z
    Nc = N @ R.T

    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    aspect = width / height
    z = -Vc[:, 2]
    z[z < 1e-6] = 1e-6
    sx = (Vc[:, 0] * f / aspect / z * 0.5 + 0.5) * width
    sy = (0.5 - Vc[:, 1] * f / z * 0.5) * height

    # lighting: key from upper-front-left of camera, plus fill and rim
    L1 = np.array([-0.35, 0.55, 0.76])
    L1 /= np.linalg.norm(L1)
    L2 = np.array([0.6, -0.1, 0.5])
    L2 /= np.linalg.norm(L2)
    lam1 = np.clip(Nc @ L1, 0, None)
    lam2 = np.clip(Nc @ L2, 0, None)
    shade = 0.34 + 0.62 * lam1 + 0.18 * lam2

    img = np.ones((height, width, 3))
    img[:] = np.array(bg)
    # subtle vertical gradient
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

    for ti in order if order is not None else []:
        ia, ib, ic = T[ti]
        xs = np.array([sx[ia], sx[ib], sx[ic]])
        ys_ = np.array([sy[ia], sy[ib], sy[ic]])
        zs = np.array([z[ia], z[ib], z[ic]])
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
            px = tex[tyi, txi] * shi[..., None]
        elif vert_colors is not None:
            ca, cb, cc = vert_colors[ia], vert_colors[ib], vert_colors[ic]
            px = (w0[..., None] * ca + w1[..., None] * cb + w2[..., None] * cc) * shi[..., None]
        else:
            color = np.asarray(mat_colors[tri_mat[ti]])
            px = color[None, None, :] * shi[..., None]
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
