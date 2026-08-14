# Mirror target selection regression

## 2026-08-13 — manual target was cleared by a repeated source click

- **Severity:** Medium — the editor remained usable, but asymmetric mirror
  transforms lost their selected target during ordinary selection work.
- **Cause:** `selectGeometry()` reset `mirrorTarget` and `mirrorPicking` on
  every click, including a click that selected the same source part and face.
- **Expected behavior:** A repeat source click preserves the chosen target,
  its orange-red halo, and an armed target picker. Clicking the source while
  the picker is armed reports an instruction without changing either part.
- **Guard:** `tests/test_edits.py` checks the idempotent selection branch, the
  source-click picker message, and the generated viewer's English-only text.
- **Fallback:** Selecting another part deliberately starts a new mirror choice;
  `Esc` cancels only the armed target picker.

## 2026-08-13 — picker could not begin without an orange source

- **Severity:** High — an asymmetric mirror pair could not be chosen with the
  intended pipette workflow until a separate, successful source-selection
  click had already happened.
- **Cause:** The viewer used one `mirrorPicking` boolean. The picker button
  and its state were both disabled whenever a whole source geometry was not
  selected, so it could represent only “choose target”.
- **Expected behavior:** With Mirror X enabled, **Pick mirror pair** starts at
  source selection when none exists, advances to target selection after the
  source click, and preserves both choices on repeated source clicks. A
  source already selected starts directly at the target stage.
- **Guard:** `tests/test_edits.py` protects the `mirrorPickPhase` source and
  target states, the enabled-without-source picker, object-mode fallback, and
  idempotent source behavior. Manual acceptance verifies the two visible
  halos and depth-tested clicks on Steely.
- **Fallback:** The Geometry and Mirror target menus still allow precise
  source/target choice when a click is occluded; `Esc` cancels only the active
  picker stage.

## 2026-08-13 — gizmo changes were invisible and face paint was unreachable

- **Severity:** High — the transform controls changed their numbers without
  moving the model, and the previous face-colour action required a difficult
  one-face selection that could also be intercepted by an armed mirror picker.
- **Cause:** The first global-draft implementation deliberately deferred every
  gizmo transform until **Apply**, but gave no temporary mesh preview. Canvas
  clicks also had no dedicated paint-brush state, so the picker and selection
  workflows could consume the click before a colour correction was made.
- **Expected behavior:** A gizmo drag and numeric transform edit preview the
  selected part locally until **Apply** or `Esc`; no transient value is
  serialised. **Paint faces** uses the depth-tested pick buffer on left-click
  and left-drag, groups each completed stroke by geometry in the draft, and
  cancels an armed mirror picker when face tools are entered.
- **Guard:** `tests/test_edits.py` checks the transient preview boundary,
  `Esc`, Apply/Apply all export protection, the explicit paint brush, grouped
  `paint_faces` draft operations, and picker cancellation on mode changes.
- **Fallback:** **Face select** remains available for inspecting or hiding one
  face. Press `Esc` to cancel a current brush stroke or leave brush mode.
