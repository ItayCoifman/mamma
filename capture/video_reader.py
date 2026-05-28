"""In-memory video frame reader for MP4 files.

No frames are written to disk. SAM receives the MP4 path directly;
YOLO and CLIP receive PIL Images from this reader.

Usage::

    from capture.video_reader import VideoFrameReader, cam_data_from_video

    reader = VideoFrameReader("/path/to/IOI_09.mp4", start=10, end=50)
    pil_img = reader.read_pil(0)   # frame 0 within the range (global frame 10)
    print(len(reader))             # 40 frames

    cam_data = cam_data_from_video("/path/to/IOI_09.mp4", start=10, end=50)
"""
import logging
import os
from typing import Any, Dict

import numpy as np

logger = logging.getLogger(__name__)


class VideoFrameReader:
    """Random-access frame reader for MP4 videos. No disk I/O for frames.

    Each ``read_*`` call opens a cv2.VideoCapture, seeks, reads one frame, and
    closes. Simple and thread-safe, though slower than keeping the capture open.
    """

    def __init__(self, video_path: str, start: int = None, end: int = None):
        import cv2

        self.video_path = os.path.abspath(video_path)

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()

        self.start = max(0, min(start or 0, total))
        self.end = max(self.start, min(end or total, total))
        self.n_frames = self.end - self.start
        logger.info(
            "VideoFrameReader: '%s' frames [%d:%d] (%d of %d frames, %dx%d, %.1f fps)",
            os.path.basename(video_path), self.start, self.end,
            self.n_frames, total, self.width, self.height, self.fps,
        )

    def __len__(self) -> int:
        return self.n_frames

    def read_bgr(self, local_idx: int) -> np.ndarray:
        """Read frame as BGR numpy array (cv2 convention)."""
        import cv2

        if local_idx < 0 or local_idx >= self.n_frames:
            raise IndexError(f"Frame index {local_idx} out of range [0, {self.n_frames})")
        global_idx = self.start + local_idx
        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, global_idx)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            raise RuntimeError(f"Failed to read frame {global_idx} from '{self.video_path}'")
        return frame

    def read_rgb(self, local_idx: int) -> np.ndarray:
        """Read frame as RGB numpy array."""
        import cv2
        return cv2.cvtColor(self.read_bgr(local_idx), cv2.COLOR_BGR2RGB)

    def read_pil(self, local_idx: int):
        """Read frame as PIL Image (RGB)."""
        from PIL import Image
        return Image.fromarray(self.read_rgb(local_idx))


def cam_data_from_video(video_path: str, start: int = None, end: int = None) -> Dict[str, Any]:
    """Build a cam_data dict from an MP4 video -- no disk I/O.

    Args:
        video_path: Path to MP4 video file.
        start: First frame index (0-based, inclusive). None = 0.
        end: Last frame index (0-based, exclusive). None = all frames.

    Returns:
        Dict compatible with ``process_multi_video_auto`` / ``FrameSource`` factory.
    """
    cam_name = os.path.splitext(os.path.basename(video_path))[0]
    reader = VideoFrameReader(video_path, start=start, end=end)

    return {
        'cam_name': np.array(cam_name),
        'video_path': np.array(os.path.abspath(video_path)),
        'frame_reader': reader,
    }
