# Rerun upgrade & PR #48 visualization improvements — findings + plan

Investigates adopting the rerun-side improvements from
[rerun-io/examples-monorepo#48][pr48] (the efficient MAMMA rewrite). All claims
below are **measured on this machine** (RTX 4090 / `mamma` env) unless noted.

[pr48]: https://github.com/rerun-io/examples-monorepo/pull/48

## TL;DR

- The biggest single win available is **`rr.VideoStream` (H.264) logging** instead
  of the current **per-frame JPEG** logging. Today's `scene.rrd` files reach
  **~400 MB**; video-stream logging should cut that by ~10× and speed up `ma_vis`
  + the GUI viewer load.
- **The "newer rerun" is mostly already here.** The env runs **rerun-sdk 0.31.4**
  and the GUI bundles **`@rerun-io/web-viewer ^0.31.4`** — every PR #48 rerun API
  (`VideoStream`, `send_columns`, `TextDocument`, blueprint, `SegmentationImage`,
  `AnnotationContext`) is present in 0.31.4.
- **`requirements.txt` is stale and self-inconsistent**: it pins `rerun-sdk==0.23.1`
  while the shipped viewer is 0.31.4. Because `.rrd` is version-specific, a *fresh*
  `pip install -r` would install a 0.23 SDK whose recordings the bundled 0.31 viewer
  likely can't read. Fixing the pin is a correctness fix, not just an upgrade.
- **The real blocker is numpy.** rerun ≥0.24 *declares* `numpy>=2`, but sam2/sam3
  need `numpy<2`. The env sidesteps this — 0.31.4 is installed and runs fine on
  `numpy 1.26.4` (it wrote the existing 400 MB recordings) — but a clean dependency
  resolution needs a decision (force-pin / numpy-2 migration / per-step env).

## What was measured (tests done)

| test | result |
|---|---|
| installed SDK | `rerun-sdk 0.31.4`, `numpy 1.26.4` |
| `requirements.txt` pin | `rerun-sdk==0.23.1` (comment: ">=0.24 needs numpy>=2") |
| GUI viewer | `@rerun-io/web-viewer ^0.31.4` (**ahead of the SDK pin**) |
| rerun 0.31.4 numpy metadata | declares `numpy>=2` — yet imports + logs fine on `numpy 1.26.4` |
| PR #48 features in 0.31.4 | `VideoStream`, `send_columns`, `TextDocument`, `Mesh3D`, `SegmentationImage`, `AnnotationContext`, `rerun.blueprint` — **all present** |
| existing `.rrd` readability | `rerun rrd stats` (0.31.4) reads them → recordings are already 0.31-format |
| current `scene.rrd` size | **up to 401 MB** (per-frame JPEG logging) |
| current image logging | `rr.log(..., rr.EncodedImage(jpg))` per frame, per camera (`rerun_log.py:504`) |

## PR #48 rerun improvements (what they are)

1. **`rr.VideoStream` H.264 logging** (the headline): async NVENC encode → video
   stream archetype, with decode-time `resize_hw`. Replaces per-frame image logs.
2. **Blueprint default layout** (`default_blueprint(timing_doc=)`) + **`TextDocument`**
   markdown timing tables instead of per-tick scalar graphs.
3. **Mesh polish**: SMPL-X mesh with vertex normals; 512-vertex triangulated cloud
   with visibility coloring; mesh albedo alpha 1.0→0.65 so cloud/joints read through.
4. **Segmentation**: `SegmentationImage` + derived `Boxes2D`, `AnnotationContext`
   with a transparent id-0 background.

## Expected improvements & the metrics they move

| improvement | metric | expected effect |
|---|---|---|
| VideoStream vs per-frame JPEG | **`.rrd` size** | ~400 MB → tens of MB (H.264 inter-frame vs independent JPEGs) — **~10×+** |
| VideoStream (NVENC/stream encode) | **`ma_vis` wall time** | fewer per-frame encode+log calls → faster log phase |
| smaller `.rrd` | **GUI viewer load + RAM** | faster open, less browser memory; easier to share |
| blueprint + TextDocument | UX (not perf) | sensible default panels; timing as a readable table |

> These are projections from the format change; Phase 1 below measures them A/B.

## Measured: VideoStream (H.264) vs per-frame JPEG — is it worth it?

A/B on a real 4K HEVC camera video, downscaled to 1080p display, JPEG q75 (the
pipeline default) vs high-quality H.264 (libx264 crf 20). JPEG-per-frame bytes =
the current `.rrd` image payload; H.264 stream bytes = the VideoStream payload.

| frames | JPEG/cam | H264/cam | ratio | scene (4 cam) |
|---:|---:|---:|---:|---|
| 30 | 9.9 MB | 0.84 MB | 11.7× | 40 → 3.4 MB |
| 100 | 32.6 MB | 2.33 MB | 14.0× | 130 → 9.3 MB |
| 200 | 65.2 MB | 4.38 MB | 14.9× | 261 → 17.5 MB |
| 426 | 140 MB | 9.20 MB | **15.2×** | **560 → 37 MB** |

- **The win grows with sequence length** (JPEG is linear per frame; H.264 amortizes
  via inter-frame compression). Extrapolated to 32 cam × 743 frames: JPEG `.rrd`
  ≈ **~8 GB** (impractical) vs H.264 ≈ **~0.5 GB**.
- **The robust win is file size → GUI load / storage / shareability, NOT `ma_vis`
  wall time.** On CPU, x264 encode is ~30% *slower* than JPEG (1.84 s vs 1.40 s /
  426 f); it only beats JPEG with **NVENC** (what PR #48 uses).

## How PR #48 loads video, and the HEVC compatibility trap

Two ways to put video in rerun:

1. **`rr.AssetVideo`** — log the whole encoded file; the **browser viewer** decodes
   it. Tiny, but only plays if the browser can decode that codec.
2. **`rr.VideoStream`** — encode frames yourself and log H.264 samples per frame.
   **PR #48 uses this**: async **NVENC → H.264** (decode source → re-encode), so it
   never hands rerun the original file.

**Codec reality (measured):** rerun 0.31.4 *lists* `H264/H265/AV1`, but **SDK support
≠ web-viewer playback.** In-browser HEVC decode is unreliable (Safari ok; Chrome only
with OS/hw support; Firefox generally not). **This repo's example videos are HEVC,
profile `Rext`, 4K** — so loading the *original* files via `AssetVideo` would likely
**not play in the web viewer** (the "videos not compatible with rerun" symptom). The
current per-frame-JPEG path avoids this; `VideoStream` avoids it too **because it
re-encodes to H.264**. The thing to avoid is remuxing the HEVC stream straight in.

| approach | `.rrd` size | plays HEVC source in viewer? | note |
|---|---|---|---|
| current: per-frame JPEG | 15× bigger | ✅ always | simplest, huge files |
| `AssetVideo` (original HEVC) | tiny | ❌ likely not (HEVC Rext) | the trap |
| `VideoStream` re-encoded H.264 | ~15× smaller | ✅ (H.264 universal) | needs encode + overlay time-sync |

## Compatibility & impact on current users

- **This repo's current user:** already on 0.31.4 SDK + 0.31.4 viewer + 0.31-format
  recordings. Aligning the pin to 0.31.x is a **no-op for them** — it just formalizes
  the working state.
- **Fresh installs (the real risk *today*):** `pip install -r requirements.txt`
  yields a **0.23 SDK** whose `.rrd` the bundled **0.31 viewer** likely can't open.
  This is an existing latent breakage; bumping the pin **fixes** it.
- **Old `.rrd` files:** rerun never promised cross-version `.rrd` stability, so a
  version jump can make pre-existing recordings unreadable in the newer viewer.
  Mitigation: `scene.rrd` is a **regenerable pipeline output** (re-run `ma_vis`), not
  precious user data — call this out in release notes.
- **numpy:** rerun ≥0.24 declares `numpy>=2`; sam2/sam3 require `numpy<2`. Runtime
  works on 1.26.4 (proven), but `pip` strict resolution will object. Decision needed
  (next section).

## The numpy decision (the crux)

Three viable paths, smallest-blast-radius first:

- **A. Force-pin + constraint (lowest risk, matches reality).** Pin `rerun-sdk` to
  the installed 0.31.x and document that it runs on `numpy<2` despite its metadata
  (install with a constraints file / `--no-deps` for rerun). Pro: zero behavior
  change, already proven. Con: `pip` emits a resolver warning; not "clean".
- **B. Migrate the whole env to `numpy>=2`.** Test sam2/sam3/training/`ma_3d` under
  numpy 2.x (their `<2` pin may also be stale). Pro: clean resolution, future-proof.
  Con: largest blast radius — must re-validate the whole pipeline incl. training.
- **C. Per-step env for `ma_vis`.** The DAG already runs each step as its own
  subprocess with its own `conda_env`; `ma_vis` is the **only** rerun consumer.
  Give it a `numpy>=2` + rerun-current env, leave the rest on `numpy<2`. Pro: clean
  separation, no numpy conflict anywhere. Con: a second env to ship/document.

Recommendation: **A now** (it formalizes the already-working state and fixes the
fresh-install/viewer mismatch with near-zero risk), then evaluate **C** as the clean
long-term story, and **B** only if/when sam2/sam3 drop their numpy<2 requirement.

## Phase 1 design — how we adapt PR #48 to MAMMA's batch pipeline

PR #48 uses `rr.VideoStream` with **per-sample** NVENC emission because it's a *live
streaming* loop. MAMMA is **batch** (re-encode the finished clip once), so the natural
fit is **`rr.AssetVideo`** — log the whole re-encoded H.264 file once, no per-frame
access-unit extraction (no PyAV needed). Same ~15× size win, simpler code.

**Conditional re-encode (only when necessary):**

| source | action | re-encode? |
|---|---|---|
| already H.264, no trim | `rr.AssetVideo` on the original file | **No** |
| HEVC/H.265/AV1/etc. (this repo's data) or needs trim/downscale | decode → re-encode H.264 → `AssetVideo` | **Yes**, once |

- Rule of thumb: **re-encode only when the source isn't already H.264** (the one codec
  the web viewer decodes everywhere).
- **Cache** the re-encode keyed on `(source path, mtime/size, target resolution, frame
  range, crf)` — repeat `ma_vis` runs reuse it (same idea as the TensorRT engine cache).
- Whole feature is **opt-in** behind `--video-stream`; default stays per-frame JPEG.

**Time alignment (simpler than PR #48 — no streaming lag):**

- Encode with **`-bf 0` (no B-frames)** so access units map 1:1 in display order.
- Log `rr.AssetVideo(path=mp4)` once (`static`), read its per-frame PTS via
  `read_frame_timestamps_nanos()`, then emit a `rr.VideoFrameReference` for each frame
  at timeline `time = frame_id/fps` — the **identical mapping overlays already use**
  (`go_to_frame → _set_time_seconds(frame_id/fps)`). Video frame N and overlay N land
  on the same timeline instant by construction.
- PR #48's "fixed-lag" re-stamping is **not needed**: that corrected a sliding-window
  fitter emitting poses `emit_lag` ticks late; MAMMA's `ma_3d` is a batch optimizer and
  `ma_vis` reads finished fits, so there is no emit-lag.

### Phase 1 prototype — measured on real `.rrd` (200 f, 1080p, 1 cam)

A standalone prototype (re-encode → `AssetVideo` + `VideoFrameReference` on the
`"time"` timeline, vs per-frame JPEG) on a real HEVC camera clip:

| | result |
|---|---|
| conditional re-encode + cache | HEVC→H.264 200 f = 4.7 MB in 1.7 s; **2nd run = cache hit (skipped)** |
| **real `.rrd` size** | AssetVideo **4.74 MB** vs JPEG **65.34 MB** → **13.8× smaller** |
| **alignment (structural)** | 200 video PTS == 200 overlay frames; video PTS == overlay `fid/fps` exactly (`f1 = 0.0333 s = 1/30`) |

So the size win holds on the actual `.rrd` (13.8×, close to the 14.9× payload
estimate; the gap is fixed `.rrd` overhead). Alignment is correct **by
construction** — same frame count (no drops, `bf=0`) and identical frame→time
mapping. Final *visual* confirmation (probe dot glides in lock-step) is a GUI check.

### Phase 1 integration (shipped on `rerun-upgrade`) + re-encode cost

Wired `--rerun-video` / `--rerun-video-long-edge 720` / `--rerun-video-crf 20`
through `visualization/cli.py` → `pipeline.py` → `rerun_log.py`
(`log_camera_video_streams` + `_ensure_h264`). Takes precedence over
`--rerun-images`; reachable from the GUI (its entry `run_ma_vis.py` shares the
`cli.py` parser, so the flag auto-appears in the flag catalogue).

**Re-encode is *faster* than the JPEG path it replaces** (counter to the "resize
32 videos is overkill" worry). Same cameras, 720p, 4 worker threads, full clips:

| backdrop path | 4 cam | →32 cam | when |
|---|---:|---:|---|
| video re-encode (decode+resize+H.264, ffmpeg) | 3.5 s | **~28 s** | one-time, **cached** |
| current JPEG (decode+resize+per-frame encode, cv2) | 17.6 s | ~141 s | **every run** |

~5× faster at *higher* resolution (720 vs JPEG's 480). The `scale` filter rides
along in the decode→encode pass we must do anyway for HEVC, so resizing is ~free;
ffmpeg's C pipeline beats cv2's per-frame Python loop. `h264_nvenc` would make it
near-instant. Default chosen: **720p long-edge** (size cheap; decode safe ×32 —
4K×32 exceeds the browser 30 fps decode budget + likely the GPU's concurrent-decode
session cap).

### Default + the ffmpeg dependency (no install change needed)

`--rerun-video` is now **on by default** (`BooleanOptionalAction`, default True);
`--no-rerun-video` restores the legacy per-frame JPEG backdrop. The default path
needs an ffmpeg binary, and that's already covered with **no install change**:

- `imageio-ffmpeg` is **already a declared dep** (`requirements.txt`) — also pulled
  by ImageIO + moviepy. It bundles a static, cross-platform ffmpeg with **libx264**
  (verified it encodes). `_ffmpeg_bin()` prefers system ffmpeg, else this bundled
  binary, so the backdrop works after a plain `pip install -r` on any OS.
- `ffmpeg_available()` gates the default: if no ffmpeg is reachable, `ma_vis`
  **auto-falls back to the JPEG path** (a warning, never a failure).
- `ffprobe` is optional (only the "already-H.264 → skip re-encode" shortcut); absent
  ⇒ we just always re-encode.

So: declare nothing new, change nothing in INSTALL; an optional one-line doc note
("video backdrop uses the bundled ffmpeg") is the only thing worth adding.

## Phased plan

- **Phase 0 — Pin alignment (correctness fix, no feature change).** Bump the
  `requirements.txt` rerun pin to the installed 0.31.x to match the GUI viewer;
  resolve numpy via path A (documented constraint). Validate: fresh-ish env imports,
  `ma_vis` produces a `.rrd` the GUI opens. *This is the safe, high-value first step.*
- **Phase 1 — VideoStream logging (the perf win), behind a flag.** Add
  `--video-stream` to `ma_vis`; log camera imagery as H.264 `rr.VideoStream` instead
  of per-frame `EncodedImage`, with fixed-lag stamping so keypoint overlays stay
  timeline-aligned. Default off first. **Measure** `.rrd` size, `ma_vis` wall time,
  and GUI load A/B vs the JPEG path; flip default on once green.
- **Phase 2 — Blueprint default layout + `TextDocument` timing.** Ship a sensible
  default viewport; render the per-stage timings as a markdown table. UX only.
- **Phase 3 (optional) — `SegmentationImage`/`Boxes2D`/`AnnotationContext`** for the
  mask overlays, if the viz benefit justifies it.

Each phase is independently revertible and gated on the existing validation rules
(no training impact — `ma_vis` is inference/viz only).

## Cons / risks summary

- numpy `>=2` vs `<2` dependency tension (the main one) — see decision above.
- `.rrd` cross-version incompatibility for pre-existing recordings (regenerable).
- VideoStream adds an H.264 encode dependency (NVENC or CPU x264) + relies on the
  web viewer's video support; overlay/video timeline alignment needs care.
- A clean `numpy>=2` migration (path B) would touch sam2/sam3 and **training** — out
  of scope unless separately validated.

## Tests still needed (do NOT run against the working env)

- In an **isolated** env: does the full pipeline (sam2/sam3, `ma_3d`, **training**)
  run under `numpy>=2`? Decides whether path B/C is open.
- Phase 1 A/B: VideoStream vs JPEG — measured `.rrd` size, `ma_vis` wall, GUI load.
- Confirm the bundled web viewer plays a VideoStream `.rrd` in-browser.
