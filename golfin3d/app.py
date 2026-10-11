"""GolfIn3D: two-camera swing capture synced to a Foresight GC3, with 2D/3D pose."""
import json
import queue
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QElapsedTimer, QObject, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QListView, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox,
                               QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import pose as P
from .capture import Camera, Clip, list_cameras, read_frames, save_clip
from .triggers import AudioTrigger, OpenConnectServer
from .ui import (ACCENT, BLUE, ORANGE, QSS, RAISED, RED, SURFACE, TEXT, TEXT2, TEXT3, Scrubber, Segmented, Skeleton3D,
                 StatusPill, Toast, VideoTile, font, label, normalise_3d)

SWINGS = P.DATA / "swings"
ROLES = {"face": "Face-on", "dtl": "Down the line"}


class Bus(QObject):
    """Thread -> UI hop. Every worker talks to the UI only through these signals."""
    shot = Signal(dict, float)
    impact = Signal(float)
    gc3_status = Signal(str)
    swing_saved = Signal(str)
    swing_ready = Signal(str)
    done = Signal(object, object)


BUS = None


def run_bg(fn, cb):
    threading.Thread(target=lambda: BUS.done.emit(cb, fn()), daemon=True).start()


def card(*widgets, spacing=10, margins=16):
    f = QFrame()
    f.setProperty("role", "card")
    v = QVBoxLayout(f)
    v.setContentsMargins(margins, margins, margins, margins)
    v.setSpacing(spacing)
    for w in widgets:
        v.addLayout(w) if isinstance(w, (QHBoxLayout, QVBoxLayout, QGridLayout)) else v.addWidget(w)
    return f


def row(*widgets, spacing=8, stretch_at=None):
    h = QHBoxLayout()
    h.setSpacing(spacing)
    h.setContentsMargins(0, 0, 0, 0)
    for i, w in enumerate(widgets):
        if w is None:
            h.addStretch(1)
        elif isinstance(w, (QHBoxLayout, QVBoxLayout)):
            h.addLayout(w)
        else:
            h.addWidget(w, 1 if i == stretch_at else 0)
    return h


def button(text, kind=None, checkable=False):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if kind:
        b.setProperty("kind", kind)
    b.setCheckable(checkable)
    return b


def page():
    w = QWidget()
    w.setObjectName("page")
    return w


# ---------------------------------------------------------------- analysis worker

def analyze_swing(folder, lefty):
    """Pose for each clip, triangulate if calibrated, metrics, thumbnail. Runs off the UI thread."""
    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text())
    data, sizes = {}, {}
    for role in ROLES:
        avi = folder / f"{role}.avi"
        if not avi.exists():
            continue
        side = json.loads(avi.with_suffix(".json").read_text())
        t = np.array(side["t"])
        p2, pw = P.analyze(read_frames(avi), t, side["size"][1])
        n = min(len(t), len(p2))
        data[f"{role}2d"], data[f"{role}w"], data[f"t_{role}"] = p2[:n], pw[:n], t[:n]
        sizes[role] = tuple(side["size"])
    calib = P.load_calib()
    if calib is not None and "face2d" in data and "dtl2d" in data:
        tf, td = data["t_face"], data["t_dtl"]
        j = P.match_times(tf, td)
        keep = np.abs(td[j] - tf) < 0.6 / max(meta["fps"].get("face", 30), meta["fps"].get("dtl", 30), 1) * 2
        data["p3"] = P.triangulate(calib, data["face2d"][keep], data["dtl2d"][j[keep]], sizes["face"], sizes["dtl"])
        data["t3"] = tf[keep]
        meta["source3d"] = "stereo"
    elif data:
        role = "face" if "facew" in data else "dtl"
        data["p3"], data["t3"] = data[f"{role}w"], data[f"t_{role}"]
        meta["source3d"] = "single camera"
    np.savez_compressed(folder / "pose.npz", **data)
    ph, ms = P.metrics(data.get("t_face"), data.get("face2d"), data.get("t_dtl"), data.get("dtl2d"),
                       data.get("t3"), data.get("p3"), lefty)
    meta.update(phases=ph, metrics=[[k, round(float(v), 1), u] for k, v, u in ms], status="ready")
    (folder / "meta.json").write_text(json.dumps(meta, indent=1))


class Pipeline(threading.Thread):
    """Serialises save + analysis so back-to-back swings don't fight over the CPU."""

    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.lefty = False

    def run(self):
        while True:
            job, folder = self.q.get()
            try:
                job()
                if folder:
                    analyze_swing(folder, self.lefty)
                    BUS.swing_ready.emit(str(folder))
            except Exception as e:  # keep the pipeline alive; mark the swing
                print("analysis failed:", folder, e, file=sys.stderr)
                if folder and (Path(folder) / "meta.json").exists():
                    m = json.loads((Path(folder) / "meta.json").read_text())
                    m["status"] = f"failed: {e}"
                    (Path(folder) / "meta.json").write_text(json.dumps(m, indent=1))
                    BUS.swing_ready.emit(str(folder))


class LivePose(threading.Thread):
    """Lightweight pose on the newest frame of a camera, for the live overlay."""

    def __init__(self, cam):
        super().__init__(daemon=True)
        self.cam, self.pts, self.on, self.error = cam, None, True, None
        self._halt = threading.Event()

    def run(self):
        try:
            tr = P.PoseTracker("full")
        except Exception as e:
            self.error = str(e)
            return
        last = None
        kf = P.Kalman(66)
        while not self._halt.is_set():
            latest = self.cam.latest
            if not self.on or latest is None or latest[0] == last:
                time.sleep(0.008)
                continue
            last, f = latest
            s = min(1.0, 640 / f.shape[1])
            pts, _ = tr(cv2.resize(f, None, fx=s, fy=s) if s < 1 else f, last * 1000)
            pts[:, :2] /= s
            # forward-only Kalman (the live view can't look ahead); swings get the zero-lag smoother
            h, vis = f.shape[0], np.nan_to_num(pts[:, 2])
            z = np.where(vis[:, None] >= P.MIN_VIS, pts[:, :2], np.nan).ravel()
            r = np.repeat((P.KF_SIGMA * h / np.maximum(vis, P.MIN_VIS)) ** 2, 2)
            pts[:, :2] = kf.step(last, z, r, np.repeat(P.KF_ACCEL * h, 2) ** 2).reshape(33, 2)
            self.pts = pts
        tr.close()

    def stop(self):
        self._halt.set()


# ---------------------------------------------------------------- small widgets

class ShotStrip(QWidget):
    """Launch-monitor numbers as a row of big figures."""
    ORDER = ["Ball speed", "Club speed", "Launch", "Spin", "Carry", "Direction", "Spin axis", "Path", "Face", "AoA"]

    def __init__(self, compact=False):
        super().__init__()
        self.shot = {}
        self.compact = compact
        self.setFixedHeight(46 if compact else 54)

    def set(self, shot):
        self.shot = shot or {}
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        items = [(k, *self.shot[k]) for k in self.ORDER if k in self.shot]
        if not items:
            p.setPen(QColor(TEXT3))
            p.setFont(font(13))
            p.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, "No launch monitor data for this swing")
            return
        x = 0
        big, small = font(19 if self.compact else 22, QFont.DemiBold), font(11, QFont.Medium)
        for k, v, u in items:
            num = f"{v:.0f}" if abs(v) >= 100 else f"{v:.1f}"
            if u == "°":  # degrees read as part of the number
                num, u = num + "°", ""
            p.setFont(big)
            w1 = p.fontMetrics().horizontalAdvance(num)
            p.setFont(small)
            w = max(w1 + p.fontMetrics().horizontalAdvance(u) + 4, p.fontMetrics().horizontalAdvance(k.upper()))
            if x + w > self.width():
                break
            p.setFont(big)
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(x, 0, w1 + 2, 30), Qt.AlignBottom | Qt.AlignLeft, num)
            p.setFont(small)
            p.setPen(QColor(TEXT2))
            p.drawText(QRectF(x + w1 + 4, 0, 40, 27), Qt.AlignBottom | Qt.AlignLeft, u)
            p.setPen(QColor(TEXT3))
            p.drawText(QRectF(x, 32, w + 10, 16), Qt.AlignLeft, k.upper())
            x += w + 28


class MetricsTable(QWidget):
    """Label / value rows. With two columns it shows A, B and the difference."""

    def __init__(self):
        super().__init__()
        self.rows, self.cols = [], 1

    def set(self, metrics_a, metrics_b=None):
        a = {k: (v, u) for k, v, u in metrics_a or []}
        b = {k: (v, u) for k, v, u in metrics_b or []}
        self.cols = 2 if metrics_b is not None else 1
        keys = list(a) + [k for k in b if k not in a]
        self.rows = [(k, a.get(k, (None,))[0], (a.get(k) or b.get(k))[1], b.get(k, (None,))[0]) for k in keys]
        self.setFixedHeight(max(1, len(self.rows)) * 30 + 26)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.rows:
            p.setPen(QColor(TEXT3))
            p.setFont(font(13))
            p.drawText(self.rect(), Qt.AlignCenter, "Metrics appear once pose analysis finishes")
            return
        w = self.width()
        p.setFont(font(11, QFont.DemiBold))
        p.setPen(QColor(TEXT3))
        p.drawText(QRectF(0, 0, w, 18), Qt.AlignLeft, "METRICS")
        if self.cols == 2:
            for x, t in ((w - 170, "A"), (w - 100, "B"), (w - 30, "Δ")):
                p.drawText(QRectF(x - 40, 0, 70, 18), Qt.AlignRight, t)
        y = 26
        for i, (k, v, u, vb) in enumerate(self.rows):
            if i:
                p.setPen(QColor(255, 255, 255, 14))
                p.drawLine(0, y, w, y)
            p.setFont(font(13))
            p.setPen(QColor(TEXT2))
            p.drawText(QRectF(0, y, w, 30), Qt.AlignVCenter | Qt.AlignLeft, k)
            p.setFont(font(13, QFont.DemiBold))
            p.setPen(QColor(TEXT))
            fmt = lambda x: f"{x:.1f}{u}" if u in ("°", ":1") else f"{x:.1f} {u}"
            if self.cols == 1:
                p.drawText(QRectF(0, y, w, 30), Qt.AlignVCenter | Qt.AlignRight, fmt(v))
            else:
                p.drawText(QRectF(w - 210, y, 80, 30), Qt.AlignVCenter | Qt.AlignRight, "—" if v is None else fmt(v))
                p.drawText(QRectF(w - 140, y, 80, 30), Qt.AlignVCenter | Qt.AlignRight, "—" if vb is None else fmt(vb))
                if v is not None and vb is not None:
                    d = vb - v
                    p.setPen(QColor(TEXT2 if abs(d) < 1 else ORANGE))
                    p.drawText(QRectF(w - 70, y, 70, 30), Qt.AlignVCenter | Qt.AlignRight, f"{d:+.1f}")
            y += 30


class LevelMeter(QWidget):
    """Mic level with the trigger threshold marked."""

    def __init__(self):
        super().__init__()
        self.level, self.threshold = 0.0, 0.5
        self.setFixedHeight(10)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(RAISED))
        p.drawRoundedRect(QRectF(self.rect()), 5, 5)
        p.setBrush(QColor(ORANGE if self.level >= self.threshold else ACCENT))
        p.drawRoundedRect(QRectF(0, 0, self.width() * min(1, self.level), self.height()), 5, 5)
        p.setBrush(QColor(TEXT))
        p.drawRect(QRectF(self.width() * self.threshold - 1, 0, 2, self.height()))


class SwingDelegate(QStyledItemDelegate):
    """Library card: rounded thumbnail, time, key numbers."""
    W, H = 268, 214

    def sizeHint(self, opt, idx):
        return QSize(self.W, self.H)

    def paint(self, p, opt, idx):
        meta = idx.data(Qt.UserRole + 1) or {}
        pix = idx.data(Qt.DecorationRole)
        r = QRectF(opt.rect).adjusted(6, 6, -6, -6)
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        sel = opt.state & QStyle.State_Selected
        hov = opt.state & QStyle.State_MouseOver
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, QColor("#1a1a1d" if hov else SURFACE))
        thumb = QRectF(r.x(), r.y(), r.width(), r.width() * 9 / 16)
        tp = QPainterPath()
        tp.addRoundedRect(thumb, 14, 14)
        tp.addRect(thumb.adjusted(0, 20, 0, 0))   # square off the bottom corners
        tp.setFillRule(Qt.WindingFill)
        p.setClipPath(tp)
        if isinstance(pix, QPixmap) and not pix.isNull():
            p.drawPixmap(thumb, pix, QRectF(pix.rect()))
        else:
            p.fillRect(thumb, QColor(RAISED))
        p.setClipping(False)
        status = meta.get("status", "")
        if status != "ready":
            p.setFont(font(11, QFont.DemiBold))
            text = "Analyzing…" if status == "analyzing" else "Analysis failed"
            w = p.fontMetrics().horizontalAdvance(text) + 18
            box = QRectF(thumb.x() + 10, thumb.y() + 10, w, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(box, 11, 11)
            p.setPen(QColor(TEXT if status == "analyzing" else RED))
            p.drawText(box, Qt.AlignCenter, text)
        y = thumb.bottom() + 10
        p.setPen(QColor(TEXT))
        p.setFont(font(14, QFont.DemiBold))
        when = datetime.fromisoformat(meta.get("created", "2000-01-01T00:00:00"))
        p.drawText(QRectF(r.x() + 14, y, r.width() - 28, 20), Qt.AlignLeft, when.strftime("%a %d %b, %H:%M:%S"))
        shot = meta.get("shot") or {}
        bits = [f"{shot[k][0]:.0f} {shot[k][1]}" for k in ("Ball speed", "Carry", "Spin") if k in shot]
        if not bits:
            bits = [f"{v:.1f}{u if u in ('°', ':1') else ' ' + u}" for k, v, u in (meta.get("metrics") or [])[:2]]
        p.setPen(QColor(TEXT2))
        p.setFont(font(12))
        p.drawText(QRectF(r.x() + 14, y + 22, r.width() - 28, 18), Qt.AlignLeft, "  ·  ".join(bits) or "—")
        if sel:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(ACCENT), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 13, 13)
        p.restore()


# ---------------------------------------------------------------- swing loading + player

def load_swing(folder):
    """Everything the swing view needs, loaded off the UI thread."""
    folder = Path(folder)
    s = {"folder": folder, "meta": json.loads((folder / "meta.json").read_text()), "clips": {}}
    for role in ROLES:
        if (folder / f"{role}.avi").exists():
            s["clips"][role] = Clip(folder / f"{role}.avi")
    s["pose"] = dict(np.load(folder / "pose.npz")) if (folder / "pose.npz").exists() else {}
    return s


def track_3d(pose, meta):
    """Viewer-ready 3D track, or None if there is no usable 3D."""
    p3 = pose.get("p3")
    if p3 is None or not len(p3) or np.isnan(p3).all():
        return None
    addr = int(np.argmin(np.abs(pose["t3"] - (meta.get("phases") or {}).get("address", pose["t3"][0]))))
    return normalise_3d(p3, addr)


class Player(QObject):
    """Shared clock in impact-relative seconds. Advances by wall time x speed, loops."""
    tick = Signal(float)

    def __init__(self):
        super().__init__()
        self.t, self.t0, self.t1, self.speed, self.playing = 0.0, -2.0, 1.0, 0.25, False
        self.step = 1 / 120
        self._timer = QTimer(self, interval=8, timeout=self._advance)
        self._clock = QElapsedTimer()

    def set_range(self, t0, t1, step):
        self.t0, self.t1, self.step = t0, t1, step
        self.seek(min(max(self.t, t0), t1))

    def seek(self, t):
        self.t = min(max(t, self.t0), self.t1)
        self.tick.emit(self.t)

    def toggle(self):
        self.playing = not self.playing
        if self.playing:
            if self.t >= self.t1 - 1e-6:
                self.t = self.t0
            self._clock.start()
            self._timer.start()
        else:
            self._timer.stop()

    def pause(self):
        if self.playing:
            self.toggle()

    def frame(self, n):
        self.pause()
        self.seek(self.t + n * self.step)

    def _advance(self):
        dt = self._clock.restart() / 1000
        t = self.t + dt * self.speed
        self.seek(self.t0 if t > self.t1 else t)


SPEEDS = [("1×", 1.0), ("½×", 0.5), ("¼×", 0.25), ("⅛×", 0.125)]
TOOLS = [("View", None), ("Line", "line"), ("Angle", "angle"), ("Circle", "circle")]


def transport(player, tiles):
    """Play / step / speed / drawing tools bar shared by swing and compare views."""
    play = button("Play")
    play.setFixedWidth(76)
    play.clicked.connect(player.toggle)
    prev_b, next_b = button("‹"), button("›")
    prev_b.setToolTip("Previous frame (←)")
    next_b.setToolTip("Next frame (→)")
    prev_b.clicked.connect(lambda: player.frame(-1))
    next_b.clicked.connect(lambda: player.frame(1))
    speed = Segmented([s for s, _ in SPEEDS], compact=True)
    speed.set_index(2, animate=False)
    speed.changed.connect(lambda i: setattr(player, "speed", SPEEDS[i][1]))
    tools = Segmented([t for t, _ in TOOLS], compact=True)
    tools.changed.connect(lambda i: [t.set_tool(TOOLS[i][1]) for t in tiles])
    clear = button("Clear", "ghost")
    clear.clicked.connect(lambda: [t.clear_shapes() for t in tiles])
    player.tick.connect(lambda _: play.setText("Pause" if player.playing else "Play"))
    return row(play, prev_b, next_b, speed, None, tools, clear), speed


def frame_at(clip, pose2d, t_pose, t, lefty, trail=True, color=ACCENT, alpha=255, offset=0.0):
    """Frame + overlays for clip at impact-relative time t (+offset for phase alignment)."""
    i = clip.index_at(t + offset)
    img = clip.frame(i)
    sk, tr = [], None
    if pose2d is not None and len(pose2d):
        j = int(np.argmin(np.abs(t_pose - clip.t[i])))
        sk = [(pose2d[j], color, alpha)]
        if trail:
            w = P.R_WR if lefty else P.L_WR
            seg = pose2d[: j + 1, w]
            tr = np.where((np.nan_to_num(seg[:, 2]) > 0.4)[:, None], seg[:, :2], np.nan)
    return img, sk, tr


# ---------------------------------------------------------------- pages

class LivePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("page")
        self.tiles = {r: VideoTile(n) for r, n in ROLES.items()}
        self.pose_btn = button("Pose overlay", checkable=True)
        self.pose_btn.setChecked(True)
        self.pose_btn.toggled.connect(lambda on: [lp.__setattr__("on", on) for lp in win.live_pose.values()])
        cap = button("Capture now", "primary")
        cap.setToolTip("Save the last few seconds (C)")
        cap.clicked.connect(lambda: win.on_impact(time.monotonic() - win.s_float("post", 1.0)))
        self.shots = ShotStrip()
        self.last_title = label("Waiting for your first swing", "h2")
        open_b = button("Open swing")
        open_b.clicked.connect(lambda: win.open_swing(win.last_swing) if win.last_swing else None)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 8, 20, 20)
        lay.setSpacing(14)
        lay.addLayout(row(self.tiles["face"], self.tiles["dtl"], spacing=14), 1)
        lay.addWidget(card(row(self.last_title, None, self.pose_btn, open_b, cap), self.shots, spacing=8))
        self._t = QTimer(self, interval=16, timeout=self.refresh)
        self._t.start()

    def refresh(self):
        if not self.isVisible():
            return
        for role, tile in self.tiles.items():
            cam = self.win.cams.get(role)
            if cam is None or cam.latest is None:
                tile.empty = (cam.error if cam and cam.error else "Starting camera…") if cam else "No camera assigned · Setup"
                tile.set_frame(None)
                continue
            lp = self.win.live_pose.get(role)
            sk = [(lp.pts, ACCENT, 230)] if lp and lp.on and lp.pts is not None else []
            tile.sub = f"{cam.fps:.0f} fps"
            tile.set_frame(cam.latest[1], sk)

    def show_last(self, meta):
        when = datetime.fromisoformat(meta["created"]).strftime("%H:%M:%S")
        self.last_title.setText(f"Last swing · {when}")
        self.shots.set(meta.get("shot"))


class LibraryPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("page")
        self.model = QStandardItemModel()
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(0)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setItemDelegate(SwingDelegate())
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.doubleClicked.connect(lambda i: win.open_swing(i.data(Qt.UserRole)))
        self.view.activated.connect(lambda i: win.open_swing(i.data(Qt.UserRole)))
        self.count = label("", "muted")
        self.cmp = button("Compare", "primary")
        self.cmp.setToolTip("Select two swings (Ctrl/⌘-click)")
        self.cmp.clicked.connect(self._compare)
        self.dele = button("Delete")
        self.dele.clicked.connect(self._delete)
        self.view.selectionModel().selectionChanged.connect(self._sel)
        self.empty = label("No swings yet. Hit a ball on the Live page, or press Capture now.", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 12, 24, 16)
        lay.addLayout(row(label("Swings", "h1"), self.count, None, self.dele, self.cmp, spacing=12))
        lay.addWidget(self.empty, 1)
        lay.addWidget(self.view, 1)
        self._sel()

    def _selected(self):
        return [i.data(Qt.UserRole) for i in self.view.selectionModel().selectedIndexes()]

    def _sel(self, *a):
        n = len(self._selected())
        self.cmp.setEnabled(n == 2)
        self.dele.setEnabled(n > 0)

    def _compare(self):
        a, b = sorted(self._selected())
        self.win.open_compare(a, b)

    def _delete(self):
        sel = self._selected()
        if QMessageBox.question(self, "Delete swings", f"Delete {len(sel)} swing(s)? This can't be undone.") == QMessageBox.Yes:
            for f in sel:
                shutil.rmtree(f, ignore_errors=True)
            self.reload()

    def reload(self):
        self.model.clear()
        folders = sorted((d for d in SWINGS.glob("*") if (d / "meta.json").exists()), reverse=True) if SWINGS.exists() else []
        for d in folders:
            self.model.appendRow(self._item(d))
        self.count.setText(f"{len(folders)} captured")
        self.empty.setVisible(not folders)
        self.view.setVisible(bool(folders))
        self._sel()

    def _item(self, d):
        it = QStandardItem()
        it.setEditable(False)
        it.setData(str(d), Qt.UserRole)
        self.update_item(it)
        return it

    def update_item(self, it):
        d = Path(it.data(Qt.UserRole))
        try:
            it.setData(json.loads((d / "meta.json").read_text()), Qt.UserRole + 1)
        except (OSError, ValueError):
            return
        if (d / "thumb.jpg").exists():
            it.setData(QPixmap(str(d / "thumb.jpg")), Qt.DecorationRole)

    def refresh_folder(self, folder):
        for r in range(self.model.rowCount()):
            it = self.model.item(r)
            if it.data(Qt.UserRole) == folder:
                self.update_item(it)
                return
        self.reload()


class SwingPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win, self.s, self.p3 = win, None, None
        self.setObjectName("page")
        self.player = Player()
        self.player.tick.connect(self.render)
        self.tiles = {r: VideoTile(n) for r, n in ROLES.items()}
        self.view3d = Skeleton3D()
        self.scrub = Scrubber()
        self.scrub.seeked.connect(lambda t: (self.player.pause(), self.player.seek(t)))
        self.title = label("", "h1")
        self.shots = ShotStrip()
        self.metrics = MetricsTable()
        self.show_sk = button("Skeleton", checkable=True)
        self.show_tr = button("Hand path", checkable=True)
        for b in (self.show_sk, self.show_tr):
            b.setChecked(True)
            b.toggled.connect(lambda _: self.render(self.player.t))
        back = button("‹  Swings", "ghost")
        back.clicked.connect(lambda: win.go(1))
        bar, self.speed = transport(self.player, list(self.tiles.values()))
        side = QScrollArea()
        side.setWidgetResizable(True)
        side.setFrameShape(QFrame.NoFrame)
        side.setFixedWidth(340)
        inner = QWidget()
        inner.setObjectName("page")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.addWidget(card(self.metrics))
        iv.addStretch(1)
        side.setWidget(inner)
        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self.view3d, 1)
        right.addWidget(side, 1)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addLayout(row(self.tiles["face"], self.tiles["dtl"], spacing=14), 1)
        left.addWidget(self.scrub)
        left.addLayout(row(bar, self.show_sk, self.show_tr))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 4, 20, 20)
        lay.setSpacing(12)
        lay.addLayout(row(back, self.title, None, spacing=12))
        lay.addWidget(card(self.shots, margins=14))
        body = QHBoxLayout()
        body.setSpacing(14)
        body.addLayout(left, 1)
        rw = QWidget()
        rw.setFixedWidth(340)
        rw.setLayout(right)
        right.setContentsMargins(0, 0, 0, 0)
        body.addWidget(rw)
        lay.addLayout(body, 1)

    def load(self, folder):
        self.title.setText("Loading…")
        self.s = None
        self.player.pause()
        for t in self.tiles.values():
            t.set_frame(None)
            t.empty = "Loading…"
        run_bg(lambda: load_swing(folder), self._loaded)

    def _loaded(self, s):
        self.s = s
        m = s["meta"]
        self.title.setText(datetime.fromisoformat(m["created"]).strftime("%A %d %B · %H:%M:%S"))
        self.shots.set(m.get("shot"))
        self.metrics.set(m.get("metrics"))
        clips = list(s["clips"].values())
        for r, t in self.tiles.items():
            t.empty = "Not recorded"
            t.sub = f"{s['clips'][r].fps:.0f} fps" if r in s["clips"] else ""
        if not clips:
            return
        self.p3 = None
        pose = s["pose"]
        self.p3 = track_3d(pose, m)
        if self.p3 is not None:
            self.view3d.source = m.get("source3d", "")
        else:
            self.view3d.note = "Analyzing…" if m.get("status") == "analyzing" else "No person detected"
            self.view3d.source = ""
        t0 = min(c.t[0] for c in clips)
        t1 = max(c.t[-1] for c in clips)
        self.scrub.set_range(t0, t1)
        self.scrub.marks = m.get("phases") or {}
        self.player.set_range(t0, t1, 1 / max(c.fps for c in clips))
        self.player.seek(m.get("phases", {}).get("address", t0) if m.get("phases") else t0)

    def render(self, t):
        self.scrub.set_time(t)
        if not self.s:
            return
        pose, lefty = self.s["pose"], self.win.lefty
        for role, tile in self.tiles.items():
            clip = self.s["clips"].get(role)
            if clip is None:
                continue
            img, sk, tr = frame_at(clip, pose.get(f"{role}2d"), pose.get(f"t_{role}"), t, lefty)
            tile.set_frame(img, sk if self.show_sk.isChecked() else [], tr if self.show_tr.isChecked() else None, clip.size)
        if self.p3 is not None:
            i = int(np.argmin(np.abs(self.s["pose"]["t3"] - t)))
            w = P.R_WR if lefty else P.L_WR
            self.view3d.set_pose([(self.p3[i], ACCENT, 255)], [(self.p3[: i + 1, w], BLUE)] if self.show_tr.isChecked() else [])
        else:
            self.view3d.set_pose([])


class ComparePage(QWidget):
    ALIGN = ["Impact", "Top", "Address"]

    def __init__(self, win):
        super().__init__()
        self.win, self.a, self.b = win, None, None
        self.setObjectName("page")
        self.player = Player()
        self.player.tick.connect(self.render)
        self.tiles = [VideoTile("A"), VideoTile("B")]
        self.view3d = Skeleton3D()
        self.view3d.note = "Pick two swings in Swings, then Compare"
        self.scrub = Scrubber()
        self.scrub.seeked.connect(lambda t: (self.player.pause(), self.player.seek(t)))
        self.role = Segmented(list(ROLES.values()), compact=True)
        self.role.changed.connect(lambda _: self.render(self.player.t))
        self.align = Segmented(["Sync impact", "Sync top", "Sync address"], compact=True)
        self.align.changed.connect(lambda _: self._sync())
        self.metrics = MetricsTable()
        self.ghost = button("Ghost B on A", checkable=True)
        self.ghost.toggled.connect(lambda _: self.render(self.player.t))
        bar, _ = transport(self.player, self.tiles)
        self.empty = label("Select two swings in Swings (Ctrl/⌘-click) and press Compare.", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addLayout(row(*self.tiles, spacing=14), 1)
        left.addWidget(self.scrub)
        left.addLayout(bar)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(14)
        right.addWidget(self.view3d, 1)
        right.addWidget(card(self.metrics))
        rw = QWidget()
        rw.setFixedWidth(360)
        rw.setLayout(right)
        self.body = QWidget()
        bl = QHBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(14)
        bl.addLayout(left, 1)
        bl.addWidget(rw)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 12, 20, 20)
        lay.setSpacing(12)
        lay.addLayout(row(label("Compare", "h1"), None, self.ghost, self.align, self.role, spacing=10))
        lay.addWidget(self.empty, 1)
        lay.addWidget(self.body, 1)
        self.body.hide()

    def load(self, fa, fb):
        self.player.pause()
        run_bg(lambda: (load_swing(fa), load_swing(fb)), self._loaded)

    def _loaded(self, ab):
        self.a, self.b = ab
        self.empty.hide()
        self.body.show()
        for tile, s, n in zip(self.tiles, ab, "AB"):
            tile.title = f"{n} · " + datetime.fromisoformat(s["meta"]["created"]).strftime("%d %b %H:%M:%S")
        self.metrics.set(self.a["meta"].get("metrics"), self.b["meta"].get("metrics") or [])
        self._p3 = [track_3d(s["pose"], s["meta"]) for s in ab]
        self._sync()

    def _sync(self):
        """Offset B so the chosen phase lines up with A's."""
        if not self.a:
            return
        key = self.ALIGN[self.align.index].lower()
        pa, pb = self.a["meta"].get("phases") or {}, self.b["meta"].get("phases") or {}
        self.off_b = (pb.get(key, 0) - pa.get(key, 0)) if key in pa and key in pb else 0.0
        clips = list(self.a["clips"].values()) or [None]
        c = clips[0]
        if c is None:
            return
        self.scrub.set_range(c.t[0], c.t[-1])
        self.scrub.marks = pa
        self.player.set_range(c.t[0], c.t[-1], 1 / c.fps)
        self.player.seek(self.player.t)

    def render(self, t):
        self.scrub.set_time(t)
        if not self.a:
            return
        role = list(ROLES)[self.role.index]
        lefty = self.win.lefty
        overlays = []
        for k, (tile, s, off, color) in enumerate(zip(self.tiles, (self.a, self.b), (0.0, self.off_b), (ACCENT, BLUE))):
            clip = s["clips"].get(role)
            if clip is None:
                tile.set_frame(None)
                tile.empty = f"No {ROLES[role]} clip"
                continue
            pose = s["pose"]
            img, sk, tr = frame_at(clip, pose.get(f"{role}2d"), pose.get(f"t_{role}"), t, lefty, color=color, offset=off)
            overlays.append(sk)
            tile.set_frame(img, sk, tr, clip.size)
        if self.ghost.isChecked() and len(overlays) == 2 and overlays[1]:
            a_tile = self.tiles[0]
            a_tile.skeletons = a_tile.skeletons + [(overlays[1][0][0], BLUE, 150)]
            a_tile.update()
        series = []
        w = P.R_WR if lefty else P.L_WR
        paths = []
        for s, p3, off, color, alpha in zip((self.a, self.b), self._p3, (0.0, self.off_b), (ACCENT, BLUE), (255, 170)):
            if p3 is None:
                continue
            i = int(np.argmin(np.abs(s["pose"]["t3"] - (t + off))))
            series.append((p3[i], color, alpha))
            paths.append((p3[: i + 1, w], color))
        self.view3d.note = "No 3D data"
        self.view3d.set_pose(series, paths)


def form(*pairs):
    """Label / field rows; fields keep a sensible width instead of stretching across the card."""
    g = QGridLayout()
    g.setHorizontalSpacing(12)
    g.setVerticalSpacing(10)
    for i, (text, w) in enumerate(pairs):
        g.addWidget(label(text, "muted"), i, 0)
        if isinstance(w, QHBoxLayout):
            g.addLayout(w, i, 1)
        else:
            w.setMaximumWidth(240)
            g.addWidget(w, i, 1)
    g.setColumnStretch(2, 1)
    return g


class SetupPage(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = page()
        self.setWidget(inner)
        s = win.settings

        # cameras
        self.cam_combo = {r: QComboBox() for r in ROLES}
        self.res = QComboBox()
        self.res.addItems(["1280×720", "1920×1080", "640×480"])
        self.res.setCurrentText(s.value("res", "1280×720"))
        self.fps = QComboBox()
        self.fps.addItems(["30", "60", "120", "240"])
        self.fps.setCurrentText(str(s.value("fps", "120")))
        scan = button("Rescan")
        scan.clicked.connect(self.scan)
        apply_b = button("Apply", "primary")
        apply_b.clicked.connect(self.apply_cams)
        g = form(*((n, self.cam_combo[r]) for r, n in ROLES.items()), ("Resolution", self.res), ("Frame rate (requested)", self.fps))
        cams = card(label("Cameras", "h2"), g,
                    label("Face-on looks at the golfer's chest. Down-the-line sits behind the hands, looking at the screen. "
                          "Cameras deliver the closest rate they support; the actual rate shows on each live tile.", "faint"),
                    row(None, scan, apply_b))

        # launch monitor
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(s.value("port", 921)))
        self.gc3_lat = QSpinBox()
        self.gc3_lat.setRange(0, 5000)
        self.gc3_lat.setSuffix(" ms")
        self.gc3_lat.setValue(int(s.value("gc3_latency", 700)))
        self.gc3_lat.valueChanged.connect(lambda v: s.setValue("gc3_latency", v))
        self.gc3_state = label("", "muted")
        self.gc3_state.setWordWrap(True)
        port_apply = button("Restart listener")
        port_apply.clicked.connect(lambda: (s.setValue("port", self.port.value()), win.start_gc3()))
        g2 = form(("Listen port", self.port), ("Shot data delay", self.gc3_lat))
        lm = card(label("Foresight GC3", "h2"), g2, self.gc3_state,
                  label("In FSX Play, turn on the GSPro connection and point it at this computer on the port above "
                        "(GolfIn3D speaks the GSPro OpenConnect protocol). Don't run GSPro on the same port at the same time. "
                        "Shot data delay is how long after impact FSX reports the shot; used only when the mic didn't catch the impact. "
                        "On macOS/Linux, ports below 1024 need admin rights.",
                        "faint"), row(None, port_apply))

        # audio
        self.mic_on = QCheckBox("Trigger on impact sound")
        self.mic_on.setChecked(s.value("mic", "true") == "true")
        self.mic_on.toggled.connect(lambda v: (s.setValue("mic", "true" if v else "false"), win.start_audio()))
        self.thr = QSlider(Qt.Horizontal)
        self.thr.setRange(5, 100)
        self.thr.setValue(int(float(s.value("threshold", 0.5)) * 100))
        self.meter = LevelMeter()
        self.thr.valueChanged.connect(self._thr)
        self.mic_off = QSpinBox()
        self.mic_off.setRange(-500, 500)
        self.mic_off.setSuffix(" ms")
        self.mic_off.setValue(int(s.value("mic_offset", 0)))
        self.mic_off.valueChanged.connect(lambda v: (s.setValue("mic_offset", v), win.audio and setattr(win.audio, "offset_ms", v)))
        self._thr(self.thr.value())
        audio = card(label("Impact microphone", "h2"), self.mic_on, label("Sensitivity — set the white mark above room noise, below a strike", "faint"),
                     self.thr, self.meter, form(("Timing offset", self.mic_off)))

        # capture
        self.pre = QDoubleSpinBox()
        self.pre.setRange(0.5, 4.0)
        self.pre.setSingleStep(0.25)
        self.pre.setSuffix(" s")
        self.pre.setValue(win.s_float("pre", 2.0))
        self.pre.valueChanged.connect(lambda v: s.setValue("pre", v))
        self.post = QDoubleSpinBox()
        self.post.setRange(0.25, 2.0)
        self.post.setSingleStep(0.25)
        self.post.setSuffix(" s")
        self.post.setValue(win.s_float("post", 1.0))
        self.post.valueChanged.connect(lambda v: s.setValue("post", v))
        self.hand = Segmented(["Right-handed", "Left-handed"], compact=True)
        self.hand.set_index(1 if win.lefty else 0, animate=False)
        self.hand.changed.connect(lambda i: (s.setValue("lefty", "true" if i else "false"), win.apply_hand()))
        g3 = form(("Before impact", self.pre), ("After impact", self.post), ("Golfer", self.hand))
        capc = card(label("Capture", "h2"), g3)

        # calibration
        self.cols, self.rows_, self.square = QSpinBox(), QSpinBox(), QDoubleSpinBox()
        for w, v, lo, hi in ((self.cols, 9, 3, 20), (self.rows_, 6, 3, 20)):
            w.setRange(lo, hi)
            w.setValue(v)
        self.square.setRange(5, 200)
        self.square.setValue(40)
        self.square.setSuffix(" mm")
        self.cal_btn = button("Start calibration", "primary")
        self.cal_btn.clicked.connect(self.toggle_calib)
        self.cal_state = label("", "muted")
        self.cal_state.setWordWrap(True)
        g4 = form(("Inner corners", row(self.cols, label("×", "muted"), self.rows_, None)), ("Square size", self.square))
        calc = card(label("3D calibration", "h2"),
                    label("Print a checkerboard, tape it to something flat, and slowly move it around the hitting area, "
                          "tilting it so both cameras see it at once. Frames are collected automatically. "
                          "Without calibration, 3D comes from a single camera and is less accurate.", "faint"),
                    g4, self.cal_state, row(None, self.cal_btn))
        self._calib = None
        self._cal_timer = QTimer(self, interval=250, timeout=self._cal_poll)
        self._show_calib()

        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 12, 24, 24)
        lay.setSpacing(14)
        lay.addWidget(label("Setup", "h1"))
        grid = QGridLayout()
        grid.setSpacing(14)
        grid.addWidget(cams, 0, 0)
        grid.addWidget(lm, 0, 1)
        grid.addWidget(audio, 1, 0)
        grid.addWidget(capc, 1, 1)
        grid.addWidget(calc, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
        self._meter_t = QTimer(self, interval=33, timeout=self._meter)
        self._meter_t.start()
        self.fill_combos(None)

    def _thr(self, v):
        self.meter.threshold = v / 100
        self.win.settings.setValue("threshold", v / 100)
        if self.win.audio:
            self.win.audio.threshold = v / 100

    def _meter(self):
        if self.isVisible():
            self.meter.level = self.win.audio.level if self.win.audio else 0
            self.meter.update()

    def fill_combos(self, found):
        for r, c in self.cam_combo.items():
            cur = self.win.settings.value(f"cam_{r}", "")
            c.clear()
            c.addItem("None", "")
            idx = sorted(set((found or []) + [int(v) for v in (self.win.settings.value("cam_face", ""), self.win.settings.value("cam_dtl", "")) if str(v) != ""]))
            for i in idx:
                c.addItem(f"Camera {i}", str(i))
            c.setCurrentIndex(max(0, c.findData(str(cur))))

    def scan(self):
        self.win.stop_cams()
        self.fill_combos(list_cameras())
        self.win.start_cams()

    def apply_cams(self):
        for r, c in self.cam_combo.items():
            self.win.settings.setValue(f"cam_{r}", c.currentData())
        self.win.settings.setValue("res", self.res.currentText())
        self.win.settings.setValue("fps", self.fps.currentText())
        self.win.stop_cams()
        self.win.start_cams()
        self.win.toast.show_msg("Cameras restarted")

    # calibration: a detector thread watches both live feeds
    def toggle_calib(self):
        if self._calib:
            self._calib["stop"] = True
            self._finish_calib()
            return
        a, b = self.win.cams.get("face"), self.win.cams.get("dtl")
        if not (a and b):
            self.cal_state.setText("Assign both cameras first.")
            return
        c = dict(stop=False, a=[], b=[], pairs=[], cols=self.cols.value(), rows=self.rows_.value(), size_a=None, size_b=None)
        self._calib = c

        def loop():
            last_t = 0
            while not c["stop"]:
                fa, fb = a.latest, b.latest
                if not (fa and fb) or fa[0] == last_t:
                    time.sleep(0.05)
                    continue
                last_t = fa[0]
                ca = P.find_board(fa[1], c["cols"], c["rows"])
                cb = P.find_board(fb[1], c["cols"], c["rows"]) if abs(fa[0] - fb[0]) < 0.05 else None
                c["size_a"], c["size_b"] = a.size, b.size
                if ca is not None and cb is not None:
                    c["pairs"].append((ca, cb))
                elif ca is not None and len(c["a"]) < 20:
                    c["a"].append(ca)
                elif cb is not None and len(c["b"]) < 20:
                    c["b"].append(cb)
                else:
                    continue
                time.sleep(0.6)  # spread samples across poses
        threading.Thread(target=loop, daemon=True).start()
        self.cal_btn.setText("Finish")
        self._cal_timer.start()

    def _cal_poll(self):
        c = self._calib
        if not c:
            return
        n = len(c["pairs"])
        self.cal_state.setText(f"Both cameras: {n}/15   ·   face-on only: {len(c['a'])}   ·   down-the-line only: {len(c['b'])}")
        if n >= 15:
            c["stop"] = True
            self._finish_calib()

    def _finish_calib(self):
        c, self._calib = self._calib, None
        self._cal_timer.stop()
        self.cal_btn.setText("Start calibration")
        if len(c["pairs"]) < 5:
            self.cal_state.setText(f"Need at least 5 views seen by both cameras (got {len(c['pairs'])}). Try tilting the board toward the camera that misses it.")
            return
        self.cal_state.setText("Solving…")
        sq = self.square.value()
        run_bg(lambda: P.calibrate(c["a"], c["b"], c["pairs"], c["size_a"], c["size_b"], c["cols"], c["rows"], sq),
               lambda _: self._show_calib(True))

    def _show_calib(self, fresh=False):
        cal = P.load_calib()
        if cal is None:
            self.cal_state.setText("Not calibrated.")
            return
        rms = cal["rms"][2]
        quality = "good" if rms < 1 else "usable" if rms < 2 else "poor — recalibrate"
        self.cal_state.setText(f"Calibrated · reprojection error {rms:.2f} px ({quality}). New swings use stereo 3D.")
        if fresh:
            self.win.toast.show_msg("Calibration saved", f"Reprojection error {rms:.2f} px")


# ---------------------------------------------------------------- main window

class Main(QMainWindow):
    NAV = ["Live", "Swings", "Compare", "Setup"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GolfIn3D")
        self.resize(1480, 920)
        self.settings = QSettings("GolfIn3D", "GolfIn3D")
        self.cams, self.live_pose = {}, {}
        self.audio, self.gc3 = None, None
        self.last_swing, self.pending = None, None   # pending: impact time awaiting the cut
        self._shot_for_pending = None
        self.recent = []                              # [(impact_t, folder)] for late GC3 data
        self.pipeline = Pipeline()
        self.pipeline.lefty = self.lefty
        self.pipeline.start()
        SWINGS.mkdir(parents=True, exist_ok=True)

        BUS.impact.connect(self.on_impact)
        BUS.shot.connect(self.on_shot)
        BUS.gc3_status.connect(self.on_gc3_status)
        BUS.swing_ready.connect(self.on_ready)
        BUS.swing_saved.connect(lambda f: self.library.reload())

        self.gc3_pill, self.mic_pill = StatusPill("GC3"), StatusPill("Mic")
        self.nav = Segmented(self.NAV)
        self.nav.changed.connect(lambda i: self.go(i, animate=False))
        top = QHBoxLayout()
        top.setContentsMargins(20, 14, 20, 10)
        brand = label("GolfIn3D")
        brand.setFont(font(15, QFont.Bold))
        top.addWidget(brand)
        top.addStretch(1)
        top.addWidget(self.nav)
        top.addStretch(1)
        top.addWidget(self.gc3_pill)
        top.addWidget(self.mic_pill)

        self.stack = QStackedWidget()
        self.live, self.library, self.swing = LivePage(self), LibraryPage(self), SwingPage(self)
        self.compare, self.setup = ComparePage(self), SetupPage(self)
        for w in (self.live, self.library, self.compare, self.setup, self.swing):
            self.stack.addWidget(w)
        root = page()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addLayout(top)
        v.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.toast = Toast(root)

        for i in range(4):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, lambda i=i: self.go(i, animate=False))
        QShortcut(QKeySequence("C"), self, lambda: self.on_impact(time.monotonic() - self.s_float("post", 1.0)))
        QShortcut(QKeySequence("Space"), self, lambda: self._player() and self._player().toggle())
        QShortcut(QKeySequence("Left"), self, lambda: self._player() and self._player().frame(-1))
        QShortcut(QKeySequence("Right"), self, lambda: self._player() and self._player().frame(1))

        self.library.reload()
        self.start_cams()
        self.start_gc3()
        self.start_audio()
        self._pill_t = QTimer(self, interval=500, timeout=self._pills)
        self._pill_t.start()
        self._gc3_text = "GC3"

    # settings helpers
    def s_float(self, k, d):
        return float(self.settings.value(k, d))

    @property
    def lefty(self):
        return self.settings.value("lefty", "false") == "true"

    def apply_hand(self):
        self.pipeline.lefty = self.lefty

    def _player(self):
        w = self.stack.currentWidget()
        return getattr(w, "player", None)

    # navigation: the nav pill animates; page swaps are instant (used constantly)
    def go(self, i, animate=True):
        self.nav.set_index(i, animate)
        self.stack.setCurrentIndex(i)
        if i == 1:
            self.library.reload()

    def open_swing(self, folder):
        self.nav.set_index(1)
        self.stack.setCurrentWidget(self.swing)
        self.swing.load(folder)

    def open_compare(self, a, b):
        self.go(2)
        self.compare.load(a, b)

    # devices
    def start_cams(self):
        w, h = map(int, self.settings.value("res", "1280×720").split("×"))
        fps = int(self.settings.value("fps", 120))
        for role in ROLES:
            idx = self.settings.value(f"cam_{role}", "")
            if str(idx) == "":
                continue
            cam = Camera(int(idx), w, h, fps, seconds=self.s_float("pre", 2.0) + self.s_float("post", 1.0) + 2.5)
            cam.start()
            self.cams[role] = cam
            lp = LivePose(cam)
            lp.on = self.live.pose_btn.isChecked()
            lp.start()
            self.live_pose[role] = lp

    def stop_cams(self):
        for lp in self.live_pose.values():
            lp.stop()
        for c in self.cams.values():
            c.stop()
        for c in self.cams.values():
            c.join(timeout=2)
        self.cams, self.live_pose = {}, {}

    def start_gc3(self):
        if self.gc3:
            self.gc3.stop()
        self.gc3 = OpenConnectServer(lambda s, t: BUS.shot.emit(s, t), BUS.gc3_status.emit, int(self.settings.value("port", 921)))
        self.gc3.start()

    def start_audio(self):
        if self.audio:
            self.audio.stop()
            self.audio = None
        if self.settings.value("mic", "true") != "true":
            return
        self.audio = AudioTrigger(BUS.impact.emit, self.s_float("threshold", 0.5), self.s_float("mic_offset", 0))
        self.audio.start()

    def on_gc3_status(self, text):
        self._gc3_text = text
        self.setup.gc3_state.setText(text)

    def _pills(self):
        t = self._gc3_text
        self.gc3_pill.set("GC3 connected" if "connected" in t else "GC3 waiting" if "Waiting" in t else "GC3 offline",
                          ACCENT if "connected" in t else TEXT3 if "Waiting" in t else RED)
        if not self.audio:
            self.mic_pill.set("Mic off", TEXT3)
        elif self.audio.error:
            self.mic_pill.set("Mic unavailable", RED)
        else:
            self.mic_pill.set("Mic armed", ACCENT)

    # capture logic: mic gives precise impact time, GC3 gives the numbers (and a fallback trigger)
    def on_impact(self, t):
        if not self.cams or (self.pending is not None and abs(t - self.pending) < 1.5):
            return
        self.pending = t
        QTimer.singleShot(int(self.s_float("post", 1.0) * 1000) + 120, lambda: self.cut(t))

    def on_shot(self, shot, t_recv):
        lat = int(self.settings.value("gc3_latency", 700)) / 1000
        # shot belongs to an impact we already caught if that impact was in the last few seconds
        for t_imp, folder in reversed(self.recent):
            if 0 <= t_recv - t_imp < 4:
                self._attach(folder, shot)
                return
        if self.pending is not None and 0 <= t_recv - self.pending < 4:
            self._shot_for_pending = shot
            return
        self._shot_for_pending = shot
        self.on_impact(t_recv - lat)

    def _attach(self, folder, shot):
        m = Path(folder) / "meta.json"
        meta = json.loads(m.read_text())
        meta["shot"] = shot
        m.write_text(json.dumps(meta, indent=1))
        self.live.show_last(meta)
        self.library.refresh_folder(str(folder))
        self.toast.show_msg("Shot data received", self._shot_line(shot))

    @staticmethod
    def _shot_line(shot):
        return "  ·  ".join(f"{shot[k][0]:.1f} {shot[k][1]}" for k in ("Ball speed", "Launch", "Spin", "Carry") if k in (shot or {}))

    def cut(self, t):
        self.pending = None
        pre, post = self.s_float("pre", 2.0), self.s_float("post", 1.0)
        folder = SWINGS / datetime.now().strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        clips = {r: (c.clip(t - pre, t + post), c.fps) for r, c in self.cams.items()}
        shot = self._shot_for_pending
        self._shot_for_pending = None
        meta = dict(created=datetime.now().isoformat(timespec="seconds"), shot=shot, status="analyzing",
                    fps={r: round(f, 1) for r, (_, f) in clips.items()})
        (folder / "meta.json").write_text(json.dumps(meta, indent=1))
        self.recent = [x for x in self.recent if t - x[0] < 10] + [(t, str(folder))]
        self.last_swing = str(folder)
        self.live.show_last(meta)
        self.toast.show_msg("Swing captured", self._shot_line(shot) or "Analyzing pose…")

        def save():
            thumb = None
            for role, (frames, _) in clips.items():
                save_clip(folder / f"{role}.avi", frames, t)
                if frames and (thumb is None or role == "dtl"):
                    j = min(frames, key=lambda f: abs(f[0] - t))[1]
                    thumb = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
            if thumb is not None:
                s = 480 / thumb.shape[1]
                cv2.imwrite(str(folder / "thumb.jpg"), cv2.resize(thumb, None, fx=s, fy=s, interpolation=cv2.INTER_AREA))
            BUS.swing_saved.emit(str(folder))
        self.pipeline.q.put((save, folder))

    def on_ready(self, folder):
        self.library.refresh_folder(folder)
        if self.stack.currentWidget() is self.swing and self.swing.s and str(self.swing.s["folder"]) == folder:
            self.swing.load(folder)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move((self.width() - self.toast.width()) // 2, self.toast.y())

    def closeEvent(self, e):
        self.stop_cams()
        if self.audio:
            self.audio.stop()
        if self.gc3:
            self.gc3.stop()
        super().closeEvent(e)


def selftest():
    """Packaged-build check: the bundled models load and run. Exit code 0 = ok."""
    for kind in ("full", "heavy"):
        tr = P.PoseTracker(kind)
        p2, _ = tr(np.zeros((240, 320, 3), np.uint8), 0)
        tr.close()
        assert p2.shape == (33, 3)
    cam = Camera(99)  # no such device: thread exits at once; join() must not raise (camera restart path)
    cam.start()
    cam.stop()
    cam.join(2)
    if sys.stdout:  # windowed builds on Windows have no stdout
        print("selftest ok")


def main():
    global BUS
    if "--selftest" in sys.argv:
        selftest()
        return
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("GolfIn3D")
    app.setStyle("Fusion")
    f = app.font()
    f.setPixelSize(13)
    app.setFont(f)
    app.setStyleSheet(QSS)
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "icon.png")))
    BUS = Bus()
    BUS.done.connect(lambda cb, r: cb(r))
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
