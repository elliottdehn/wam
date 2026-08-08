/**
 * The turntable renderer.
 *
 * Lifted from `viewer/template.html` in the compiler repo — same shaders, same
 * skinning, same camera solve, so a model looks identical here and in the
 * viewer page the CLI drops next to your glTF. What is gone is everything the
 * turntable does not need: orbit input, wireframe, the skeleton overlay, fog
 * and the detail texture. Yaw is driven by the clock instead of the mouse.
 */
import type { WamAnim, WamModel } from './types'

const VS = `
attribute vec3 aPos; attribute vec3 aNrm; attribute vec3 aCol; attribute vec2 aUV;
attribute vec2 aPBR;
uniform mat4 uMVP;
varying vec3 vN; varying vec3 vC; varying vec2 vUV; varying vec3 vPos;
varying vec2 vPBR;
void main(){
  gl_Position = uMVP * vec4(aPos, 1.0);
  vN = aNrm;              // world-space; the sun is fixed in the world
  vC = aCol;
  vPBR = aPBR;
  vUV = aUV;
  vPos = aPos;
}`

const FS = `
precision mediump float;
varying vec3 vN; varying vec3 vC; varying vec2 vUV; varying vec3 vPos;
varying vec2 vPBR;
uniform sampler2D uTex;
uniform float uUseTex;
uniform vec3 uEye;
void main(){
  vec3 n = normalize(vN);
  float back = 0.0;
  if(dot(n, uEye - vPos) < 0.0){ n = -n; back = 1.0; }
  vec3 L1 = normalize(vec3(-0.45, 0.85, 0.40));   // sun
  vec3 L2 = normalize(vec3(0.55, 0.15, -0.60));   // fill
  float sh = 0.34 + 0.60*max(dot(n,L1),0.0) + 0.16*max(dot(n,L2),0.0);
  if(back > 0.5) sh *= 0.7;
  vec3 base = mix(vC, texture2D(uTex, vUV).rgb, uUseTex);
  vec3 lit = base * min(sh, 1.15);
  // vPBR.x < 0 marks "never declared", which keeps the flat look untouched.
  if(vPBR.x >= 0.0){
    float metal = vPBR.x, rough = vPBR.y;
    vec3 V = normalize(uEye - vPos);
    // A metal has no diffuse: with one sharp light and nothing to reflect it
    // renders black, so give it a hemisphere to pick up instead.
    vec3 R = reflect(-V, n);
    float env = 0.45 + 0.55 * clamp(R.y * 0.5 + 0.5, 0.0, 1.0);
    float shade = mix(min(sh, 1.15), env, metal);
    vec3 H = normalize(L1 + V);
    float power = 2.0 + 512.0 * pow(1.0 - rough, 3.0);
    float gain = pow(1.0 - rough, 2.0) * (0.35 + 0.65 * metal);
    float spec = pow(max(dot(n, H), 0.0), power) * gain;
    vec3 tint = metal > 0.5 ? base : vec3(1.0);
    lit = base * shade + tint * spec;
  }
  gl_FragColor = vec4(lit, 1.0);
}`

const IDENT: Quat = [0, 0, 0, 1]
const FOV = (Math.PI * 28) / 180

type Quat = [number, number, number, number]

function quatMat(q: Quat) {
  const [x, y, z, w] = q
  return [
    1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
    2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
    2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
  ]
}

function slerp(a: Quat, b: Quat, t: number): Quat {
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
  let s = 1
  if (d < 0) { d = -d; s = -1 }
  if (d > 0.9995) {
    const o = [
      a[0] + t * (s * b[0] - a[0]), a[1] + t * (s * b[1] - a[1]),
      a[2] + t * (s * b[2] - a[2]), a[3] + t * (s * b[3] - a[3]),
    ]
    const n = Math.hypot(o[0], o[1], o[2], o[3])
    return [o[0] / n, o[1] / n, o[2] / n, o[3] / n]
  }
  const th = Math.acos(d), st = Math.sin(th)
  const sa = Math.sin((1 - t) * th) / st, sb = (s * Math.sin(t * th)) / st
  return [
    a[0] * sa + b[0] * sb, a[1] * sa + b[1] * sb,
    a[2] * sa + b[2] * sb, a[3] * sa + b[3] * sb,
  ]
}

export interface TurntableOptions {
  /** Radians per second. */
  speed?: number
  /** Starting camera elevation in radians; positive looks down on the model. */
  pitch?: number
  /** Starting fraction of the fitted distance; >1 pulls back. */
  zoom?: number
  background?: [number, number, number]
  /** Name of an animation to play, or undefined for the rest pose. */
  anim?: string
  /** Drag to orbit, wheel/pinch to zoom, arrows to nudge. */
  interactive?: boolean
  /**
   * Milliseconds of stillness before the spin picks up again after someone
   * lets go. 0 leaves it stopped — on a shared link a page that never turns
   * again after one stray click reads as broken, so it resumes by default.
   */
  resumeAfter?: number
}

const ZOOM_MIN = 0.12
const ZOOM_MAX = 4.0
const PITCH_LIMIT = 1.2

interface Loaded {
  data: WamModel
  NV: number
  NT: number
  NB: number
  center: [number, number, number]
  dist: number
  fpos: Float32Array
  fnrm: Float32Array
  posed: Float64Array
  G: Float64Array
  texReady: boolean
}

export class Turntable {
  private gl: WebGLRenderingContext
  private prog: WebGLProgram
  private loc: Record<string, number | WebGLUniformLocation | null> = {}
  private buf: Record<string, WebGLBuffer> = {}
  private glTex: WebGLTexture
  private M: Loaded | null = null
  private raf = 0
  private yaw = 0.55
  private last = 0
  private clock = 0
  private ro: ResizeObserver
  private opts: Required<Omit<TurntableOptions, 'anim'>> & { anim?: string }
  // Camera state is mutable once the pointer can move it, so it lives here
  // rather than being read back out of the options every frame.
  private pitch: number
  private zoom: number
  private spinning = true
  private idleSince = 0
  private pointers = new Map<number, { x: number; y: number }>()
  private pinchDist = 0
  private detach: Array<() => void> = []
  // Declared rather than a constructor parameter property: this project builds
  // with `erasableSyntaxOnly`, which rejects the shorthand.
  private canvas: HTMLCanvasElement

  constructor(canvas: HTMLCanvasElement, opts: TurntableOptions = {}) {
    this.canvas = canvas
    this.opts = {
      speed: opts.speed ?? 0.45,
      pitch: opts.pitch ?? 0.16,
      zoom: opts.zoom ?? 1.0,
      background: opts.background ?? [0.09, 0.11, 0.11],
      anim: opts.anim,
      interactive: opts.interactive ?? true,
      resumeAfter: opts.resumeAfter ?? 3000,
    }
    this.pitch = this.opts.pitch
    this.zoom = this.opts.zoom
    const gl = canvas.getContext('webgl', { antialias: true, alpha: false })
    if (!gl) throw new Error('WebGL is unavailable in this browser')
    this.gl = gl

    this.prog = this.makeProgram(VS, FS)
    for (const a of ['aPos', 'aNrm', 'aCol', 'aUV', 'aPBR']) {
      this.loc[a] = gl.getAttribLocation(this.prog, a)
    }
    for (const u of ['uMVP', 'uTex', 'uUseTex', 'uEye']) {
      this.loc[u] = gl.getUniformLocation(this.prog, u)
    }
    for (const b of ['pos', 'nrm', 'col', 'uv', 'pbr']) {
      this.buf[b] = gl.createBuffer()!
    }
    this.glTex = gl.createTexture()!

    gl.enable(gl.DEPTH_TEST)
    gl.depthFunc(gl.LEQUAL)
    // Open shells (webs, arcs) are single-sided surfaces; culling would punch
    // holes in exactly the parts that need to read from both sides.
    gl.disable(gl.CULL_FACE)

    this.ro = new ResizeObserver(() => this.resize())
    this.ro.observe(canvas)
    this.resize()
    if (this.opts.interactive) this.bindInput()
    // Reachable from the DOM node so the camera can be inspected from the
    // console or a test without threading a ref through every component.
    ;(canvas as HTMLCanvasElement & { turntable?: Turntable }).turntable = this
  }

  /** Current camera, for debugging and tests. */
  state() {
    return {
      yaw: this.yaw, pitch: this.pitch, zoom: this.zoom,
      spinning: this.spinning, anim: this.opts.anim ?? null,
    }
  }

  // ---- input ---------------------------------------------------------------

  private bindInput() {
    const cv = this.canvas
    const on = <K extends keyof HTMLElementEventMap>(
      type: K,
      fn: (ev: HTMLElementEventMap[K]) => void,
      opts?: AddEventListenerOptions,
    ) => {
      cv.addEventListener(type, fn as EventListener, opts)
      this.detach.push(() => cv.removeEventListener(type, fn as EventListener, opts))
    }

    cv.style.touchAction = 'none'   // or a touch drag scrolls the page instead
    cv.style.cursor = 'grab'
    cv.tabIndex = 0

    on('pointerdown', (e) => {
      cv.setPointerCapture(e.pointerId)
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
      this.pinchDist = 0
      this.interacted()
      cv.style.cursor = 'grabbing'
    })

    on('pointermove', (e) => {
      const prev = this.pointers.get(e.pointerId)
      if (!prev) return
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
      this.interacted()

      if (this.pointers.size >= 2) {
        // Two fingers: pinch. Orbiting on the same gesture fights the pinch.
        const [a, b] = [...this.pointers.values()]
        const dist = Math.hypot(a.x - b.x, a.y - b.y)
        if (this.pinchDist > 0 && dist > 0) {
          this.setZoom(this.zoom * (this.pinchDist / dist))
        }
        this.pinchDist = dist
        return
      }
      this.yaw += (e.clientX - prev.x) * 0.008
      this.pitch = Math.max(
        -PITCH_LIMIT,
        Math.min(PITCH_LIMIT, this.pitch + (e.clientY - prev.y) * 0.006),
      )
    })

    const release = (e: PointerEvent) => {
      this.pointers.delete(e.pointerId)
      if (this.pointers.size < 2) this.pinchDist = 0
      if (this.pointers.size === 0) cv.style.cursor = 'grab'
      this.interacted()
    }
    on('pointerup', release)
    on('pointercancel', release)
    on('lostpointercapture', release)

    // passive: false so the page does not scroll out from under the model.
    on('wheel', (e) => {
      e.preventDefault()
      this.setZoom(this.zoom * Math.exp(e.deltaY * 0.0012))
      this.interacted()
    }, { passive: false })

    on('dblclick', (e) => {
      e.preventDefault()
      this.resetView()
    })

    on('keydown', (e) => {
      const step = e.shiftKey ? 0.24 : 0.08
      switch (e.key) {
        case 'ArrowLeft': this.yaw -= step; break
        case 'ArrowRight': this.yaw += step; break
        case 'ArrowUp':
          this.pitch = Math.max(-PITCH_LIMIT, this.pitch - step); break
        case 'ArrowDown':
          this.pitch = Math.min(PITCH_LIMIT, this.pitch + step); break
        case '+': case '=': this.setZoom(this.zoom * 0.88); break
        case '-': case '_': this.setZoom(this.zoom / 0.88); break
        default: return
      }
      e.preventDefault()
      this.interacted()
    })
  }

  private setZoom(z: number) {
    this.zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z))
  }

  /** Hand control to the pointer and start the clock on giving it back. */
  private interacted() {
    this.spinning = false
    this.idleSince = performance.now()
  }

  private makeProgram(vs: string, fs: string) {
    const gl = this.gl
    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, src)
      gl.compileShader(s)
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        throw new Error(gl.getShaderInfoLog(s) ?? 'shader compile failed')
      }
      return s
    }
    const p = gl.createProgram()!
    gl.attachShader(p, compile(gl.VERTEX_SHADER, vs))
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs))
    gl.linkProgram(p)
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(p) ?? 'program link failed')
    }
    return p
  }

  private resize() {
    const gl = this.gl
    const r = this.canvas.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    const w = Math.max(1, Math.round(r.width * dpr))
    const h = Math.max(1, Math.round(r.height * dpr))
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w
      this.canvas.height = h
    }
    gl.viewport(0, 0, w, h)
  }

  load(data: WamModel) {
    const gl = this.gl
    const NV = data.verts.length / 3
    const NT = data.tris.length / 3
    const NB = data.bones.length

    const bbMin = [1e9, 1e9, 1e9]
    const bbMax = [-1e9, -1e9, -1e9]
    for (let v = 0; v < NV; v++) {
      for (let a = 0; a < 3; a++) {
        const val = data.verts[v * 3 + a]
        if (val < bbMin[a]) bbMin[a] = val
        if (val > bbMax[a]) bbMax[a] = val
      }
    }
    const center: [number, number, number] = [
      (bbMin[0] + bbMax[0]) / 2, (bbMin[1] + bbMax[1]) / 2, (bbMin[2] + bbMax[2]) / 2,
    ]
    const radius = Math.hypot(
      bbMax[0] - bbMin[0], bbMax[1] - bbMin[1], bbMax[2] - bbMin[2],
    ) / 2

    // Triangles get private corners: the compiler's normals are per-face and
    // sharing vertices would average them into the smooth shading these models
    // are not built for.
    const fcol = new Float32Array(NT * 9)
    const fuv = new Float32Array(NT * 6)
    const fpbr = new Float32Array(NT * 6)
    for (let t = 0; t < NT; t++) {
      const mt = data.mats[data.triMat[t]]
      for (let k = 0; k < 3; k++) {
        const vi = data.tris[t * 3 + k]
        let r: number, g: number, b: number
        if (data.vcols) {
          r = data.vcols[vi * 3]; g = data.vcols[vi * 3 + 1]; b = data.vcols[vi * 3 + 2]
        } else {
          ;[r, g, b] = mt.rgb
        }
        fcol[t * 9 + k * 3] = r
        fcol[t * 9 + k * 3 + 1] = g
        fcol[t * 9 + k * 3 + 2] = b
        fpbr[t * 6 + k * 2] = mt?.metal !== undefined ? mt.metal : -1
        fpbr[t * 6 + k * 2 + 1] = mt?.rough !== undefined ? mt.rough : 0.9
        if (data.uv) {
          fuv[t * 6 + k * 2] = data.uv[vi * 2]
          fuv[t * 6 + k * 2 + 1] = data.uv[vi * 2 + 1]
        }
      }
    }

    const fpos = new Float32Array(NT * 9)
    const fnrm = new Float32Array(NT * 9)
    const put = (b: WebGLBuffer, d: Float32Array, usage: number) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, b)
      gl.bufferData(gl.ARRAY_BUFFER, d, usage)
    }
    put(this.buf.col, fcol, gl.STATIC_DRAW)
    put(this.buf.pbr, fpbr, gl.STATIC_DRAW)
    put(this.buf.uv, fuv, gl.STATIC_DRAW)
    put(this.buf.pos, fpos, gl.DYNAMIC_DRAW)
    put(this.buf.nrm, fnrm, gl.DYNAMIC_DRAW)

    this.M = {
      data, NV, NT, NB, center,
      dist: (radius * 1.25) / Math.tan(FOV / 2),
      fpos, fnrm,
      posed: new Float64Array(NV * 3),
      G: new Float64Array(NB * 12),
      texReady: false,
    }
    this.clock = 0

    if (data.tex) {
      const im = new Image()
      im.onload = () => {
        gl.bindTexture(gl.TEXTURE_2D, this.glTex)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, im)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
        if (this.M) this.M.texReady = true
      }
      im.src = data.tex
    }
    this.resize()
  }

  private currentAnim(): WamAnim | undefined {
    if (!this.M || !this.opts.anim) return undefined
    return this.M.data.anims.find((a) => a.name === this.opts.anim)
  }

  private animQuat(anim: WamAnim, phase: number, bi: number): Quat {
    const tr = anim.tracks[String(bi)]
    if (!tr) return IDENT
    const S = tr.length - 1
    const f = Math.min(phase, 0.99999) * S
    const i0 = Math.floor(f)
    return slerp(tr[i0], tr[Math.min(i0 + 1, S)], f - i0)
  }

  /** Accumulate each bone's world transform down the hierarchy. */
  private composeBones(rot: (i: number) => Quat) {
    const { data, NB, G } = this.M!
    for (let i = 0; i < NB; i++) {
      const b = data.bones[i]
      const R = quatMat(rot(i))
      const h = b.h
      // Rotate about the bone's own head, not the origin.
      const tx = h[0] - (R[0] * h[0] + R[1] * h[1] + R[2] * h[2])
      const ty = h[1] - (R[3] * h[0] + R[4] * h[1] + R[5] * h[2])
      const tz = h[2] - (R[6] * h[0] + R[7] * h[1] + R[8] * h[2])
      const o = i * 12
      if (b.p < 0) {
        G[o] = R[0]; G[o + 1] = R[1]; G[o + 2] = R[2]; G[o + 3] = tx
        G[o + 4] = R[3]; G[o + 5] = R[4]; G[o + 6] = R[5]; G[o + 7] = ty
        G[o + 8] = R[6]; G[o + 9] = R[7]; G[o + 10] = R[8]; G[o + 11] = tz
      } else {
        const p = b.p * 12
        for (let r = 0; r < 3; r++) {
          const pr = p + r * 4, gr = o + r * 4
          G[gr] = G[pr] * R[0] + G[pr + 1] * R[3] + G[pr + 2] * R[6]
          G[gr + 1] = G[pr] * R[1] + G[pr + 1] * R[4] + G[pr + 2] * R[7]
          G[gr + 2] = G[pr] * R[2] + G[pr + 1] * R[5] + G[pr + 2] * R[8]
          G[gr + 3] = G[pr] * tx + G[pr + 1] * ty + G[pr + 2] * tz + G[pr + 3]
        }
      }
    }
  }

  private skinVerts() {
    const { data, NV, G, posed } = this.M!
    for (let v = 0; v < NV; v++) {
      const s = data.skin[v]
      const j0 = s[0], w0 = s[1], j1 = s[2]
      const x = data.verts[v * 3], y = data.verts[v * 3 + 1], z = data.verts[v * 3 + 2]
      let o = j0 * 12
      let px = w0 * (G[o] * x + G[o + 1] * y + G[o + 2] * z + G[o + 3])
      let py = w0 * (G[o + 4] * x + G[o + 5] * y + G[o + 6] * z + G[o + 7])
      let pz = w0 * (G[o + 8] * x + G[o + 9] * y + G[o + 10] * z + G[o + 11])
      if (j1 >= 0) {
        const w1 = 1 - w0
        o = j1 * 12
        px += w1 * (G[o] * x + G[o + 1] * y + G[o + 2] * z + G[o + 3])
        py += w1 * (G[o + 4] * x + G[o + 5] * y + G[o + 6] * z + G[o + 7])
        pz += w1 * (G[o + 8] * x + G[o + 9] * y + G[o + 10] * z + G[o + 11])
      }
      posed[v * 3] = px; posed[v * 3 + 1] = py; posed[v * 3 + 2] = pz
    }
  }

  private buildMVP() {
    const M = this.M!
    const dist = M.dist * this.zoom
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw)
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch)
    const R = [cy, 0, -sy, sp * sy, cp, sp * cy, cp * sy, -sp, cp * cy]
    const f = 1 / Math.tan(FOV / 2)
    const aspect = this.canvas.width / this.canvas.height
    const near = dist * 0.1, far = dist * 4
    const A = (far + near) / (near - far), B = (2 * far * near) / (near - far)
    const c = M.center
    const rx = [R[0], R[1], R[2]], ry = [R[3], R[4], R[5]], rz = [R[6], R[7], R[8]]
    const tx = -(rx[0] * c[0] + rx[1] * c[1] + rx[2] * c[2])
    const ty = -(ry[0] * c[0] + ry[1] * c[1] + ry[2] * c[2])
    const tz = -(rz[0] * c[0] + rz[1] * c[1] + rz[2] * c[2]) - dist
    const fa = f / aspect
    const m = new Float32Array(16)
    m[0] = fa * rx[0]; m[4] = fa * rx[1]; m[8] = fa * rx[2]; m[12] = fa * tx
    m[1] = f * ry[0]; m[5] = f * ry[1]; m[9] = f * ry[2]; m[13] = f * ty
    m[2] = A * rz[0]; m[6] = A * rz[1]; m[10] = A * rz[2]; m[14] = A * tz + B
    m[3] = -rz[0]; m[7] = -rz[1]; m[11] = -rz[2]; m[15] = -tz
    const eye: [number, number, number] = [
      c[0] + dist * R[6], c[1] + dist * R[7], c[2] + dist * R[8],
    ]
    return { m, eye }
  }

  private draw() {
    const gl = this.gl
    const bg = this.opts.background
    gl.clearColor(bg[0], bg[1], bg[2], 1)
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT)
    const M = this.M
    if (!M) return

    const anim = this.currentAnim()
    if (anim) {
      const phase = anim.loop
        ? (this.clock / anim.dur) % 1
        : Math.min(this.clock / anim.dur, 1)
      this.composeBones((i) => this.animQuat(anim, phase, i))
    } else {
      this.composeBones(() => IDENT)
    }
    this.skinVerts()

    const { data, NT, posed, fpos, fnrm } = M
    // Face normals are recomputed from the posed triangle rather than skinned,
    // which keeps the faceted read correct through an animation.
    for (let t = 0; t < NT; t++) {
      const a = data.tris[t * 3], b = data.tris[t * 3 + 1], c = data.tris[t * 3 + 2]
      const ax = posed[a * 3], ay = posed[a * 3 + 1], az = posed[a * 3 + 2]
      const bx = posed[b * 3], by = posed[b * 3 + 1], bz = posed[b * 3 + 2]
      const cx = posed[c * 3], cyy = posed[c * 3 + 1], cz = posed[c * 3 + 2]
      let nx = (by - ay) * (cz - az) - (bz - az) * (cyy - ay)
      let ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az)
      let nz = (bx - ax) * (cyy - ay) - (by - ay) * (cx - ax)
      const nl = Math.hypot(nx, ny, nz) || 1
      nx /= nl; ny /= nl; nz /= nl
      const o = t * 9
      fpos[o] = ax; fpos[o + 1] = ay; fpos[o + 2] = az
      fpos[o + 3] = bx; fpos[o + 4] = by; fpos[o + 5] = bz
      fpos[o + 6] = cx; fpos[o + 7] = cyy; fpos[o + 8] = cz
      for (let k = 0; k < 3; k++) {
        fnrm[o + k * 3] = nx; fnrm[o + k * 3 + 1] = ny; fnrm[o + k * 3 + 2] = nz
      }
    }

    const { m, eye } = this.buildMVP()
    gl.useProgram(this.prog)
    gl.uniformMatrix4fv(this.loc.uMVP as WebGLUniformLocation, false, m)
    gl.uniform3fv(this.loc.uEye as WebGLUniformLocation, eye)

    const attr = (name: string, buf: WebGLBuffer, size: number, data?: Float32Array) => {
      const l = this.loc[name] as number
      gl.bindBuffer(gl.ARRAY_BUFFER, buf)
      if (data) gl.bufferSubData(gl.ARRAY_BUFFER, 0, data)
      if (l >= 0) {
        gl.enableVertexAttribArray(l)
        gl.vertexAttribPointer(l, size, gl.FLOAT, false, 0, 0)
      }
    }
    attr('aPos', this.buf.pos, 3, fpos)
    attr('aNrm', this.buf.nrm, 3, fnrm)
    attr('aCol', this.buf.col, 3)
    attr('aPBR', this.buf.pbr, 2)
    attr('aUV', this.buf.uv, 2)

    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, this.glTex)
    gl.uniform1i(this.loc.uTex as WebGLUniformLocation, 0)
    gl.uniform1f(this.loc.uUseTex as WebGLUniformLocation, M.texReady ? 1 : 0)

    gl.drawArrays(gl.TRIANGLES, 0, NT * 3)
  }

  /**
   * The loop runs whenever the model is interactive, even when the spin is
   * stopped — a drag has to redraw, and an animation has to keep playing.
   * `spinning` only gates whether the clock adds yaw on its own.
   */
  start(spin = true) {
    this.spinning = spin
    if (this.raf) return
    this.last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min((now - this.last) / 1000, 0.1)
      this.last = now
      if (!this.spinning && this.opts.resumeAfter > 0 && this.pointers.size === 0
          && now - this.idleSince > this.opts.resumeAfter) {
        this.spinning = true
      }
      if (this.spinning) this.yaw += dt * this.opts.speed
      this.clock += dt
      this.draw()
      this.raf = requestAnimationFrame(tick)
    }
    this.raf = requestAnimationFrame(tick)
  }

  /** Put the camera back where it started. */
  resetView() {
    this.yaw = 0.55
    this.pitch = this.opts.pitch
    this.zoom = this.opts.zoom
    this.interacted()
  }

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf)
    this.raf = 0
  }

  /** One frame, for a paused or reduced-motion viewer. */
  renderOnce() {
    this.draw()
  }

  setAnim(name?: string) {
    this.opts.anim = name
    this.clock = 0
  }

  dispose() {
    this.stop()
    this.detach.forEach((off) => off())
    this.detach = []
    this.ro.disconnect()
    const gl = this.gl
    Object.values(this.buf).forEach((b) => gl.deleteBuffer(b))
    gl.deleteTexture(this.glTex)
    gl.deleteProgram(this.prog)
    this.M = null
  }
}
