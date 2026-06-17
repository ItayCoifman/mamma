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

## Implementation status — Phase 0 + Phase 1 DONE (validated)

- **Phase 0 (`run_ma_3d.py`)**: stamps additive, namespaced `smplx_export_*` metadata
  (`model_type`, `gender`, `num_betas`, `flat_hand_mean`) into `smplx_params_*.npz`.
  Defensive (never breaks a run) and backward-compatible (old readers ignore it).
- **Phase 1 (`optimization/export_blender.py`)**: per-person exporter → add-on npz.
  Validated on the example: **round-trip 0.000 mm**, correct add-on keys/shapes, fps
  read from ma_cap, Z-up→Y-up applied. Runs on old (pre-metadata) files via fallback.

**Corrected flat_hand_mean finding (the trap):** the prediction (neutral) model is
built by `get_smplx_models()` which **defaults `flat_hand=False`** and is called
without override in normal inference — so **inference fits use `flat_hand_mean=False`**.
The `rich`/`chi3d` → `flat_hand=True` branch in `run_ma_3d.py` is **eval-only** and
only affects the *GT* model, not the prediction. So `ma_3d` now stamps the **real**
pred-model value (`smplx_model[body_id]["neutral"].flat_hand_mean`), and the exporter
**auto-detects** flat_hand_mean by reconstructing the saved `pred_vertices` (the stamp
is only a first-try hint) — bulletproof across datasets and older files.

## Phase 2 (FBX/ABC) — investigated; approach chosen

**Pure-Python without Blender?** Assessed: **Alembic is doable pure-Python** (it's a
vertex/geometry cache — write the GPU-computed `pred_vertices` + faces via PyAlembic),
but **rigged FBX needs a real FBX writer** (Autodesk SDK) or Blender. Since the
downstream wants a true rigged FBX, we use the **official SMPL-X Blender add-on**.

**The gated zip is self-contained.** `data/smplx_blender_addon-1.0.3-20260511.zip`
(downloaded from the SMPL-X gated site) contains the add-on code **and** the
**`smplx_model_lh_20230302.blend`** (344 MB, the **locked-head** model MAMMA uses) +
hand poses + betas→joints regressors. So no separately-gated model is needed beyond it.

**`bpy` module vs Blender executable.** `bpy` *is* pip-installable for this env
(py3.11 → `bpy 4.5.x`, satisfies the add-on's `blender_version_min = 4.5.0`), which is
shipping-friendly — BUT importing `bpy` into the `mamma` env risks colliding with its
`numpy<2` pins (bpy bundles its own numpy), the exact constraint we protect.
**Decision: drive the Blender *executable* headless** (its bundled Python → zero
`mamma`-env impact; Blender 5.1.2 is present and ≥4.5). If pip-only shipping is ever
required, `bpy` belongs in a *separate* env (the DAG supports per-step `conda_env`),
never the main one.

**Export mechanism.** The add-on builds the SMPL-X rig+mesh from its `.blend` and
imports our npz onto it; export is then native:
`bpy.ops.wm.alembic_export(selected=True, packuv=False, face_sets=True)` and
`bpy.ops.export_scene.fbx(add_leaf_bones=False, bake_anim_simplify_factor=0, …)`
(add-on op idnames `object.smplx_export_alembic` / `object.smplx_export_fbx`).

**Isolation.** Load our OWN managed copy of the add-on (extracted from the downloaded
zip) under `--factory-startup` + a temp `BLENDER_USER_RESOURCES`, so it touches neither
the `mamma` env NOR the user's global Blender config — a **pre-existing legacy add-on
install conflicts** with the new extension otherwise.

**Download handling (gated, to implement).** Add `data/download_smplx_blender_addon.sh`
(SMPL-X login, mirroring `download_smplx_locked_head.sh`) for
`download.is.tue.mpg.de/download.php?domain=smplx&sfile=smplx_blender_addon-1.0.3-20260511.zip`,
extract add-on + `.blend` to a managed dir, and surface it in the GUI Pipeline-assets
panel alongside the other gated downloads.

## Locked decisions

- **Sequencing:** **NPZ first** (pure-Python exporter + round-trip validation), then
  FBX/ABC via Blender headless, then pipeline/GUI integration.
- **Up-axis:** **varies by dataset** → the exporter takes the world up-axis as input
  (from `ma_vis`'s `--up-axis`, the run config, and/or stamped `ma_3d` metadata) and
  applies the per-capture up-axis → AMASS Y-up conversion. Default Z-up if unspecified;
  verify upright in Blender.
- **Multi-person:** **per-person files** — one `<seq>_body-NN_smplx.{npz,fbx,abc}` per
  detected person (matches the add-on's one-body model). No combined scene for now.
- **Target engine:** **Generic / Blender** — the add-on's default axis + scale, no
  engine-specific (Unity/Unreal) scaling. Engine presets can be added later.

## Build order (per the decisions)

- **Phase 1 — npz exporter** (`optimization/export_blender.py` or
  `scripts/export_smplx_blender.py`): reads `smplx_params_body_id-*.npz` + ma_cap
  `global.npz` (fps) + the SMPL-X model (`pose_mean`); takes `--up-axis`; bakes the
  hand mean; converts the frame; writes per-person `<seq>_body-NN_smplx.npz`. Gate on
  the round-trip test (exported params → SMPL-X verts ≈ MAMMA `pred_vertices`).
- **Phase 2 — FBX + ABC**: `scripts/blender_export.py` via `blender --background`,
  importing the Phase-1 npz with the add-on and calling its FBX/Alembic exporters
  (generic axis/scale).
- **Phase 3 — integration**: optional `ma_export` step + a GUI "Export for Blender"
  action, once 1–2 are validated.

> Recommended small `ma_3d` edit (Phase 0, optional): stamp `flat_hand_mean`, `gender`,
> `num_betas`, `up_axis`, and `fps` into `smplx_params_*.npz` so the exporter is fully
> self-describing across datasets (esp. given the varying up-axis).
