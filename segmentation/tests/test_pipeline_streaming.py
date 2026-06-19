"""Integration tests for the issue-#14 streaming mask pipeline.

These exercise the real pipeline methods against a disk-backed MaskStore without
loading SAM/YOLO/CLIP (the segmenter is built via ``__new__`` with only the
attributes the methods touch). They lock the externally-observable contract:

  * ``save_images_from_video`` writes per-frame mask PNGs with the exact names
    and pixel content downstream ``landmarks/run_ma_2d.py`` reads, while indexing
    SAM's frame tensor one frame at a time (no whole-video copy).
  * ``_postprocess_tracklets`` merges duplicate tracklets and discards tiny ones
    with identical results when operating on the store.
"""
import os

import cv2
import matplotlib
matplotlib.use("Agg")  # headless render for the crop-summary test
import numpy as np
import torch

from core.mask_store import MaskStore
from core.pipeline import SegmentMultipleFrames


def _make_segmenter(assignment_config):
    """A SegmentMultipleFrames with just the attributes the tested methods use."""
    seg = SegmentMultipleFrames.__new__(SegmentMultipleFrames)
    seg.img_mean_sam = torch.tensor((0.485, 0.456, 0.406)).view(1, 3, 1, 1)
    seg.img_std_sam = torch.tensor((0.229, 0.224, 0.225)).view(1, 3, 1, 1)
    seg.assignment_config = assignment_config
    seg._log_info = lambda *a, **k: None
    seg._log_warn = lambda *a, **k: None
    seg._log_error = lambda *a, **k: None
    return seg


def _crop_mask_data(h=20, w=30):
    img = np.random.default_rng(0).integers(0, 255, size=(h, w, 3), dtype=np.uint8)
    mask = np.zeros((h, w), bool); mask[5:15, 8:20] = True
    bbox = np.array([8, 5, 20, 15], dtype=np.float32)
    return {0: {"img": [img], "mask": [mask], "frame": [3], "bbox": [bbox], "iou": [1.0]}}


def test_crop_summary_builds_img_bbx_regardless_of_viz(tmp_path):
    """img_bbx (feeds cross-camera matching) is built whether or not the debug
    viz PNG is rendered — and is byte-identical between the two modes."""
    seg = _make_segmenter({"exports": {}})

    d_off = _crop_mask_data(); out_off = tmp_path / "off"; out_off.mkdir()
    seg.save_picked_masks(d_off, str(out_off), render_viz=False)
    assert len(d_off[0]["img_bbx"]) == 1
    assert list(out_off.glob("*.png")) == []          # no viz written

    d_on = _crop_mask_data(); out_on = tmp_path / "on"; out_on.mkdir()
    seg.save_picked_masks(d_on, str(out_on), render_viz=True)
    assert len(d_on[0]["img_bbx"]) == 1
    assert (out_on / "person_00_crop_summary.png").exists()   # viz written

    assert np.array_equal(d_off[0]["img_bbx"][0], d_on[0]["img_bbx"][0])  # matching input identical


def test_drop_full_frames_keeps_matching_fields():
    seg = _make_segmenter({})
    d = {0: {"img": [np.zeros((4, 4, 3), np.uint8)],
             "img_bbx": [np.zeros((2, 2, 3), np.uint8)],
             "mask": [np.zeros((4, 4), bool)], "frame": [0], "bbox": [np.zeros(4)]}}
    seg._drop_full_frames(d)
    assert "img" not in d[0]                                   # full frames freed
    for fld in ("img_bbx", "mask", "frame", "bbox"):          # matching fields retained
        assert fld in d[0]


def test_debug_crop_summary_flag():
    assert _make_segmenter({"exports": {"debug_crop_summary": True}})._debug_crop_summary() is True
    assert _make_segmenter({"exports": {}})._debug_crop_summary() is False
    assert _make_segmenter({})._debug_crop_summary() is False


def test_save_images_writes_expected_mask_pngs(tmp_path):
    seg = _make_segmenter({"exports": {"skip_masked_outputs": True}})
    # bbox pruning is a no-op stub for this test (operates on sampled save_masks).
    seg.get_frames_from_far_bbx = lambda obj_ids, save_masks, fidx: save_masks

    n_frames, sam_size = 4, 32
    w_orig, h_orig = 40, 24
    inference_state = {
        "images": torch.rand(n_frames, 3, sam_size, sam_size),
        "video_width": w_orig,
        "video_height": h_orig,
    }

    store = MaskStore()
    rng = np.random.default_rng(0)
    expected = {}
    for f in range(n_frames):
        masks = {}
        for oid in (0, 1):
            m = rng.integers(0, 2, size=(h_orig, w_orig)).astype(bool)
            masks[oid] = m
            expected[(f, oid)] = m
        store.set_frame(f, masks)

    out = tmp_path / "cam"
    seg.save_images_from_video(inference_state, store, [0, 1], str(out), vis_frame_stride=2)

    # Every mask PNG exists with the exact name + content landmarks expects.
    for (f, oid), m in expected.items():
        p = out / "masks" / f"mask_{f:04d}_{oid + 1:02d}.png"
        assert p.exists(), f"missing {p}"
        got = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        assert got.shape == (h_orig, w_orig)
        assert np.array_equal(got > 0, m)

    # The store is closed/cleaned up by save_images_from_video.
    assert not os.path.isdir(store._dir)


def test_postprocess_merges_duplicates_and_discards_tiny():
    seg = _make_segmenter({"masks": {
        "merge_duplicate_tracklets": True,
        "merge_iou_threshold": 0.5,
        "discard_tiny_tracklets": True,
        "tiny_tracklet_min_area_ratio": 0.1,
        "tiny_tracklet_min_frame_ratio": 0.5,
    }})

    h, w = 10, 10
    big = np.zeros((h, w), bool); big[:8, :8] = True   # large mask, id 0
    dup = big.copy()                                   # identical -> merges into id 0
    tiny = np.zeros((h, w), bool); tiny[0, 0] = True   # 1px -> discarded

    store = MaskStore()
    for f in range(4):
        store.set_frame(f, {0: big, 1: dup, 2: tiny})

    store_out, ids = seg._postprocess_tracklets("cam", store, [0, 1, 2], image_size=(w, h))

    assert ids == [0]                       # 1 merged into 0, 2 discarded
    assert store_out.all_obj_ids() == [0]
    # The surviving id keeps a full mask on every frame.
    for f in range(4):
        assert np.array_equal(store_out.frame(f)[0], big)
    store_out.close()
