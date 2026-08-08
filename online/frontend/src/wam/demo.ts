/**
 * NB: no `\` line continuations in here. Inside a TS template literal a
 * trailing backslash is a *JavaScript* line continuation and is eaten before
 * the WAM compiler sees it, so the string the browser compiles stops matching
 * the file on disk. Keep every directive on one line.
 *
 * A small model for the landing page turntable — deliberately written with
 * `dir=`/`pitch=` only, so it compiles against the compiler on this branch.
 * (Chain-relative `curl=`/`swing=` landed on `runescape` and is not merged.)
 */
export const DEMO_WAM = `model sentinel
  height 2.4
  style chunky

palette
  hide  #2c3336 rough=0.8
  plate #6f7a5e rough=0.7
  gold  #c8a24a metal=1.0 rough=0.2
  eye   #e2531b rough=0.25

skeleton
  root pelvis at 0.50
  bone spine parent=pelvis dir=up len=0.16
  bone chest parent=spine dir=up len=0.14
  bone neck  parent=chest dir=up len=0.05
  bone head  parent=neck  dir=up len=0.12
  mirror
    bone clavicle parent=chest at=0.85 dir=side tilt=-16 len=0.19
    bone upperarm parent=clavicle dir=down pitch=6 tilt=5 len=0.19
    bone forearm  parent=upperarm dir=down pitch=-8 len=0.17
    bone thigh parent=pelvis side=0.08 dir=down tilt=4 pitch=-7 len=0.24
    bone shin  parent=thigh dir=down pitch=9 len=0.22
    bone foot  parent=shin dir=fwd len=0.10
  end

parts
  loft torso bones=pelvis..neck material=hide material_arc=plate:55-125
    ring 0.00 w=0.25 d=0.20
    ring 0.42 w=0.23 d=0.19
    ring 0.80 w=0.32 d=0.21
    ring 1.00 w=0.15 d=0.15
    cap start=dome end=dome

  loft skull bones=head..head material=hide
    ring 0.00 w=0.15 d=0.15
    ring 0.45 w=0.18 d=0.18
    ring 1.00 w=0.12 d=0.12
    cap start=dome end=dome

  # No on= here, deliberately. on= snaps the origin to the nearest surface
  # point and then aims the part along that surface's normal — from a point on
  # the bone axis (i.e. inside the skull) it picked a side facet and stood the
  # crest up 21 degrees off vertical and 0.018 off centre. Starting the free ray
  # inside the head instead makes it flush and exactly vertical by construction.
  loft crest bone=head at=0.72 dir=up len=0.13 material=gold
    ring 0.00 w=0.04 d=0.16
    ring 1.00 w=0.01 d=0.05 tip
    cap start=flat end=point

  mirror
    attach eyeb bone=head kind=eye at=0.45 offset=(0.055,0.01,0.075) on=skull size=0.034 material=eye

    loft arm bones=clavicle..forearm material=hide
      ring 0.00 w=0.15 d=0.15
      ring 0.22 w=0.13 d=0.13
      ring 0.55 w=0.09 d=0.09
      ring 1.00 w=0.07 d=0.07
      cap start=dome end=dome

    loft pauldron bone=clavicle at=0.80 dir=side len=0.09 material=plate
      ring 0.00 w=0.20 d=0.20
      ring 1.00 w=0.13 d=0.13
      cap start=dome end=flat

    loft leg bones=thigh..shin material=hide
      ring 0.00 w=0.17 d=0.17
      ring 0.55 w=0.12 d=0.12
      ring 1.00 w=0.09 d=0.09
      cap start=dome end=dome

    loft boot bones=foot..foot material=plate frame=up shape=squarish
      ring 0.00 w=0.11 dtop=0.10 dbot=0.10
      ring 1.00 w=0.12 dtop=0.07 dbot=0.09
      cap start=dome end=flat
  end

animations
  anim idle loop dur=3.0
    ch spine pitch 0%=0 50%=-5 100%=0
    ch chest pitch 0%=0 55%=4 100%=0
    ch head  yaw   0%=-9 40%=10 100%=-9
    ch upperarm.l tilt 0%=0 50%=9 100%=0
    ch upperarm.r tilt 0%=0 50%=-9 100%=0

checks
  # The crest stands straight up on the midline. Both read 0.018 when on= was
  # aiming it, so these move with the defect rather than merely passing.
  assert abs(x(crest)) < 0.005
  assert abs(z(crest)) < 0.005
  assert top(crest) > top(skull)
  assert bottom(boot.l) in -0.01..0.02
  assert tris < 1200
  # The crest is rooted inside the skull on purpose — that is what makes it
  # flush. Everything else still has to keep its distance.
  noclip except=crest+skull
`
