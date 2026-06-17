# SMPL-X export to Blender-addon `.npz`, FBX, and Alembic — plan

Goal: export MAMMA's SMPL-X fit results into formats that drop straight into the
SMPL-X Blender add-on (`jtesch/smplx_blender_addon`) and onward to game/render
engines: (1) an add-on-compatible **`.npz`** (AMASS-style), (2) **FBX**, (3)
**Alembic `.abc`**. References checked against the add-on source at
`/home/ayiannakidis/Projects/smplx_blender_addon`.

## Feasibility verdict

- **`.npz` export: feasible now, pure-Python.** `ma_3d` **already saves the full
  SMPL-X parameters** (`smplx_params_body_id-NN.npz`); the add-on's import format is
  an AMASS-style npz with the **same 165-dim axis-angle pose layout**. The export is
  a format translation plus two real conversions (hand-mean, coordinate frame) — no
  re-fitting, no heavy `ma_3d` change.
- **FBX / Alembic: feasible via Blender headless + this add-on.** The add-on's FBX
  and Alembic exporters are Blender operators (`bpy.ops.export_scene.fbx`,
  `bpy.ops.wm.alembic_export`). So the path is: write the `.npz` → drive
  `blender --background --python …` to import it with the add-on → call the add-on's
  export. Adds an **optional Blender dependency** (only for FBX/ABC, not the npz).

## What MAMMA has vs. what the add-on wants

MAMMA `ma_3d` writes (`optimization/run_ma_3d.py`), per person:
`smplx_params_body_id-NN.npz` → `smplx_pose (F,165)`, `smplx_betas (1,10)`,
`smplx_translation (F,3)` (world frame, metres); plus `verts_joints_*` (verts/joints).

Add-on import (`operators/animation.py:87-97`) requires an npz with:
`poses (F,165)`, `betas (num_betas,)`, `trans (F,3)`, `gender` (str),
`mocap_frame_rate`/`mocap_framerate` (int).

**Pose layout matches exactly** (both `[0:3]`go `[3:66]`body `[66:69]`jaw
`[69:75]`eyes `[75:120]`Lhand `[120:165]`Rhand, full axis-angle, `use_pca=False`):

| add-on key | from MAMMA | conversion |
|---|---|---|
| `poses` | `smplx_pose` | reorder: none (layout identical); **bake hand-mean** (below); **rotate global_orient** (below) |
| `trans` | `smplx_translation` | **coordinate-frame rotate** (below) |
| `betas` | `smplx_betas` | squeeze `(1,10)→(10,)`; add-on applies to `Shape000..009` |
| `gender` | constant `'neutral'` | MAMMA fits neutral |
| `mocap_frame_rate` | ma_cap `global.npz` `fps` | **read from ma_cap** (not in ma_3d output) |

## The tricky conversions (the crux)

1. **Hand mean / `flat_hand_mean`.** MAMMA fits `use_pca=False` with
   `flat_hand_mean=True` for lockhead (default) and `False` for rich/chi3d. With
   `flat_hand_mean=False` the SMPL-X forward adds a non-zero `pose_mean` to the hand
   pose; the stored hand angles are *relative* to that mean. The add-on supports a
   FLAT mode (use angles as-is) and a RELAXED mode (it adds its own mean to fingers).
   **Cleanest, convention-proof approach: bake the model's `pose_mean` into the
   exported hand angles** so they are absolute (`flat_hand_mean=True`-equivalent),
   then import as **FLAT**. Since `pose_mean` is 0 when `flat_hand_mean=True`, the
   same code path works for both datasets. (`pose_mean` = `left_hand_mean`/
   `right_hand_mean` from the SMPL-X model file, or `model.pose_mean`.)

2. **Global orientation + translation — coordinate frame.** MAMMA's world frame is
   the camera rig's, typically **Z-up** (`ma_vis --up-axis` default `z`), with **no**
   up-axis transform applied. The add-on expects **AMASS = OpenGL Y-up** and itself
   applies a fixed **−90° X** to the root on import to reach Blender Z-up. So the
   export must convert MAMMA(Z-up) → AMASS(Y-up): rotate `global_orient` (compose as
   rotation matrices then back to axis-angle) and `trans` by `R = ±90° about X`.
   **Sign must be verified empirically** (body stands upright, faces forward) on a
   reference frame. Handle non-Z-up captures by reading the actual up-axis.

3. **FPS.** Not in `ma_3d` output — read `fps` from ma_cap's `global.npz` and write
   `mocap_frame_rate`. The add-on validates `mocap_fps ≥ target_fps` and sub-samples,
   so the real value must be present (don't hardcode 30).

4. **betas / expression / gender.** Squeeze betas to `(10,)`; the add-on's SMPL-X
   `.blend` has 300 shape keys but applies whatever length is given. Expression is
   **not optimized in MAMMA** and **not part of the add-on's animation npz** → ignore.
   Gender `'neutral'`. (Validate the 10-beta basis matches the add-on's SMPL-X model
   version via the round-trip below.)

5. **Multi-person.** One `smplx_params_body_id-NN.npz` per person → one exported npz
   (and one FBX/ABC) per person. Decide naming + whether to also offer a combined scene.

## Does `ma_3d` need editing?

**For the npz: essentially no** — params are already saved. **Recommended small,
optional edit:** have `ma_3d` stamp the **model config into its output npz** so the
exporter is self-describing and unambiguous — add `flat_hand_mean`, `gender`,
`model_type='smplx'`, `num_betas`, and `fps` (threaded from ma_cap). Without it the
exporter must assume defaults (lockhead → `flat_hand_mean=True`) and read fps from
ma_cap separately — workable, but the metadata makes it robust across datasets.

## Architecture

- **Phase-1 npz exporter** — a standalone, dependency-light module/CLI, e.g.
  `optimization/export_blender.py` (or `scripts/export_smplx_blender.py`): reads
  `smplx_params_body_id-*.npz` (+ ma_cap `global.npz` for fps, + the SMPL-X model for
  `pose_mean`), applies the conversions, writes `<seq>_body-NN_smplx.npz`. Reuses
  MAMMA's existing SMPL-X model loader (`optimization/utils_smplx.py`) so the
  `pose_mean`/`flat_hand_mean` come from the exact model that was fit.
- **Phase-2 FBX/ABC** — `scripts/blender_export.py` run via `blender --background
  --python`: enables the add-on, adds the SMPL-X body, imports the Phase-1 npz, then
  calls the add-on's FBX (`export_scene.fbx`, `add_leaf_bones=False`,
  `bake_anim_simplify_factor=0`) and Alembic (`wm.alembic_export`) exporters. Target
  presets (Unity/Unreal) drive the add-on's scale/axis handling.
- **Phase-3 integration** — expose as a pipeline step / GUI action ("Export for
  Blender") once Phases 1–2 are validated.

## Validation

- **Round-trip (the key correctness test):** feed the exported `poses`+`betas`+`trans`
  back through SMPL-X (matching `flat_hand_mean`) and compare the resulting vertices
  to MAMMA's saved `pred_vertices` — should match to ~mm. This proves the pose layout,
  the hand-mean baking, and the betas basis in one shot.
- **Coordinate frame:** import one sequence into Blender, confirm the body is upright,
  on the ground plane, facing the expected direction (verifies the ±90° X sign).
- **FPS / timing:** animation duration = frames / fps.
- **Multi-person:** N people → N upright, correctly-placed armatures.

## Open decisions (need your input)

- **Up-axis of your captures** — always Z-up (the `--up-axis z` default), or does it
  vary per dataset/calibration? Drives conversion #2.
- **Per-person files vs. one combined export** — separate `.npz`/FBX/ABC per person
  (simplest, matches the add-on's one-body model), or a combined scene too?
- **Target engine** — Unity, Unreal, or generic? The add-on bakes engine-specific
  scale/axis into FBX/ABC (e.g. ×100 for Unreal).
- **Where it lives** — a new pipeline step (`ma_export`) + GUI button, or a standalone
  CLI script first.
