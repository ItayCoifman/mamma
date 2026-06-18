"""Blender headless SMPL-X exporter — a CLIENT of the SMPL-X Blender add-on.

Run via the portable Blender:
  blender --background --python scripts/blender_smplx_export.py -- \
      --addon-dir data/blender_addon --npz body.npz --out-prefix out/body \
      --formats fbx,abc,bvh,usd --fps 30 --fbx-target UNITY

We do NOT install or duplicate the add-on: we import it as a library, call
register(), and drive its operators (scene.smplx_add_gender,
object.smplx_add_animation, object.smplx_export_fbx/_alembic). BVH/USD aren't
add-on features, so those use Blender's native exporters on the add-on-built rig.

Our npz is already AMASS Y-up with the relaxed-hand mean baked in, so we import
it as anim_format=AMASS + hand_reference=FLAT.
"""
import sys
import os

import bpy


def _args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--addon-dir", required=True,
                   help="Dir containing the 'smplx_blender_addon' package (with data/*.blend).")
    p.add_argument("--npz", required=True, help="Add-on-format animation npz to import.")
    p.add_argument("--out-prefix", required=True, help="Output path prefix (no extension).")
    p.add_argument("--formats", default="fbx,abc", help="Comma list: fbx,abc,bvh,usd.")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--fbx-target", default="UNITY", choices=["UNITY", "UNREAL"])
    return p.parse_args(argv)


def _enable_addon(addon_dir):
    addon_dir = os.path.abspath(addon_dir)
    if addon_dir not in sys.path:
        sys.path.insert(0, addon_dir)
    import smplx_blender_addon
    try:
        smplx_blender_addon.register()
    except Exception as e:
        # Already-registered is fine; anything else re-raises.
        print(f"[blender_export] register note: {e}")
    return smplx_blender_addon


def _reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _select(objs, active):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        if o:
            o.select_set(True)
    bpy.context.view_layer.objects.active = active


def main():
    a = _args()
    formats = [f.strip().lower() for f in a.formats.split(",") if f.strip()]
    _reset_scene()
    _enable_addon(a.addon_dir)

    # smplx_add_animation ADDS the model AND animates it (it calls
    # scene.smplx_add_gender internally). Do NOT add a body first or you get a
    # duplicate and export the wrong, un-animated one. Set the model variant +
    # UV first (the add-model reads them), like the reference does.
    wm = bpy.context.window_manager
    wm.smplx_tool.smplx_version = "locked_head"
    wm.smplx_tool.smplx_uv = "UV_2023"
    bpy.ops.object.smplx_add_animation(
        filepath=os.path.abspath(a.npz),
        anim_format="AMASS",            # our npz is AMASS Y-up
        hand_reference="FLAT",          # relaxed-hand mean already baked in
        target_framerate=a.fps,
        keyframe_corrective_pose_weights=True,  # keyframe pose-correctives per frame
    )
    mesh = bpy.context.view_layer.objects.active
    arm = mesh.parent if mesh else None
    sc = bpy.context.scene
    print(f"[blender_export] animated body: mesh={mesh.name if mesh else None} "
          f"arm={arm.name if arm else None} frames={sc.frame_start}-{sc.frame_end}")

    out = os.path.abspath(a.out_prefix)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    written = []

    if "fbx" in formats:
        _select([mesh, arm], mesh)
        bpy.ops.object.smplx_export_fbx(filepath=out + ".fbx", target_format=a.fbx_target)
        written.append(out + ".fbx")

    if "abc" in formats:
        _select([mesh, arm], mesh)
        bpy.ops.object.smplx_export_alembic(filepath=out + ".abc")
        written.append(out + ".abc")

    if "bvh" in formats:
        # Native exporter on the armature (the add-on has no BVH op).
        _select([arm], arm)
        bpy.ops.export_anim.bvh(filepath=out + ".bvh", frame_start=bpy.context.scene.frame_start,
                                frame_end=bpy.context.scene.frame_end, root_transform_only=False)
        written.append(out + ".bvh")

    if "usd" in formats:
        _select([mesh, arm], mesh)
        bpy.ops.wm.usd_export(filepath=out + ".usd", selected_objects_only=True,
                              export_animation=True)
        written.append(out + ".usd")

    for w in written:
        ok = os.path.exists(w) and os.path.getsize(w) > 0
        print(f"[blender_export] {'OK  ' if ok else 'MISS'} {w}"
              f"{' (' + str(os.path.getsize(w)) + ' B)' if ok else ''}")
    if not all(os.path.exists(w) and os.path.getsize(w) > 0 for w in written):
        raise SystemExit("[blender_export] one or more outputs missing")


if __name__ == "__main__":
    main()
