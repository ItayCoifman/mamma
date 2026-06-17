"""Export MAMMA SMPL-X fits to the SMPL-X Blender add-on ``.npz`` format.

Per person, reads ``ma_3d``'s ``smplx_params_body_id-NN.npz`` and writes an
AMASS-style npz (``poses`` / ``betas`` / ``trans`` / ``gender`` /
``mocap_frame_rate``) that the ``smplx_blender_addon`` imports. Pure-Python
(numpy + the ``smplx`` model, for the relaxed-hand mean) — no Blender needed.

Conversions (see docs/smplx-export-plan.md):
  * pose layout is already identical (165 axis-angle), so it copies straight over;
  * the relaxed-hand mean is BAKED into the hand angles so the result is absolute
    (import as the add-on's FLAT mode), which is convention-proof: the mean is
    zero when the fit used ``flat_hand_mean=True``;
  * ``global_orient`` + ``trans`` are rotated from the capture's world up-axis to
    the add-on's AMASS Y-up convention;
  * ``mocap_frame_rate`` comes from ma_cap's ``global.npz`` (or ``--fps``).

Self-describing where possible: reads ``smplx_export_*`` metadata stamped by
``run_ma_3d`` and falls back to safe defaults for older outputs.
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import re
from typing import Optional

import numpy as np

log = logging.getLogger("export_blender")

# Pose-vector slices (SMPL-X, 165 axis-angle) — identical in MAMMA and the add-on.
_GLOBAL = slice(0, 3)
_LHAND = slice(75, 120)
_RHAND = slice(120, 165)


def _up_axis_rotation(up_axis: str) -> np.ndarray:
    """3x3 rotation mapping the capture world (``up_axis`` is +up) to AMASS Y-up.

    The add-on then applies its own fixed -90deg X on import to reach Blender
    Z-up, so the on-disk file must be in AMASS (OpenGL) Y-up. The exact sign is
    verified visually in Blender; flip via ``--flip-forward`` if a body faces
    backward.
    """
    a = up_axis.lower()
    if a == "y":
        return np.eye(3)
    if a == "z":  # +Z -> +Y  (Rx(-90))
        return np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    if a == "x":  # +X -> +Y  (Rz(+90))
        return np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=np.float64)
    raise ValueError(f"up_axis must be x/y/z, got {up_axis!r}")


def _rotvec_to_mat(rotvecs: np.ndarray) -> np.ndarray:
    """(N,3) axis-angle -> (N,3,3) rotation matrices."""
    try:
        from scipy.spatial.transform import Rotation
        return Rotation.from_rotvec(rotvecs).as_matrix()
    except Exception:  # pragma: no cover - scipy fallback
        import cv2
        return np.stack([cv2.Rodrigues(r.astype(np.float64))[0] for r in rotvecs])


def _mat_to_rotvec(mats: np.ndarray) -> np.ndarray:
    """(N,3,3) rotation matrices -> (N,3) axis-angle."""
    try:
        from scipy.spatial.transform import Rotation
        return Rotation.from_matrix(mats).as_rotvec()
    except Exception:  # pragma: no cover
        import cv2
        return np.stack([cv2.Rodrigues(m.astype(np.float64))[0].ravel() for m in mats])


def _scalar(data, key, default):
    """Read a 0-d scalar (str/bool/int) from an npz, with a default if absent."""
    if key not in getattr(data, "files", []):
        return default
    v = data[key]
    try:
        return v.item()
    except Exception:
        return default


def _resolve_fps(ma_cap_dir: Optional[str], seq_name: str, fps_override: Optional[int]) -> int:
    if fps_override:
        return int(fps_override)
    if ma_cap_dir:
        g = os.path.join(ma_cap_dir, seq_name, "gt", "global.npz")
        if os.path.exists(g):
            try:
                with np.load(g, allow_pickle=True) as gd:
                    if "fps" in gd.files:
                        return int(np.asarray(gd["fps"]).item())
            except Exception as e:
                log.warning("could not read fps from %s: %s", g, e)
    log.warning("fps not found (no --fps and no ma_cap global.npz); defaulting to 30")
    return 30


def _build_model(model_dir, gender, num_betas, flat_hand_mean, batch_size):
    import smplx
    return smplx.create(
        model_dir, model_type="smplx", gender=gender, ext="npz",
        flat_hand_mean=bool(flat_hand_mean), num_betas=int(num_betas),
        use_pca=False, num_pca_comps=45, batch_size=int(batch_size),
    )


def _reconstruct_verts(model, pose, betas_row, trans):
    """Run SMPL-X forward on the stored pose split into its parts (the way MAMMA
    did), to compare against the saved ``pred_vertices``."""
    import torch
    F = pose.shape[0]
    betas_t = torch.tensor(np.tile(betas_row, (F, 1)), dtype=torch.float32)
    with torch.no_grad():
        return model(
            return_verts=True, betas=betas_t,
            global_orient=torch.tensor(pose[:, 0:3], dtype=torch.float32),
            body_pose=torch.tensor(pose[:, 3:66], dtype=torch.float32),
            jaw_pose=torch.tensor(pose[:, 66:69], dtype=torch.float32),
            leye_pose=torch.tensor(pose[:, 69:72], dtype=torch.float32),
            reye_pose=torch.tensor(pose[:, 72:75], dtype=torch.float32),
            left_hand_pose=torch.tensor(pose[:, 75:120], dtype=torch.float32),
            right_hand_pose=torch.tensor(pose[:, 120:165], dtype=torch.float32),
            transl=torch.tensor(trans, dtype=torch.float32),
        ).vertices.cpu().numpy()


def export_person(params_path, model_dir, up_axis, fps, out_path,
                  verts_path=None, validate=True, validate_tol_mm=2.0):
    """Convert one ``smplx_params_body_id-NN.npz`` to an add-on npz at ``out_path``."""
    with np.load(params_path, allow_pickle=True) as d:
        pose = np.asarray(d["smplx_pose"], dtype=np.float64)          # (F,165)
        betas = np.asarray(d["smplx_betas"], dtype=np.float64)        # (1,nb)
        trans = np.asarray(d["smplx_translation"], dtype=np.float64)  # (F,3)
        model_type = _scalar(d, "smplx_export_model_type", "smplx")
        gender = _scalar(d, "smplx_export_gender", "neutral")
        flat_hint = bool(_scalar(d, "smplx_export_flat_hand_mean", False))
        num_betas = int(_scalar(d, "smplx_export_num_betas", betas.shape[-1]))

    if model_type != "smplx":
        raise ValueError(f"{params_path}: unsupported model_type {model_type!r}")
    F = pose.shape[0]
    betas_row = betas.reshape(-1)[:num_betas]

    ref = None
    if validate and verts_path and os.path.exists(verts_path):
        with np.load(verts_path, allow_pickle=True) as vd:
            ref = np.asarray(vd["pred_vertices"], dtype=np.float64)

    # Determine flat_hand_mean. The fit's convention is whatever reconstructs the
    # saved vertices — so when they're available we AUTO-DETECT it (the stamped
    # metadata is only a first-try hint). This is bulletproof across datasets,
    # eval-vs-inference differences, and older files that predate the metadata.
    chosen_model, chosen_flat, chosen_mm = None, None, None
    tried = []
    for flat in ([flat_hint, not flat_hint] if ref is not None else [flat_hint]):
        if flat in tried:
            continue
        tried.append(flat)
        m = _build_model(model_dir, gender, num_betas, flat, F)
        if ref is None:
            chosen_model, chosen_flat = m, flat
            log.info("%s: F=%d nb=%d gender=%s; no verts to validate -> "
                     "flat_hand_mean=%s (hint)", os.path.basename(params_path),
                     F, num_betas, gender, flat)
            break
        mm = float(np.abs(_reconstruct_verts(m, pose, betas_row, trans) - ref).max() * 1000.0)
        log.info("%s: flat_hand_mean=%s -> round-trip max %.3f mm",
                 os.path.basename(params_path), flat, mm)
        if mm <= validate_tol_mm:
            chosen_model, chosen_flat, chosen_mm = m, flat, mm
            break
    if chosen_model is None:
        raise RuntimeError(
            f"{params_path}: neither flat_hand_mean reconstructs the saved vertices "
            f"within {validate_tol_mm} mm (tried {tried}) — model config likely wrong; "
            "refusing to export")
    if chosen_mm is not None:
        log.info("  detected flat_hand_mean=%s (round-trip %.3f mm)", chosen_flat, chosen_mm)

    # --- bake the relaxed-hand mean -> absolute (add-on FLAT mode) ---
    # model.left/right_hand_mean is the relaxed mean (0 when flat_hand_mean=True),
    # exactly what the SMPL-X forward adds internally, so this yields the absolute
    # hand pose for the detected convention.
    lhm = chosen_model.left_hand_mean.detach().cpu().numpy().reshape(-1)
    rhm = chosen_model.right_hand_mean.detach().cpu().numpy().reshape(-1)
    poses = pose.copy()
    poses[:, _LHAND] += lhm
    poses[:, _RHAND] += rhm

    # --- rotate global_orient + trans: capture up-axis -> AMASS Y-up ---
    # global_orient rotates the body about the ROOT joint, so a whole-body rotation
    # R needs translation offset (R - I) @ J0, where J0 is the (pose-independent,
    # betas-dependent) rest pelvis. Omitting it rotates about the wrong pivot — a
    # fixed ~tens-of-cm error.
    import torch
    with torch.no_grad():
        J0 = chosen_model(
            betas=torch.tensor(np.tile(betas_row, (F, 1)), dtype=torch.float32)
        ).joints[0, 0].cpu().numpy().astype(np.float64)
    R = _up_axis_rotation(up_axis)
    go_mat = _rotvec_to_mat(poses[:, _GLOBAL])             # (F,3,3)
    poses[:, _GLOBAL] = _mat_to_rotvec(R[None] @ go_mat)   # R . global
    trans = trans @ R.T + (R - np.eye(3)) @ J0            # R . (J0 + t) - J0

    # --- validate the FINAL export end-to-end (bake + rotation together) ---
    # Run the exported (absolute) poses through a FLAT model; the result must equal
    # the MAMMA vertices rotated by R. Catches bake/rotation/pivot bugs that the
    # input-param round-trip above cannot see.
    if ref is not None:
        flat_model = chosen_model if chosen_flat else _build_model(model_dir, gender, num_betas, True, F)
        v_out = _reconstruct_verts(flat_model, poses, betas_row, trans)
        out_mm = float(np.abs(v_out - ref @ R.T).max() * 1000.0)
        if out_mm > validate_tol_mm:
            raise RuntimeError(
                f"{params_path}: exported npz does not reproduce R@verts "
                f"({out_mm:.3f} mm > {validate_tol_mm} mm) — bake/rotation bug; refusing to export")
        log.info("  export round-trip OK: %.3f mm", out_mm)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    np.savez(
        out_path,
        poses=poses.astype(np.float32),
        betas=betas_row.astype(np.float32),
        trans=trans.astype(np.float32),
        gender=str(gender),
        mocap_frame_rate=int(fps),
        surface_model_type="smplx",
    )
    log.info("  wrote %s (poses %s, fps %d, up_axis %s)", out_path, poses.shape, fps, up_axis)
    return out_path


def export_sequence(ma_3d_dir, seq_name, model_dir, out_dir, up_axis="z",
                    ma_cap_dir=None, fps=None, validate=True):
    seq_dir = os.path.join(ma_3d_dir, seq_name)
    params = sorted(glob.glob(os.path.join(seq_dir, "smplx_params_body_id-*.npz")))
    if not params:
        raise FileNotFoundError(f"no smplx_params_body_id-*.npz under {seq_dir}")
    fps = _resolve_fps(ma_cap_dir, seq_name, fps)
    out = []
    for p in params:
        m = re.search(r"body_id-(\d+)", os.path.basename(p))
        bid = m.group(1) if m else "00"
        verts = os.path.join(seq_dir, f"verts_joints_body_id-{bid}.npz")
        out_path = os.path.join(out_dir, f"{seq_name}_body-{bid}_smplx.npz")
        out.append(export_person(p, model_dir, up_axis, fps, out_path,
                                 verts_path=verts, validate=validate))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ma-3d-dir", "--ma_3d_dir", required=True,
                    help="ma_3d output root (contains <seq>/smplx_params_body_id-*.npz).")
    ap.add_argument("--seq-name", "--seq_name", required=True)
    ap.add_argument("--out-dir", "--out_dir", required=True)
    ap.add_argument("--smplx-models", "--smplx_models",
                    default="data/body_models/smplx_locked_head",
                    help="Folder containing smplx/SMPLX_NEUTRAL.npz (for the hand mean).")
    ap.add_argument("--up-axis", "--up_axis", default="z", choices=["x", "y", "z"],
                    help="World up-axis of the capture (optional override). Default z.")
    ap.add_argument("--ma-cap-dir", "--ma_cap_dir", default=None,
                    help="ma_cap output root, to read fps from <seq>/gt/global.npz.")
    ap.add_argument("--fps", type=int, default=None,
                    help="Override mocap_frame_rate (else read from ma_cap, else 30).")
    ap.add_argument("--no-validate", action="store_true",
                    help="Skip the round-trip vertex check (not recommended).")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose == 0 else logging.DEBUG,
                        format="%(levelname)s %(name)s: %(message)s")

    outs = export_sequence(
        args.ma_3d_dir, args.seq_name, args.smplx_models, args.out_dir,
        up_axis=args.up_axis, ma_cap_dir=args.ma_cap_dir, fps=args.fps,
        validate=not args.no_validate,
    )
    print(f"exported {len(outs)} file(s):")
    for o in outs:
        print(f"  {o}")


if __name__ == "__main__":
    main()
