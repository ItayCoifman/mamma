"""Phase-1 prototype: AssetVideo (re-encoded H.264) vs per-frame JPEG, on a real
camera video. Proves (a) real .rrd size delta and (b) video/overlay time-alignment
on the SAME 'time' timeline the pipeline uses. Throwaway — does not touch the repo."""
import cv2, os, subprocess, shutil, hashlib, time, sys
import numpy as np
import rerun as rr

VIDEO = "data/mamma_example/pushing_and_lifting_from_ground/videos/D001.mp4"
FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
FPS = 30.0
LONG_EDGE = 1920
JPEG_Q = 75
CRF = 20
N = 200
CACHE = "/tmp/_vproto_cache"
os.makedirs(CACHE, exist_ok=True)


def source_codec(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name", "-of",
                        "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    return r.stdout.strip()


def reencode_if_needed(path, w, h, n, crf):
    """Conditional re-encode: only when the source isn't already H.264. Cached."""
    codec = source_codec(path)
    if codec == "h264":
        print(f"[reencode] source already h264 -> would AssetVideo the original (no re-encode)")
        # for the prototype we still need a downscaled/trimmed copy; real code would
        # skip when no trim/resize is needed.
    key = hashlib.md5(f"{path}:{os.path.getmtime(path)}:{w}x{h}:{n}:{crf}".encode()).hexdigest()[:12]
    out = os.path.join(CACHE, f"{key}.mp4")
    if os.path.exists(out):
        print(f"[reencode] cache hit {out} ({os.path.getsize(out)/1e6:.1f} MB) — skipped")
        return out
    t = time.time()
    # bf=0: no B-frames -> access units map 1:1 in display order (clean alignment).
    subprocess.run([FFMPEG, "-y", "-i", path, "-frames:v", str(n),
                    "-vf", f"scale={w}:{h}", "-c:v", "libx264", "-preset", "medium",
                    "-crf", str(crf), "-bf", "0", "-pix_fmt", "yuv420p", out],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    print(f"[reencode] {codec}->h264 {w}x{h} n={n}: {os.path.getsize(out)/1e6:.1f} MB in {time.time()-t:.1f}s")
    return out


# target display dims (even)
cap = cv2.VideoCapture(VIDEO)
W0, H0 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
s = LONG_EDGE / max(W0, H0)
W, H = (int(W0 * s) // 2) * 2, (int(H0 * s) // 2) * 2

# ---- A) AssetVideo path ----
mp4 = reencode_if_needed(VIDEO, W, H, N, CRF)
rrd_video = "/tmp/_proto_video.rrd"
rr.init("proto_video")
rr.save(rrd_video)
video = rr.AssetVideo(path=mp4)
rr.log("cam/video", video, static=True)
ts_ns = np.asarray(video.read_frame_timestamps_nanos())
n_vid = len(ts_ns)
secs = ts_ns * 1e-9  # video frame N presentation time (== N/fps for cfr)
# Pin each video frame onto the 'time' timeline at the SAME mapping overlays use.
rr.send_columns(
    "cam/video",
    indexes=[rr.TimeColumn("time", timestamp=secs)],
    columns=rr.VideoFrameReference.columns_nanos(ts_ns),
)
# Moving probe overlay (left->right) stamped per-frame the SAME way go_to_frame does,
# so in the GUI it should glide in lock-step with the video if aligned.
for fid in range(n_vid):
    rr.set_time("time", timestamp=fid / FPS)
    x = (fid / max(1, n_vid - 1)) * W
    rr.log("cam/video/probe", rr.Points2D([[x, H / 2]], radii=12, colors=[255, 0, 0]))
rr.disconnect()
sz_video = os.path.getsize(rrd_video)

# ---- B) per-frame JPEG path (current approach), same frames ----
rrd_jpeg = "/tmp/_proto_jpeg.rrd"
rr.init("proto_jpeg")
rr.save(rrd_jpeg)
cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
for fid in range(n_vid):
    ok, bgr = cap.read()
    if not ok:
        break
    small = cv2.resize(bgr, (W, H), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_Q])
    rr.set_time("time", timestamp=fid / FPS)
    rr.log("cam/image", rr.EncodedImage(contents=bytes(buf), media_type="image/jpeg"))
    rr.log("cam/image/probe", rr.Points2D([[(fid / max(1, n_vid - 1)) * W, H / 2]], radii=12, colors=[255, 0, 0]))
rr.disconnect()
cap.release()
sz_jpeg = os.path.getsize(rrd_jpeg)

print("\n================ RESULTS ================")
print(f"frames logged           : {n_vid}")
print(f"video frame timestamps  : {n_vid}  (== overlay frames? {n_vid == N or n_vid <= N})")
print(f"time mapping            : overlay fid/fps and video PTS agree at f0={secs[0]:.4f}s, "
      f"f1={secs[1]:.4f}s (expect {1/FPS:.4f}s)")
print(f".rrd  AssetVideo(H.264) : {sz_video/1e6:8.2f} MB  ->  {rrd_video}")
print(f".rrd  per-frame JPEG    : {sz_jpeg/1e6:8.2f} MB  ->  {rrd_jpeg}")
print(f"REAL .rrd size ratio    : {sz_jpeg/sz_video:6.1f}x smaller with AssetVideo")
