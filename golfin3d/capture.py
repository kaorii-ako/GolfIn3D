"""USB camera capture into a JPEG ring buffer, plus clip save/load."""
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

BACKEND = {"win32": cv2.CAP_DSHOW, "darwin": cv2.CAP_AVFOUNDATION}.get(sys.platform, cv2.CAP_V4L2)


def list_cameras(max_index=8):
    """Indices that open and return a frame. Skips indices a running Camera already holds."""
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, BACKEND)
        if cap.isOpened() and cap.read()[0]:
            found.append(i)
        cap.release()
    return found


class Camera(threading.Thread):
    """Grabs frames as fast as the camera allows. Keeps the last `seconds` as JPEG bytes
    (a 120 fps raw buffer would be gigabytes) and the latest raw frame for live view."""

    def __init__(self, index, width=1280, height=720, fps=120, seconds=6.0):
        super().__init__(daemon=True)
        self.index, self.req = index, (width, height, fps)
        self.seconds = seconds
        self.buf = deque()          # (t_monotonic, jpeg bytes)
        self.latest = None          # (t, BGR)
        self.fps = 0.0
        self.size = (0, 0)
        self.error = None
        self._halt = threading.Event()
        self._lock = threading.Lock()

    def run(self):
        cap = cv2.VideoCapture(self.index, BACKEND)
        if not cap.isOpened():
            self.error = f"Camera {self.index} unavailable"
            return
        w, h, fps = self.req
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # needed for >30 fps on most UVC cams
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, fps)
        stamps = deque(maxlen=60)
        while not self._halt.is_set():
            ok, frame = cap.read()
            t = time.monotonic()
            if not ok:
                time.sleep(0.01)
                continue
            jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()
            with self._lock:
                self.buf.append((t, jpg))
                while self.buf and t - self.buf[0][0] > self.seconds:
                    self.buf.popleft()
            self.latest = (t, frame)
            self.size = (frame.shape[1], frame.shape[0])
            stamps.append(t)
            if len(stamps) > 10:
                self.fps = (len(stamps) - 1) / (stamps[-1] - stamps[0])
        cap.release()

    def clip(self, t0, t1):
        with self._lock:
            return [(t, j) for t, j in self.buf if t0 <= t <= t1]

    def stop(self):
        self._halt.set()


def save_clip(path, frames, impact_t):
    """frames: [(t, jpeg)]. Writes MJPG .avi (plays anywhere, no re-encode loss beyond JPEG)
    and a sidecar of per-frame times relative to impact. Returns frame count."""
    if not frames:
        return 0
    first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
    h, w = first.shape[:2]
    dur = frames[-1][0] - frames[0][0]
    fps = (len(frames) - 1) / dur if dur > 0 else 30
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    for _, j in frames:
        vw.write(cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR))
    vw.release()
    Path(path).with_suffix(".json").write_text(json.dumps({
        "t": [round(t - impact_t, 5) for t, _ in frames], "fps": fps, "size": [w, h]}))
    return len(frames)


class Clip:
    """A saved clip held as JPEG bytes (decoded on demand) with impact-relative times."""

    def __init__(self, path, max_width=1280):
        meta = json.loads(Path(path).with_suffix(".json").read_text())
        self.t = np.array(meta["t"])
        self.fps = meta["fps"]
        self.size = tuple(meta["size"])
        self.scale = min(1.0, max_width / self.size[0])
        self.jpgs = []
        cap = cv2.VideoCapture(str(path))
        while True:
            ok, f = cap.read()
            if not ok:
                break
            if self.scale < 1:
                f = cv2.resize(f, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
            self.jpgs.append(cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
        cap.release()
        self.t = self.t[: len(self.jpgs)]

    def __len__(self):
        return len(self.jpgs)

    def index_at(self, t):
        return int(np.argmin(np.abs(self.t - t))) if len(self.t) else 0

    def frame(self, i):
        return cv2.imdecode(np.frombuffer(self.jpgs[i], np.uint8), cv2.IMREAD_COLOR)


def read_frames(path):
    """Full-resolution frames for analysis."""
    cap = cv2.VideoCapture(str(path))
    while True:
        ok, f = cap.read()
        if not ok:
            break
        yield f
    cap.release()
