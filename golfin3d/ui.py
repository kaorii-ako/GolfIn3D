"""Theme and custom-painted widgets. Dark, quiet chrome so the video is the hero."""
import math

import numpy as np
from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from .pose import BONES, LEFT

BG, SURFACE, RAISED = "#0a0a0b", "#141416", "#1c1c1f"
TEXT, TEXT2, TEXT3 = "#f5f5f7", "#98989f", "#5c5c62"
ACCENT, BLUE, ORANGE, RED = "#30d158", "#0a84ff", "#ff9f0a", "#ff453a"
HAIR = "rgba(255,255,255,0.07)"
EASE = QEasingCurve.OutCubic  # Qt's strongest built-in ease-out; enter/move animations
ANIM_MS = 220

QSS = f"""
* {{ color: {TEXT}; outline: 0; }}
QMainWindow, QWidget#page {{ background: {BG}; }}
QLabel[role="h1"] {{ font-size: 22px; font-weight: 650; letter-spacing: -0.4px; }}
QLabel[role="h2"] {{ font-size: 13px; font-weight: 600; color: {TEXT2}; letter-spacing: 0.2px; }}
QLabel[role="muted"] {{ color: {TEXT2}; }}
QLabel[role="faint"] {{ color: {TEXT3}; font-size: 12px; }}
QFrame[role="card"] {{ background: {SURFACE}; border: 1px solid {HAIR}; border-radius: 14px; }}
QPushButton {{ background: {RAISED}; border: 1px solid {HAIR}; border-radius: 9px;
  padding: 7px 14px; font-weight: 550; }}
QPushButton:hover {{ background: #232327; }}
QPushButton:pressed {{ background: #2c2c31; padding-top: 8px; padding-bottom: 6px; }}
QPushButton:disabled {{ color: {TEXT3}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: #04130a; border: none; }}
QPushButton[kind="primary"]:hover {{ background: #3fdc66; }}
QPushButton[kind="primary"]:pressed {{ background: #28b84c; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {TEXT2}; }}
QPushButton[kind="ghost"]:hover {{ color: {TEXT}; }}
QPushButton:checked {{ background: rgba(48,209,88,0.16); color: {ACCENT}; border-color: rgba(48,209,88,0.35); }}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ background: {RAISED}; border: 1px solid {HAIR};
  border-radius: 8px; padding: 5px 9px; min-height: 20px; }}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {BLUE}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {HAIR}; selection-background-color: #2c2c31; }}
QSlider::groove:horizontal {{ height: 4px; background: #2c2c31; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QListView {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.14); border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid #3a3a3f; background: {RAISED}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {HAIR}; padding: 4px 8px; }}
"""


def label(text, role=None):
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(role == "faint")
    return w


def bgr_to_qimage(a):
    a = np.ascontiguousarray(a)
    return QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format_BGR888).copy()


def font(px, weight=QFont.Normal):
    f = QFont()
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


# ---------------------------------------------------------------- segmented control

class Segmented(QWidget):
    """Pill segmented control with a sliding selection. The pill animates from its
    current position so rapid clicks retarget smoothly instead of jumping."""
    changed = Signal(int)

    def __init__(self, items, compact=False):
        super().__init__()
        self.items, self.index, self._hover, self._press = items, 0, -1, -1
        self.h = 28 if compact else 32
        self.setFixedHeight(self.h)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._x = 0.0
        self._anim = QVariantAnimation(self, duration=ANIM_MS, easingCurve=EASE)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_x", v), self.update()))
        self._fm = font(12 if compact else 13, QFont.DemiBold)
        w = sum(self.fontMetrics().horizontalAdvance(t) for t in items)
        self.setMinimumWidth(w + len(items) * (24 if compact else 34))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return self.minimumSize()

    def _seg_w(self):
        return (self.width() - 4) / len(self.items)

    def set_index(self, i, animate=True):
        if i == self.index and animate:
            return
        self.index = i
        target = 2 + i * self._seg_w()
        if animate:
            self._anim.stop()
            self._anim.setStartValue(float(self._x))
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._x = target
            self.update()

    def resizeEvent(self, e):
        self._x = 2 + self.index * self._seg_w()

    def _at(self, x):
        return max(0, min(len(self.items) - 1, int((x - 2) // self._seg_w())))

    def mousePressEvent(self, e):
        self._press = self._at(e.position().x())
        self.update()

    def mouseReleaseEvent(self, e):
        i = self._at(e.position().x())
        if i == self._press and self.rect().contains(e.position().toPoint()):
            self.set_index(i)
            self.changed.emit(i)
        self._press = -1
        self.update()

    def mouseMoveEvent(self, e):
        self._hover = self._at(e.position().x())
        self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(RAISED))
        p.drawRoundedRect(QRectF(self.rect()), r, r)
        sw = self._seg_w()
        pill = QRectF(self._x, 2, sw, self.height() - 4)
        if self._press >= 0:  # instant press feedback, before release
            pill = pill.adjusted(1.5, 1, -1.5, -1)
        p.setBrush(QColor("#3a3a40"))
        p.drawRoundedRect(pill, r - 2, r - 2)
        p.setFont(self._fm)
        for i, t in enumerate(self.items):
            sel = i == self.index
            p.setPen(QColor(TEXT if sel or i == self._hover else TEXT2))
            p.drawText(QRectF(2 + i * sw, 0, sw, self.height()), Qt.AlignCenter, t)


# ---------------------------------------------------------------- status pill

class StatusPill(QWidget):
    def __init__(self, text):
        super().__init__()
        self.text, self.color = text, TEXT3
        self.setFixedHeight(26)
        self._f = font(12, QFont.Medium)
        self._fit()

    def _fit(self):
        self.setFixedWidth(self.fontMetrics().horizontalAdvance(self.text) + 38)

    def set(self, text, color):
        self.text, self.color = text, color
        self.setToolTip(text)
        self._fit()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(SURFACE))
        p.drawRoundedRect(QRectF(self.rect()), 13, 13)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QPointF(14, 13), 3.5, 3.5)
        p.setPen(QColor(TEXT2))
        p.setFont(self._f)
        p.drawText(QRectF(24, 0, self.width() - 28, 26), Qt.AlignVCenter, self.text)


# ---------------------------------------------------------------- skeleton overlay

def draw_skeleton(p, pts, to_screen, color=ACCENT, alpha=255, width=3.0):
    """pts: (33,3) [x, y, vis] in frame pixels. to_screen maps (x,y) -> QPointF."""
    if pts is None or np.isnan(pts[:, 0]).all():
        return
    vis = np.nan_to_num(pts[:, 2]) > 0.4
    left, right = QColor(color), QColor(TEXT)
    left.setAlpha(alpha)
    right.setAlpha(int(alpha * 0.85))
    for a, b in BONES:
        if vis[a] and vis[b]:
            c = left if a in LEFT and b in LEFT else right if a not in LEFT and b not in LEFT else QColor(255, 255, 255, alpha // 2)
            p.setPen(QPen(c, width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(to_screen(pts[a, :2]), to_screen(pts[b, :2]))
    p.setPen(Qt.NoPen)
    for i in {j for bone in BONES for j in bone}:
        if vis[i]:
            p.setBrush(left if i in LEFT else right)
            p.drawEllipse(to_screen(pts[i, :2]), width * 0.9, width * 0.9)


# ---------------------------------------------------------------- video tile

class VideoTile(QWidget):
    """Aspect-fit frame in a rounded card, with skeleton overlays, a label chip and
    simple drawing tools (line / angle / circle) stored in frame-normalised coords."""

    def __init__(self, title):
        super().__init__()
        self.title, self.sub = title, ""
        self.img, self.frame_size = None, (16, 9)
        self.skeletons = []         # [(pts, color, alpha)]
        self.trail = None           # (N,2) frame px, hand path
        self.empty = "No camera"
        self.tool, self.shapes, self._draft = None, [], []
        self.setMinimumSize(240, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_frame(self, bgr, skeletons=(), trail=None, src_size=None):
        """src_size: resolution the skeleton/trail pixels refer to, if the image was downscaled."""
        self.img = bgr_to_qimage(bgr) if bgr is not None else None
        if bgr is not None:
            self.frame_size = tuple(src_size or (bgr.shape[1], bgr.shape[0]))
        self.skeletons, self.trail = list(skeletons), trail
        self.update()

    def _rect(self):
        fw, fh = self.frame_size
        s = min(self.width() / fw, self.height() / fh)
        w, h = fw * s, fh * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), s

    def _norm(self, pos):
        r, _ = self._rect()
        return ((pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height())

    def _screen_n(self, n):
        r, _ = self._rect()
        return QPointF(r.x() + n[0] * r.width(), r.y() + n[1] * r.height())

    # drawing tools
    def mousePressEvent(self, e):
        if not self.tool or e.button() != Qt.LeftButton:
            return
        self._draft.append(self._norm(e.position()))
        need = {"line": 2, "circle": 2, "angle": 3}[self.tool]
        if self.tool != "angle" and len(self._draft) == 1:
            self._draft.append(self._draft[0])  # second point follows the mouse while dragging
        if len(self._draft) >= need and self.tool == "angle":
            self.shapes.append((self.tool, self._draft[:3]))
            self._draft = []
        self.update()

    def mouseMoveEvent(self, e):
        if self._draft:
            self._draft[-1] = self._norm(e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if self.tool in ("line", "circle") and len(self._draft) == 2:
            if self._draft[0] != self._draft[1]:
                self.shapes.append((self.tool, self._draft))
            self._draft = []
            self.update()

    def clear_shapes(self):
        self.shapes, self._draft = [], []
        self.update()

    def set_tool(self, tool):
        self.tool, self._draft = tool, []
        self.setCursor(Qt.CrossCursor if tool else Qt.ArrowCursor)
        self.setMouseTracking(bool(tool))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r, s = self._rect()
        clip = QPainterPath()
        clip.addRoundedRect(r if self.img else QRectF(self.rect()), 14, 14)
        p.fillPath(clip, QColor(SURFACE))
        if self.img is None:
            p.setPen(QColor(TEXT3))
            p.setFont(font(13))
            p.drawText(self.rect(), Qt.AlignCenter, self.empty)
        else:
            p.setClipPath(clip)
            p.drawImage(r, self.img)
            to = lambda xy: QPointF(r.x() + xy[0] * s, r.y() + xy[1] * s)
            if self.trail is not None and len(self.trail) > 1:
                pen = QPen(QColor(BLUE), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                path = QPainterPath()
                started = False
                for x, y in self.trail:
                    if np.isnan(x):
                        continue
                    pt = to((x, y))
                    path.lineTo(pt) if started else path.moveTo(pt)
                    started = True
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            for pts, color, alpha in self.skeletons:
                draw_skeleton(p, pts, to, color, alpha, width=max(2.0, 3.2 * min(1.0, r.width() / 900)))
            self._paint_shapes(p)
            p.setClipping(False)
        self._chip(p, r if self.img else QRectF(self.rect()))

    def _paint_shapes(self, p):
        pen = QPen(QColor(ORANGE), 2, Qt.SolidLine, Qt.RoundCap)
        p.setFont(font(12, QFont.DemiBold))
        r, _ = self._rect()
        for kind, pts in self.shapes + ([(self.tool, self._draft)] if self._draft else []):
            q = [self._screen_n(n) for n in pts]
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            if kind == "line" and len(q) >= 2:
                p.drawLine(q[0], q[1])
                d = (pts[1][0] - pts[0][0]) * r.width(), (pts[1][1] - pts[0][1]) * r.height()
                ang = abs(math.degrees(math.atan2(-d[1], d[0])))
                self._tag(p, q[1], f"{min(ang, 180 - ang):.0f}°")
            elif kind == "circle" and len(q) >= 2:
                rad = math.hypot(q[1].x() - q[0].x(), q[1].y() - q[0].y())
                p.drawEllipse(q[0], rad, rad)
            elif kind == "angle":
                for a, b in zip(q, q[1:]):
                    p.drawLine(a, b)
                if len(q) == 3:
                    v1 = (q[0].x() - q[1].x(), q[0].y() - q[1].y())
                    v2 = (q[2].x() - q[1].x(), q[2].y() - q[1].y())
                    a = abs(math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1])))
                    self._tag(p, q[1], f"{a:.0f}°")

    def _tag(self, p, at, text):
        w = p.fontMetrics().horizontalAdvance(text) + 12
        box = QRectF(at.x() + 8, at.y() - 22, w, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QColor(ORANGE))
        p.drawText(box, Qt.AlignCenter, text)

    def _chip(self, p, r):
        p.setFont(font(11, QFont.DemiBold))
        text = self.title.upper() + (f"  ·  {self.sub}" if self.sub else "")
        w = p.fontMetrics().horizontalAdvance(text) + 18
        box = QRectF(r.x() + 10, r.y() + 10, w, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(box, 11, 11)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(box, Qt.AlignCenter, text)


# ---------------------------------------------------------------- scrubber

class Scrubber(QWidget):
    """Timeline in impact-relative seconds with phase markers. Seeks on press, tracks 1:1."""
    seeked = Signal(float)

    def __init__(self):
        super().__init__()
        self.t0, self.t1, self.t = -2.0, 1.0, 0.0
        self.marks = {}
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self._drag = False

    def set_range(self, t0, t1):
        self.t0, self.t1 = t0, max(t1, t0 + 0.01)
        self.update()

    def set_time(self, t):
        self.t = t
        self.update()

    def _x(self, t):
        return 12 + (t - self.t0) / (self.t1 - self.t0) * (self.width() - 24)

    def _t(self, x):
        return min(self.t1, max(self.t0, self.t0 + (x - 12) / (self.width() - 24) * (self.t1 - self.t0)))

    def mousePressEvent(self, e):
        self._drag = True
        self.seeked.emit(self._t(e.position().x()))

    def mouseMoveEvent(self, e):
        if self._drag:
            self.seeked.emit(self._t(e.position().x()))

    def mouseReleaseEvent(self, e):
        self._drag = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = 26
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2c2c31"))
        p.drawRoundedRect(QRectF(12, y - 2, self.width() - 24, 4), 2, 2)
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(12, y - 2, self._x(self.t) - 12, 4), 2, 2)
        p.setFont(font(10, QFont.DemiBold))
        for name, t in self.marks.items():
            x = self._x(t)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TEXT2))
            p.drawRoundedRect(QRectF(x - 1, y - 7, 2, 14), 1, 1)
            p.setPen(QColor(TEXT3))
            p.drawText(QRectF(x - 40, 0, 80, 14), Qt.AlignCenter, name.upper())
        x = self._x(self.t)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(x, y), 8 if self._drag else 7, 8 if self._drag else 7)
        p.setPen(QColor(TEXT2))
        p.setFont(font(11))
        p.drawText(QRectF(self.width() - 92, 32, 80, 12), Qt.AlignRight, f"{self.t * 1000:+.0f} ms")


# ---------------------------------------------------------------- toast

class Toast(QWidget):
    """Slides down from the top edge and fades; exits faster than it enters."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title, self.body = "", ""
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._p = 0.0
        self._anim = QVariantAnimation(self, valueChanged=self._step)
        self._timer = QTimer(self, singleShot=True, timeout=self._out)

    def show_msg(self, title, body="", ms=2600):
        self.title, self.body = title, body
        f1, f2 = font(13, QFont.DemiBold), font(12)
        from PySide6.QtGui import QFontMetrics
        w = max(QFontMetrics(f1).horizontalAdvance(title), QFontMetrics(f2).horizontalAdvance(body)) + 48
        self.resize(max(w, 220), 58 if body else 40)
        self.raise_()
        self.show()
        self._go(1.0, ANIM_MS + 60, EASE)
        self._timer.start(ms)

    def _out(self):
        self._go(0.0, 160, QEasingCurve.InOutCubic)

    def _go(self, to, ms, curve):
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(curve)
        self._anim.setStartValue(self._p)  # from the current value: interruptible
        self._anim.setEndValue(to)
        self._anim.start()

    def _step(self, v):
        self._p = v
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, int(64 + (v - 1) * 14))
        self.setVisible(v > 0.01)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self._p)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(QColor(36, 36, 40, 245))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.setBrush(QColor(ACCENT))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(20, self.height() / 2), 4, 4)
        p.setPen(QColor(TEXT))
        p.setFont(font(13, QFont.DemiBold))
        if self.body:
            p.drawText(QRectF(34, 9, self.width() - 40, 20), Qt.AlignVCenter, self.title)
            p.setPen(QColor(TEXT2))
            p.setFont(font(12))
            p.drawText(QRectF(34, 29, self.width() - 40, 20), Qt.AlignVCenter, self.body)
        else:
            p.drawText(QRectF(34, 0, self.width() - 40, self.height()), Qt.AlignVCenter, self.title)


# ---------------------------------------------------------------- 3D view

class Skeleton3D(QWidget):
    """Orbitable 3D skeleton. Drag to orbit (1:1), release carries momentum and decays;
    scroll to zoom. Draws a ground grid and the lead-hand path."""

    def __init__(self):
        super().__init__()
        self.series = []       # [(pts (33,3) normalised, color, alpha)]
        self.paths = []        # [((N,3), color)]
        self.yaw, self.pitch, self.zoom = math.radians(-35), math.radians(12), 1.0
        self.note = "No 3D data yet"
        self.source = ""
        self._v = (0.0, 0.0)
        self._last = None
        self._spin = QTimer(self, interval=16, timeout=self._coast)
        self.setMinimumSize(240, 220)
        self.setCursor(Qt.OpenHandCursor)

    def set_pose(self, series, paths=()):
        self.series, self.paths = series, list(paths)
        self.update()

    def mousePressEvent(self, e):
        self._spin.stop()
        self._last, self._v = e.position(), (0.0, 0.0)
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        d = e.position() - self._last
        self._last = e.position()
        dy, dp = d.x() * 0.01, d.y() * 0.01
        self._v = (dy, dp)
        self._apply(dy, dp)

    def mouseReleaseEvent(self, e):
        self._last = None
        self.setCursor(Qt.OpenHandCursor)
        if abs(self._v[0]) + abs(self._v[1]) > 0.004:
            self._spin.start()

    def _coast(self):
        self._v = (self._v[0] * 0.94, self._v[1] * 0.94)
        self._apply(*self._v)
        if abs(self._v[0]) + abs(self._v[1]) < 0.0008:
            self._spin.stop()

    def _apply(self, dy, dp):
        self.yaw += dy
        self.pitch = max(-1.2, min(1.4, self.pitch + dp))
        self.update()

    def wheelEvent(self, e):
        self.zoom = max(0.4, min(3.0, self.zoom * (1.0015 ** e.angleDelta().y())))
        self.update()

    def _proj(self, X):
        """X: (...,3) in metres-ish, y up, centred near origin -> screen xy and depth."""
        cy, sy, cp, sp = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
        x = X[..., 0] * cy + X[..., 2] * sy
        z = -X[..., 0] * sy + X[..., 2] * cy
        y = X[..., 1] * cp - z * sp
        z = X[..., 1] * sp + z * cp
        f = min(self.width(), self.height()) * 0.42 * self.zoom
        persp = 3.2 / (3.2 + z)
        return np.stack([self.width() / 2 + x * f * persp, self.height() * 0.56 - (y - 0.9) * f * persp], -1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14, 14)
        p.fillPath(path, QColor(SURFACE))
        p.setFont(font(11, QFont.DemiBold))
        p.setPen(QColor(TEXT3))
        p.drawText(QRectF(14, 10, 300, 18), Qt.AlignLeft, ("3D  ·  " + self.source).upper() if self.source else "3D")
        if not self.series:
            p.setFont(font(13))
            p.drawText(self.rect(), Qt.AlignCenter, self.note)
            return
        # ground grid, 2 m square
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        for v in np.linspace(-1, 1, 9):
            a = self._proj(np.array([[v, 0, -1], [v, 0, 1], [-1, 0, v], [1, 0, v]]))
            p.drawLine(QPointF(*a[0]), QPointF(*a[1]))
            p.drawLine(QPointF(*a[2]), QPointF(*a[3]))
        for pts, color in self.paths:
            s = self._proj(pts)
            c = QColor(color)
            c.setAlpha(200)
            p.setPen(QPen(c, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for a, b in zip(s, s[1:]):
                if not (np.isnan(a).any() or np.isnan(b).any()):
                    p.drawLine(QPointF(*a), QPointF(*b))
        for pts, color, alpha in self.series:
            s = self._proj(pts)
            draw_skeleton(p, np.column_stack([s, np.where(np.isnan(pts[:, 0]), 0, 1)]),
                          lambda xy: QPointF(*xy), color, alpha, 3.0)


def normalise_3d(p3, address_idx):
    """Make a 3D track viewer-friendly: metres, y up, feet on y=0, mid-hip at origin at address,
    facing the viewer. Works for both stereo (mm, camera frame) and MediaPipe world coords (m)."""
    from .pose import L_AN, L_HIP, L_SH, R_AN, R_HIP, R_SH
    P = p3.astype(float).copy()
    if np.nanmax(np.abs(P)) > 20:  # millimetres
        P /= 1000.0
    a = P[address_idx]
    up = (a[L_SH] + a[R_SH]) / 2 - (a[L_AN] + a[R_AN]) / 2
    side = a[L_HIP] - a[R_HIP]
    if np.isnan(up).any() or np.isnan(side).any():
        return P
    up /= np.linalg.norm(up)
    side = side - up * (side @ up)
    side /= np.linalg.norm(side)
    fwd = np.cross(side, up)
    Rm = np.stack([side, up, fwd])              # rows: new x, y, z axes
    origin = (a[L_HIP] + a[R_HIP]) / 2
    P = (P - origin) @ Rm.T
    feet = np.nanmin(P[address_idx][[L_AN, R_AN], 1])
    P[..., 1] -= feet
    return P
