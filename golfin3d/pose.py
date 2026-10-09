"""Pose estimation (MediaPipe), stereo calibration/triangulation and swing metrics."""
import json
import math
import urllib.request
from pathlib import Path

import cv2
import numpy as np

DATA = Path.home() / "GolfIn3D"
MODELS = DATA / "models"
CALIB = DATA / "calibration.json"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_{0}/float16/latest/pose_landmarker_{0}.task"

# MediaPipe landmark indices
NOSE, L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 0, 11, 12, 13, 14, 15, 16
L_HIP, R_HIP, L_KN, R_KN, L_AN, R_AN = 23, 24, 25, 26, 27, 28
BONES = [(L_SH, R_SH), (L_SH, L_EL), (L_EL, L_WR), (R_SH, R_EL), (R_EL, R_WR),
         (L_SH, L_HIP), (R_SH, R_HIP), (L_HIP, R_HIP), (L_HIP, L_KN), (L_KN, L_AN),
         (R_HIP, R_KN), (R_KN, R_AN)]
LEFT = {L_SH, L_EL, L_WR, L_HIP, L_KN, L_AN}


def model_path(kind="full"):
    """Bundled model if the build shipped one, else download once into ~/GolfIn3D/models."""
    bundled = Path(__file__).parent / "models" / f"pose_landmarker_{kind}.task"
    if bundled.exists():
        return str(bundled)
    p = MODELS / f"pose_landmarker_{kind}.task"
    if not p.exists():
        MODELS.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL.format(kind), tmp)
        tmp.rename(p)
    return str(p)


class PoseTracker:
    """One MediaPipe landmarker in VIDEO mode. Timestamps must increase per instance."""

    def __init__(self, kind="full"):
        from mediapipe.tasks.python import BaseOptions, vision
        import mediapipe as mp
        self._mp = mp
        self._lm = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path(kind)),
            running_mode=vision.RunningMode.VIDEO, num_poses=1))
        self._last_ts = -1

    def __call__(self, bgr, ts_ms):
        """-> (33,3) [x_px, y_px, visibility] and (33,3) world metres, or NaN arrays."""
        ts = max(int(ts_ms), self._last_ts + 1)
        self._last_ts = ts
        h, w = bgr.shape[:2]
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                             data=np.ascontiguousarray(bgr[:, :, ::-1]))
        r = self._lm.detect_for_video(img, ts)
        if not r.pose_landmarks:
            return np.full((33, 3), np.nan), np.full((33, 3), np.nan)
        p2 = np.array([(l.x * w, l.y * h, l.visibility) for l in r.pose_landmarks[0]])
        p3 = np.array([(l.x, -l.y, -l.z) for l in r.pose_world_landmarks[0]])  # y up
        return p2, p3

    def close(self):
        self._lm.close()


def analyze(frames, times):
    """frames: iterable of BGR images; times: seconds. -> (N,33,3) 2D, (N,33,3) world."""
    tr = PoseTracker("full")
    out2, out3 = [], []
    for f, t in zip(frames, times):
        a, b = tr(f, (t - times[0]) * 1000)
        out2.append(a)
        out3.append(b)
    tr.close()
    return np.array(out2), np.array(out3)


# ---------------------------------------------------------------- calibration

def board_points(cols, rows, square_mm):
    obj = np.zeros((rows * cols, 3), np.float32)
    obj[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_mm
    return obj


def find_board(bgr, cols, rows):
    """Chessboard corners in full-res pixels, or None. Detects on a downscaled copy for speed."""
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    s = min(1.0, 640 / g.shape[1])
    small = cv2.resize(g, None, fx=s, fy=s) if s < 1 else g
    ok, c = cv2.findChessboardCorners(small, (cols, rows), cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_FAST_CHECK)
    if not ok:
        return None
    c = (c / s).astype(np.float32)
    return cv2.cornerSubPix(g, c, (7, 7), (-1, -1), (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3))


def calibrate(singles_a, singles_b, pairs, size_a, size_b, cols, rows, square_mm):
    """singles_*: corner lists per camera; pairs: [(corners_a, corners_b)]. Saves and returns calib dict."""
    obj = board_points(cols, rows, square_mm)
    views_a = singles_a + [p[0] for p in pairs]
    views_b = singles_b + [p[1] for p in pairs]
    ra, Ka, da, _, _ = cv2.calibrateCamera([obj] * len(views_a), views_a, size_a, None, None)
    rb, Kb, db, _, _ = cv2.calibrateCamera([obj] * len(views_b), views_b, size_b, None, None)
    rs, *_, R, T, _, _ = cv2.stereoCalibrate(
        [obj] * len(pairs), [p[0] for p in pairs], [p[1] for p in pairs],
        Ka, da, Kb, db, size_a, flags=cv2.CALIB_FIX_INTRINSIC)
    c = dict(Ka=Ka.tolist(), da=da.tolist(), Kb=Kb.tolist(), db=db.tolist(), R=R.tolist(), T=T.tolist(),
             size_a=list(size_a), size_b=list(size_b), rms=[ra, rb, rs])
    DATA.mkdir(parents=True, exist_ok=True)
    CALIB.write_text(json.dumps(c, indent=1))
    return c


def load_calib():
    try:
        c = json.loads(CALIB.read_text())
    except (OSError, ValueError):
        return None
    return {k: np.array(v) if k[0] in "KdRT" else v for k, v in c.items()}


def _scaled_K(K, calib_size, size):
    K = K.copy()
    K[0] *= size[0] / calib_size[0]
    K[1] *= size[1] / calib_size[1]
    return K


def triangulate(calib, a, b, size_a, size_b, min_vis=0.5):
    """a, b: (N,33,3) 2D tracks already time-matched. -> (N,33,3) mm in camera-A frame."""
    Ka = _scaled_K(calib["Ka"], calib["size_a"], size_a)
    Kb = _scaled_K(calib["Kb"], calib["size_b"], size_b)
    n = a.shape[0]
    pa = cv2.undistortPoints(np.nan_to_num(a[..., :2]).reshape(-1, 1, 2).astype(np.float64), Ka, calib["da"]).reshape(-1, 2)
    pb = cv2.undistortPoints(np.nan_to_num(b[..., :2]).reshape(-1, 1, 2).astype(np.float64), Kb, calib["db"]).reshape(-1, 2)
    P1 = np.hstack([np.eye(3), np.zeros((3, 1))])
    P2 = np.hstack([calib["R"], calib["T"].reshape(3, 1)])
    X = cv2.triangulatePoints(P1, P2, pa.T, pb.T)
    X = (X[:3] / X[3]).T.reshape(n, 33, 3)
    ok = (np.nan_to_num(a[..., 2]) > min_vis) & (np.nan_to_num(b[..., 2]) > min_vis)
    X[~ok] = np.nan
    return X


def match_times(ta, tb):
    """For each time in ta, index of nearest tb. Both sorted."""
    tb = np.asarray(tb)
    i = np.clip(np.searchsorted(tb, ta), 1, len(tb) - 1)
    return np.where(np.abs(tb[i - 1] - ta) <= np.abs(tb[i] - ta), i - 1, i)


# ---------------------------------------------------------------- metrics

def _mid(p, i, j):
    return (p[..., i, :] + p[..., j, :]) / 2


def _smooth(x, k=5):
    """Moving average along time, NaN-tolerant (NaNs forward/back-filled first)."""
    x = np.asarray(x, float).copy()
    ok = ~np.isnan(x)
    if ok.sum() < 2:
        return x
    x = np.interp(np.arange(len(x)), np.flatnonzero(ok), x[ok])
    return np.convolve(np.pad(x, k // 2, mode="edge"), np.ones(k) / k, mode="valid")


def phases(t, lead_wrist_y):
    """Swing phases from the lead wrist's image height. t: seconds relative to impact (0).
    -> dict(address, top, impact) times, or {} if the wrist was never tracked."""
    y = _smooth(lead_wrist_y)
    if np.isnan(y).all():
        return {}
    t = np.asarray(t)
    imp = int(np.argmin(np.abs(t)))
    win = np.flatnonzero((t >= t[imp] - 2.0) & (t <= t[imp]))
    if len(win) < 3:
        return {}
    top = int(win[np.argmin(y[win])])                       # highest hands = smallest y
    # address: hands at their lowest before the top; takeaway starts once they've risen 5% of the way up
    lo = int(win[0] + np.argmax(y[win[0]: top + 1]))
    rise = y[lo] - y[top]
    addr = lo + int(np.flatnonzero(y[lo: top + 1] >= y[lo] - 0.05 * rise)[-1])
    return dict(address=float(t[addr]), top=float(t[top]), impact=float(t[imp]))


def _angle_from_vertical(lower, upper):
    d = upper - lower
    return math.degrees(math.atan2(abs(d[0]), -d[1]))  # image y points down


def _at(t, arr, when):
    return arr[int(np.argmin(np.abs(np.asarray(t) - when)))]


def metrics(t_face, face, t_dtl, dtl, t3, p3, lefty=False):
    """All inputs may be None. 2D arrays (N,33,3) px; p3 (N,33,3) any unit (mm if stereo).
    -> (phases dict, list of (label, value, unit))."""
    lead_wr = R_WR if lefty else L_WR
    out = []
    ph = {}
    if face is not None:
        ph = phases(t_face, face[:, lead_wr, 1])
    elif dtl is not None:
        ph = phases(t_dtl, dtl[:, lead_wr, 1])
    if not ph:
        return ph, out
    bs, ds = ph["top"] - ph["address"], ph["impact"] - ph["top"]
    if ds > 0:
        out.append(("Tempo", bs / ds, ":1"))
    out.append(("Backswing", bs * 1000, "ms"))
    out.append(("Downswing", ds * 1000, "ms"))

    if face is not None:
        sw = np.nanmedian(np.linalg.norm(face[:, L_SH, :2] - face[:, R_SH, :2], axis=1))
        a, i = _at(t_face, face, ph["address"]), _at(t_face, face, ph["impact"])
        if sw > 0:
            out.append(("Head sway", (i[NOSE, 0] - a[NOSE, 0]) / sw * 100, "% sh"))
            out.append(("Hip sway", (_mid(i, L_HIP, R_HIP)[0] - _mid(a, L_HIP, R_HIP)[0]) / sw * 100, "% sh"))
        d = i[L_SH, :2] - i[R_SH, :2]
        out.append(("Shoulder tilt @ imp", math.degrees(math.atan2(-d[1], abs(d[0]))), "°"))

    if dtl is not None:
        a, i = _at(t_dtl, dtl, ph["address"]), _at(t_dtl, dtl, ph["impact"])
        sa = _angle_from_vertical(_mid(a, L_HIP, R_HIP)[:2], _mid(a, L_SH, R_SH)[:2])
        si = _angle_from_vertical(_mid(i, L_HIP, R_HIP)[:2], _mid(i, L_SH, R_SH)[:2])
        out.append(("Spine @ address", sa, "°"))
        out.append(("Spine @ impact", si, "°"))
        out.append(("Posture loss", sa - si, "°"))

    if p3 is not None and len(p3):
        a, top = _at(t3, p3, ph["address"]), _at(t3, p3, ph["top"])
        up = _mid(a, L_SH, R_SH) - _mid(a, L_AN, R_AN)
        if not np.isnan(up).any():
            up /= np.linalg.norm(up)
            sh = turn(a[L_SH] - a[R_SH], top[L_SH] - top[R_SH], up)
            hp = turn(a[L_HIP] - a[R_HIP], top[L_HIP] - top[R_HIP], up)
            out += [("Shoulder turn", sh, "°"), ("Hip turn", hp, "°"), ("X-factor", sh - hp, "°")]
    return ph, out


def turn(v0, v1, up):
    """Unsigned rotation (deg) of a body line about `up` from v0 to v1."""
    p0, p1 = v0 - up * (v0 @ up), v1 - up * (v1 @ up)
    c = p0 @ p1 / (np.linalg.norm(p0) * np.linalg.norm(p1))
    return math.degrees(math.acos(np.clip(c, -1, 1)))


if __name__ == "__main__":
    # self-check: phases, turn, triangulation round-trip
    t = np.linspace(-2, 0.5, 251)
    y = np.where(t < -1.2, 500, np.where(t < -0.3, 500 - (t + 1.2) / 0.9 * 300, 200 + (t + 0.3) / 0.3 * 300))
    ph = phases(t, y)
    assert abs(ph["top"] + 0.3) < 0.03 and abs(ph["address"] + 1.2) < 0.1 and abs(ph["impact"]) < 0.01, ph
    assert abs(turn(np.array([1, 0, 0.]), np.array([0, 0, 1.]), np.array([0, 1, 0.])) - 90) < 1e-6

    K = np.array([[800, 0, 320], [0, 800, 240], [0, 0, 1.]])
    R, _ = cv2.Rodrigues(np.array([0, math.radians(-60), 0.]))
    T = np.array([2000, 0, 800.])
    X = np.random.default_rng(0).uniform([-300, -800, 2500], [300, 800, 3500], (33, 3))
    def proj(R_, T_):
        p, _ = cv2.projectPoints(X, cv2.Rodrigues(R_)[0], T_, K, None)
        return np.hstack([p.reshape(-1, 2), np.ones((33, 1))])[None]
    calib = dict(Ka=K, da=np.zeros(5), Kb=K, db=np.zeros(5), R=R, T=T, size_a=[640, 480], size_b=[640, 480])
    Y = triangulate(calib, proj(np.eye(3), np.zeros(3)), proj(R, T), (640, 480), (640, 480))[0]
    assert np.abs(Y - X).max() < 1e-3, np.abs(Y - X).max()
    assert list(match_times([0.0, 0.5, 1.0], [0.1, 0.45, 0.9])) == [0, 1, 2]
    print("pose self-check ok")
