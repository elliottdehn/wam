/** The blob `wam/viewer_export.py` emits. Field names are its field names. */
export interface WamMaterial {
  name: string
  rgb: [number, number, number]
  /** Only present when the palette entry declared it. */
  metal?: number
  rough?: number
}

export interface WamBone {
  n: string
  /** Index of the parent bone, or -1 for the root. */
  p: number
  h: [number, number, number]
}

export interface WamAnim {
  name: string
  dur: number
  loop: boolean
  /** Bone index (as a string key) to one quaternion per sample. */
  tracks: Record<string, [number, number, number, number][]>
}

export interface WamModel {
  name: string
  height: number
  verts: number[]
  tris: number[]
  triMat: number[]
  mats: WamMaterial[]
  /** Per vertex: [boneA, weightA, boneB]. weightB is 1 - weightA. */
  skin: [number, number, number][]
  bones: WamBone[]
  anims: WamAnim[]
  vcols?: number[]
  uv?: number[]
  /** data: URI for the baked texture atlas. */
  tex?: string
}
