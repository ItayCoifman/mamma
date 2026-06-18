# Exporting SMPL-X results (npz / FBX / Alembic / BVH / USD)

MAMMA's `ma_3d` fit can be exported into formats that drop straight into the
**SMPL-X Blender add-on** and on into game/render engines:

| format | what it is | needs Blender? |
|---|---|---|
| **`.npz`** | the SMPL-X Blender add-on's native animation file (AMASS-style) | **No** |
| **`.abc`** (Alembic) | animated **vertex/geometry cache** — render engines (Houdini, Maya, UE geometry cache) | yes |
| **`.fbx`** | **rigged** skeleton + skinned mesh + animation — game engines (Unity, Unreal) | yes |
| **`.bvh`** | skeleton-only motion (mocap retargeting) | yes |
| **`.usd`** | USD scene (UE5/Unity/Houdini import natively) | yes |

One person → one set of files (`<seq>_body-NN_smplx.npz`, `<seq>_body-NN.fbx`, …).

## Quick start — npz only (no setup)

The npz is pure-Python; it needs nothing beyond the `mamma` env:

```bash
python optimization/export_blender.py \
  --ma-3d-dir output/ma_3d/<tag>/<capture> \
  --seq-name  <sequence> \
  --ma-cap-dir output/ma_cap/<tag>/<capture> \
  --out-dir   output/export/<sequence> \
  --formats   npz
```

It **validates correctness**: it reconstructs MAMMA's saved vertices from the
exported parameters and refuses to write if they don't match (so a subtly wrong
file can't slip through). Import the `.npz` via the add-on's *Add Animation*.

## FBX / Alembic / BVH / USD — one-time setup

These build a rig in Blender, so they need a Blender runtime + the add-on. Both
are fetched with the two scripts below (or via the GUI **Exporter** tab):

```bash
# 1) Portable Blender 4.5 LTS (no install/root; isolated from the mamma env)
bash data/download_blender.sh

# 2) SMPL-X Blender add-on (gated; SMPL-X account at https://smpl-x.is.tue.mpg.de/)
#    Self-contained: add-on code + the locked-head .blend model.
bash data/download_smplx_blender_addon.sh
```

`download_blender.sh` lands a portable Blender in `data/blender/`; the add-on
extracts to `data/blender_addon/`. The exporter auto-detects both. (You can point
at a different Blender with `MAMMA_BLENDER_BIN=/path/to/blender`.)

## Export all formats

```bash
python optimization/export_blender.py \
  --ma-3d-dir output/ma_3d/<tag>/<capture> \
  --seq-name  <sequence> \
  --ma-cap-dir output/ma_cap/<tag>/<capture> \
  --out-dir   output/export/<sequence> \
  --formats   npz,fbx,abc,bvh,usd \
  --fbx-target UNITY        # or UNREAL (the add-on's only FBX presets)
```

If Blender or the add-on isn't present, the exporter **falls back to npz-only**
with a warning — it never fails the run for a missing optional dependency.

## Options

| flag | meaning |
|---|---|
| `--formats` | comma list of `npz,fbx,abc,bvh,usd` (default `npz`) |
| `--coord-system` | `keep` (default) exports the fit **untouched** — original up-axis AND floor (respects each capture's own system). `blender` is an optional extra-fix: rotate the detected up onto Blender's **+Z** and drop the feet to the floor (Z=0) |
| `--up-axis` | only for `--coord-system blender`: the source up to rotate to +Z (`auto` default detects it via foot-plane + body-vertical; or force `x`/`y`/`z`) |
| `--fps` | override `mocap_frame_rate` (else read from ma_cap's `global.npz`, else 30) |
| `--fbx-target` | `UNITY` (≈ generic, default) or `UNREAL` (×100 scale) |
| `--smplx-models` | SMPL-X model folder (default `data/body_models/smplx_locked_head`) |
| `--blender-bin` | explicit Blender binary (else `MAMMA_BLENDER_BIN` / `data/blender/` / `PATH`) |
| `--addon-dir` | add-on folder (default `data/blender_addon`) |
| `--no-validate` | skip the round-trip vertex check (not recommended) |

## How the conventions are handled (the tricky bits)

- **Hand pose / `flat_hand_mean`** — the fit's hand convention is **auto-detected**
  from the saved vertices (normal inference is `flat_hand_mean=False`); the relaxed
  mean is baked into absolute hand angles so the add-on imports them as **FLAT**.
- **Coordinate system** — by default the fit is exported **untouched**: your
  capture's own up-axis and floor are respected (MAMMA's data is Z-up, which the
  add-on's AMASS import reproduces faithfully). `--coord-system blender` is an
  optional fix that rotates the auto-detected up onto Blender's **+Z** and grounds
  the feet (with the root-pivot correction `(R−I)·J0` so the body sits correctly).
- **FPS** — taken from ma_cap so animation timing is right.

## Viewing in Blender

Open Blender (the add-on installed normally), use **Add Animation** on the `.npz`,
or import the `.fbx`/`.abc`/`.usd` directly. FBX with `--fbx-target UNREAL` is
pre-scaled (×100) for Unreal; `UNITY` keeps Blender units.

> **Tip:** the `.npz` is the most faithful (it's the add-on's own format and is
> round-trip-validated). FBX/ABC/etc. are derived from it through Blender.
