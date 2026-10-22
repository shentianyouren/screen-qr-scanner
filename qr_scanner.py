# -*- coding: utf-8 -*-
"""
屏幕二维码扫描器 (Screen QR Scanner)
====================================
识别「屏幕上显示的」二维码：截屏 + 本地解码，不需要摄像头、不联网。

- 白蓝配色 / 无边框圆角窗口 / 上下扫描线动画
- 整屏自动识别，失败可拖拽框选区域
- 识别结果一键打开链接、保存为 txt、自动复制到剪贴板

运行: python qr_scanner.py
依赖: PySide6, opencv-python, numpy
"""

import os
import re
import sys
from datetime import datetime

import cv2
import numpy as np
from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect,
                            QRectF, Qt, QStandardPaths, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QGuiApplication,
                           QIcon, QImage, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame,
                               QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

# ---------------------------------------------------------------- 主题色
PRIMARY = "#2F6BFF"
PRIMARY_DEEP = "#1F56E0"
PRIMARY_SOFT = "#EFF4FF"
SUCCESS = "#22C55E"
WARN = "#F59E0B"
DANGER = "#EF4444"
TEXT = "#16233D"
TEXT_SUB = "#7C8CA8"
LINE = "#E4EDFF"

FONT_FAMILY = "Microsoft YaHei UI, Microsoft YaHei, PingFang SC, sans-serif"

APP_NAME = "屏幕二维码扫描器"
APP_VERSION = "1.0.0"


def resource_path(*parts) -> str:
    """兼容「源码运行」与「PyInstaller 打包后」的资源路径"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


# ---------------------------------------------------------------- 图像工具
def qimage_to_rgb_array(qimg: QImage):
    """QImage -> numpy RGB（按行字节对齐修正 stride）"""
    qimg = qimg.convertToFormat(QImage.Format_RGB888)
    w, h, stride = qimg.width(), qimg.height(), qimg.bytesPerLine()
    buf = np.frombuffer(bytes(qimg.constBits()), dtype=np.uint8)
    return buf.reshape(h, stride)[:, : w * 3].reshape(h, w, 3)


def pixmap_to_rgb_array(pm: QPixmap):
    return qimage_to_rgb_array(pm.toImage())


def decode_qr(rgb):
    """多尺度尝试解码，兼顾大屏截图与小图。返回 (文本, 角点, 缩放回原图的系数)"""
    det = cv2.QRCodeDetector()
    if rgb.ndim == 2:
        gray = rgb
    elif rgb.shape[2] == 4:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGBA2GRAY)
    else:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    for scale in (1.0, 0.5, 2.0, 1.5):
        if scale == 1.0:
            g = gray
        else:
            interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
            g = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)
        try:
            data, pts, _ = det.detectAndDecode(g)
        except cv2.error:
            data, pts = "", None
        if data:
            return data, pts, 1.0 / scale
    return "", None, 1.0


def points_bounds(pts, back):
    """把解码角点换算回原图坐标并取外接矩形"""
    if pts is None or len(pts) == 0:
        return None
    arr = np.array(pts, dtype=np.float32).reshape(-1, 2) * back
    x0, y0 = arr.min(axis=0)
    x1, y1 = arr.max(axis=0)
    return QRect(int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0)))


# ---------------------------------------------------------------- 屏幕捕获
class DesktopScanner:
    """整屏抓取 + 二维码识别"""

    @staticmethod
    def virtual_geometry():
        vg = QRect()
        for s in QGuiApplication.screens():
            vg = vg.united(s.geometry())
        return vg

    @classmethod
    def capture(cls):
        screens = QGuiApplication.screens()
        if not screens:
            return None
        vg = cls.virtual_geometry()
        if len(screens) == 1:
            pm = screens[0].grabWindow(0)
            # 统一成 1:1 设备像素，后续裁剪/解码坐标不用再换算 DPR
            return None if pm is None else QPixmap.fromImage(pm.toImage())

        dpr = screens[0].devicePixelRatio()
        canvas = QPixmap(int(vg.width() * dpr), int(vg.height() * dpr))
        canvas.fill(QColor("#000000"))
        canvas.setDevicePixelRatio(dpr)
        p = QPainter(canvas)
        p.scale(dpr, dpr)
        for s in screens:
            pm = s.grabWindow(0)
            if pm is not None and not pm.isNull():
                g = s.geometry()
                p.drawPixmap(QPoint(g.x() - vg.x(), g.y() - vg.y()), pm)
        p.end()
        return QPixmap.fromImage(canvas.toImage()) if not canvas.isNull() else None

    @staticmethod
    def decode(pm: QPixmap):
        """返回 (文本, 二维码外接矩形[像素坐标])"""
        if pm is None or pm.isNull():
            return "", None
        text, pts, back = decode_qr(pixmap_to_rgb_array(pm))
        return text, points_bounds(pts, back)

    @staticmethod
    def is_link(text: str) -> bool:
        if not text:
            return False
        t = text.strip()
        return bool(re.match(r"^(https?://|www\.)", t, re.I))


# ---------------------------------------------------------------- 扫描显示区
class ScanView(QWidget):
    """画面预览 + 取景角标 + 上下扫描线动画。"""

    DEFAULT_PH = ("等待扫描屏幕二维码", "点击「开始扫描」识别屏幕中的二维码")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(300)
        self._pixmap = None
        self._scanning = False
        self._success = False
        self._contain = True
        self._ph = self.DEFAULT_PH
        self._y = 30.0
        self._dir = 1
        self._band = None
        self._band_key = None

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # -- 对外接口
    def set_frame(self, pixmap: QPixmap, contain: bool = True):
        self._pixmap = pixmap
        self._contain = contain
        self.update()

    def start_anim(self):
        self._success = False
        self._scanning = True
        if self._pixmap is None:
            self._ph = ("正在读取屏幕…", "请稍候")
        self._y = 30.0
        self._dir = 1
        self._timer.start()
        self.update()

    def stop_anim(self):
        self._scanning = False
        self._timer.stop()
        if self._pixmap is None:
            self._ph = self.DEFAULT_PH
        self.update()

    def set_success(self, ok: bool):
        self._success = ok
        self.update()

    def clear(self):
        self._pixmap = None
        self._success = False
        self._ph = self.DEFAULT_PH
        self.update()

    # -- 动画推进
    def _tick(self):
        top, bottom = 30.0, self.height() - 30.0
        self._y += 5.0 * self._dir
        if self._y >= bottom:
            self._y, self._dir = bottom, -1
        elif self._y <= top:
            self._y, self._dir = top, 1
        self.update()

    # -- 绘制
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)

        if self._pixmap is None:
            bg = QLinearGradient(r.topLeft(), r.bottomLeft())
            bg.setColorAt(0.0, QColor("#F6F9FF"))
            bg.setColorAt(1.0, QColor("#EDF3FF"))
            p.fillPath(path, bg)
        else:
            p.fillPath(path, QColor("#0B1425"))

        p.save()
        p.setClipPath(path)
        if self._pixmap is not None:
            pm = self._pixmap
            sw, sh = pm.width(), pm.height()
            scale = (min if self._contain else max)(r.width() / sw, r.height() / sh)
            dw, dh = sw * scale, sh * scale
            target = QRectF(r.x() - (dw - r.width()) / 2,
                            r.y() - (dh - r.height()) / 2, dw, dh)
            p.drawPixmap(target, pm, QRectF(0, 0, sw, sh))

        self._draw_placeholder(p, r)
        self._draw_corners(p, r)
        self._draw_scanline(p, r)
        p.restore()

        p.setPen(QPen(QColor(PRIMARY if self._scanning else LINE),
                      1.4 if self._scanning else 1.0))
        p.drawPath(path)

    def _draw_placeholder(self, p, r):
        if self._pixmap is not None:
            return
        cx = r.center().x()
        cy = r.center().y() - 12

        p.setPen(QPen(QColor("#AFC6F2"), 2))
        p.setBrush(Qt.NoBrush)
        body = QRectF(cx - 28, cy - 20, 56, 40)
        p.drawRoundedRect(body, 9, 9)
        for sx, sy, s in ((-19, -13, 7), (7, -13, 7), (-11, 5, 7)):
            p.setBrush(QColor("#C8D9F7"))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(cx + sx, cy + sy, s, s), 2, 2)
            p.setPen(QPen(QColor("#AFC6F2"), 2))
            p.setBrush(Qt.NoBrush)

        p.setPen(QColor("#6C86B4"))
        p.setFont(QFont("Microsoft YaHei UI", 10))
        p.drawText(QRectF(r.x(), cy + 38, r.width(), 22),
                   Qt.AlignHCenter | Qt.AlignTop, self._ph[0])
        p.setPen(QColor("#9AAECB"))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QRectF(r.x(), cy + 62, r.width(), 22),
                   Qt.AlignHCenter | Qt.AlignTop, self._ph[1])

    def _draw_corners(self, p, r):
        color = QColor(SUCCESS if self._success else PRIMARY)
        color.setAlpha(235)
        p.setPen(QPen(color, 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        inset, L = 14.0, 22.0
        x0, y0 = r.left() + inset, r.top() + inset
        x1, y1 = r.right() - inset, r.bottom() - inset
        corners = [
            (QPoint(x0, y0 + L), QPoint(x0, y0), QPoint(x0 + L, y0)),
            (QPoint(x1 - L, y0), QPoint(x1, y0), QPoint(x1, y0 + L)),
            (QPoint(x1, y1 - L), QPoint(x1, y1), QPoint(x1 - L, y1)),
            (QPoint(x0 + L, y1), QPoint(x0, y1), QPoint(x0, y1 - L)),
        ]
        for a, b, c in corners:
            path = QPainterPath(a)
            path.lineTo(b)
            path.lineTo(c)
            p.drawPath(path)

    def _draw_scanline(self, p, r):
        if not self._scanning:
            return
        y = self._y
        left, right = r.left() + 20, r.right() - 20
        width = right - left

        band_h = 54
        band = self._band_pixmap(int(width), band_h)
        if band is not None:
            p.drawPixmap(int(left), int(y - band_h), band)

        glow = QLinearGradient(left, 0, right, 0)
        glow.setColorAt(0.0, QColor(47, 107, 255, 0))
        glow.setColorAt(0.2, QColor(47, 107, 255, 150))
        glow.setColorAt(0.5, QColor(96, 165, 250, 255))
        glow.setColorAt(0.8, QColor(47, 107, 255, 150))
        glow.setColorAt(1.0, QColor(47, 107, 255, 0))
        p.setPen(QPen(glow, 3.0, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QRectF(left, y, width, 0).topLeft(),
                   QRectF(left, y, width, 0).topRight())

    def _band_pixmap(self, w, h):
        """扫描线拖尾：竖直渐隐 + 左右柔化，缓存避免每帧重建。"""
        if w <= 2 or h <= 2:
            return None
        key = (w, h)
        if self._band_key == key:
            return self._band

        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        pp = QPainter(img)
        vg = QLinearGradient(0, 0, 0, h)
        vg.setColorAt(0.0, QColor(47, 107, 255, 0))
        vg.setColorAt(0.75, QColor(47, 107, 255, 46))
        vg.setColorAt(1.0, QColor(47, 107, 255, 92))
        pp.fillRect(0, 0, w, h, vg)

        pp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        hg = QLinearGradient(0, 0, w, 0)
        hg.setColorAt(0.0, QColor(0, 0, 0, 0))
        hg.setColorAt(0.16, QColor(0, 0, 0, 255))
        hg.setColorAt(0.84, QColor(0, 0, 0, 255))
        hg.setColorAt(1.0, QColor(0, 0, 0, 0))
        pp.fillRect(0, 0, w, h, hg)
        pp.end()

        self._band = QPixmap.fromImage(img)
        self._band_key = key
        return self._band


# ---------------------------------------------------------------- 屏幕框选浮层
class RegionSelector(QWidget):
    """显示冻结的桌面截图，拖拽框选二维码区域。"""

    selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, shot: QPixmap):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.shot = shot
        self._start = None
        self._cur = None
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)

        vg = DesktopScanner.virtual_geometry()
        self.setGeometry(vg)
        self._sx = shot.width() / max(1, vg.width())
        self._sy = shot.height() / max(1, vg.height())

    # -- 绘制
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        full = QRectF(self.rect())
        p.drawPixmap(full, self.shot, QRectF(self.shot.rect()))

        dim = QColor(10, 20, 40, 150)
        p.fillRect(full, dim)

        sel = self._selection()
        if sel is not None and sel.width() > 0 and sel.height() > 0:
            p.save()
            p.setClipRect(sel)
            p.drawPixmap(full, self.shot, QRectF(self.shot.rect()))
            p.restore()
            p.setPen(QPen(QColor(PRIMARY), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(sel.adjusted(0, 0, -1, -1))

            label = f"{int(sel.width() * self._sx)} × {int(sel.height() * self._sy)}"
            p.setFont(QFont("Microsoft YaHei UI", 9))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(label) + 16
            box = QRectF(sel.left(), max(4, sel.top() - 26), tw, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(47, 107, 255, 230))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(box, Qt.AlignCenter, label)

        # 顶部提示
        hint = "拖拽框选二维码所在区域    ·    Esc 取消"
        p.setFont(QFont("Microsoft YaHei UI", 10))
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(hint) + 40
        hr = QRectF((self.width() - w) / 2, 26, w, 38)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(16, 28, 48, 215))
        p.drawRoundedRect(hr, 12, 12)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(hr, Qt.AlignCenter, hint)

    def _selection(self):
        if self._start is None or self._cur is None:
            return None
        return QRect(self._start, self._cur).normalized()

    # -- 交互
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start = e.position().toPoint()
            self._cur = self._start
            self.update()

    def mouseMoveEvent(self, e):
        if self._start is not None:
            self._cur = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._start is None:
            return
        sel = self._selection()
        self._start = self._cur = None
        if sel is None or sel.width() < 12 or sel.height() < 12:
            self.update()
            return
        px = QRect(int(sel.x() * self._sx), int(sel.y() * self._sy),
                   int(sel.width() * self._sx), int(sel.height() * self._sy))
        px = px.intersected(self.shot.rect())
        self.hide()
        self.selected.emit(px)
        self.close()

    def mouseDoubleClickEvent(self, e):
        self.hide()
        self.selected.emit(self.shot.rect())
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            self.cancelled.emit()
            self.close()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.hide()
            self.selected.emit(self.shot.rect())
            self.close()
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------- 小部件
class Dot(QWidget):
    """状态圆点。"""

    def __init__(self, color=PRIMARY, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(9, 9)

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        glow = QColor(self._color)
        glow.setAlpha(60)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(0, 0, 9, 9)
        p.setBrush(self._color)
        p.drawEllipse(2, 2, 5, 5)


class Logo(QWidget):
    """标题栏小图标：蓝底 + 定位方块。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(PRIMARY))
        p.drawRoundedRect(QRectF(0, 0, 26, 26), 8, 8)
        p.setBrush(QColor("#FFFFFF"))
        for x, y in ((6, 6), (15, 6), (6, 15), (15, 15)):
            p.drawRoundedRect(QRectF(x, y, 5, 5), 1.2, 1.2)


class WinButton(QPushButton):
    """标题栏最小化 / 关闭按钮。"""

    def __init__(self, text, danger=False, parent=None):
        super().__init__(text, parent)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        hover = "#FFEAEA" if danger else "#EEF3FF"
        press = "#FFD9D9" if danger else "#E2EBFF"
        color = "#E25656" if danger else "#6B7C99"
        self.setStyleSheet(f"""
            QPushButton {{
                border: none; border-radius: 15px; color: {color};
                background: transparent; font-size: 14px; font-family: {FONT_FAMILY};
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {press}; }}
        """)


class TitleBar(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win = window
        self._drag = None
        self.setFixedHeight(54)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 0, 14, 0)
        lay.setSpacing(10)
        lay.addWidget(Logo())

        title = QLabel("屏幕二维码扫描")
        title.setStyleSheet(
            f"color:{TEXT}; font-size:14.5px; font-weight:600; font-family:{FONT_FAMILY};")
        lay.addWidget(title)
        lay.addStretch(1)

        mini = WinButton("—")
        mini.clicked.connect(window.showMinimized)
        close = WinButton("✕", danger=True)
        close.clicked.connect(window.close)
        lay.addWidget(mini)
        lay.addWidget(close)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self._win.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None


# ---------------------------------------------------------------- 主窗口
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("屏幕二维码扫描")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(470, 690)

        self._text = ""
        self._shot = None
        self._selector = None
        self._busy = False
        # 避免 QSS 继承让 QLabel 带上不透明白底（浅色卡片上会露出色块边缘）
        self.setStyleSheet("QLabel { background: transparent; }")
        self._build_ui()
        self._animate_in()

    # ---------- 界面搭建
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(0)

        card = QWidget()
        card.setObjectName("Card")
        card.setStyleSheet("#Card { background: #FFFFFF; border-radius: 26px; }")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(46)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(24, 58, 128, 70))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(TitleBar(self))

        body = QVBoxLayout()
        body.setContentsMargins(22, 4, 22, 22)
        body.setSpacing(13)

        self.scan = ScanView()
        body.addWidget(self.scan, 1)

        # 状态行
        status_row = QHBoxLayout()
        status_row.setContentsMargins(4, 0, 4, 0)
        status_row.setSpacing(7)
        self.dot = Dot(TEXT_SUB)
        status_row.addWidget(self.dot)
        self.status = QLabel("准备就绪")
        self.status.setStyleSheet(
            f"color:{TEXT_SUB}; font-size:12px; font-family:{FONT_FAMILY};")
        status_row.addWidget(self.status)
        status_row.addStretch(1)
        self.hint = QLabel("Ctrl+V 可粘贴截图")
        self.hint.setStyleSheet(
            f"color:#B3C1D6; font-size:11.5px; font-family:{FONT_FAMILY};")
        status_row.addWidget(self.hint)
        body.addLayout(status_row)

        # 结果框
        box = QFrame()
        box.setObjectName("ResultBox")
        box.setStyleSheet(
            "#ResultBox { background: #F7FAFF; border: 1px solid #E7EFFF; border-radius: 16px; }")
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(16, 11, 12, 13)
        box_lay.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(6)
        cap = QLabel("识别结果")
        cap.setStyleSheet(
            f"color:{TEXT_SUB}; font-size:11.5px; letter-spacing:0.5px; font-family:{FONT_FAMILY};")
        head.addWidget(cap)
        head.addStretch(1)
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setEnabled(False)
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                border:none; background:transparent; color:{PRIMARY};
                font-size:11.5px; padding:2px 6px; font-family:{FONT_FAMILY};
            }}
            QPushButton:hover {{ color:{PRIMARY_DEEP}; }}
            QPushButton:disabled {{ color:#C3D0E4; }}
        """)
        self.copy_btn.clicked.connect(self._copy)
        head.addWidget(self.copy_btn)
        box_lay.addLayout(head)

        self.result = QLabel("等待识别…")
        self.result.setWordWrap(True)
        self.result.setFixedHeight(46)
        self.result.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.result.setStyleSheet(
            f"color:#A9B8CE; font-size:13px; font-family:{FONT_FAMILY};")
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box_lay.addWidget(self.result)

        body.addWidget(box)

        # 底部按钮：左 开始扫描 / 中 打开链接 / 右 保存
        btns = QHBoxLayout()
        btns.setSpacing(10)

        self.start_btn = QPushButton("开始扫描")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setFixedHeight(48)
        self.start_btn.setStyleSheet(f"""
            QPushButton {{
                background: {PRIMARY}; color: #FFFFFF; border: none; border-radius: 14px;
                font-size: 14px; font-weight: 600; font-family: {FONT_FAMILY};
            }}
            QPushButton:hover {{ background: {PRIMARY_DEEP}; }}
            QPushButton:pressed {{ background: #1949C4; }}
        """)
        self.start_btn.clicked.connect(self._start_scan)
        btns.addWidget(self.start_btn, 1)

        self.open_btn = QPushButton("打开链接")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.setFixedSize(112, 48)
        self.open_btn.setEnabled(False)
        self.open_btn.setToolTip("在新窗口打开二维码中的网页")
        self.open_btn.setStyleSheet(f"""
            QPushButton {{
                background: {PRIMARY_SOFT}; color: {PRIMARY}; border: 1px solid #D9E5FF;
                border-radius: 14px; font-size: 13.5px; font-weight: 600;
                font-family: {FONT_FAMILY};
            }}
            QPushButton:hover {{ background: #E3ECFF; border-color: #C9DAFF; }}
            QPushButton:pressed {{ background: #D6E3FF; }}
            QPushButton:disabled {{
                background: #F5F8FE; color: #C0CEE2; border-color: #EAF0FB;
            }}
        """)
        self.open_btn.clicked.connect(self._open_link)
        btns.addWidget(self.open_btn)

        self.save_btn = QPushButton("保存")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setFixedHeight(48)
        self.save_btn.setEnabled(False)
        self.save_btn.setStyleSheet(f"""
            QPushButton {{
                background: #FFFFFF; color: {PRIMARY}; border: 1px solid #D9E5FF;
                border-radius: 14px; font-size: 13.5px; font-weight: 600;
                font-family: {FONT_FAMILY};
            }}
            QPushButton:hover {{ background: #F5F9FF; border-color: #C9DAFF; }}
            QPushButton:pressed {{ background: #EAF1FF; }}
            QPushButton:disabled {{
                background: #FAFCFF; color: #C0CEE2; border-color: #EDF3FC;
            }}
        """)
        self.save_btn.clicked.connect(self._save)
        btns.addWidget(self.save_btn, 1)

        body.addLayout(btns)
        root.addLayout(body, 1)

        self.paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        self.paste_shortcut.activated.connect(self._paste_image)

    def _animate_in(self):
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self._anim = anim

    # ---------- 状态
    def _set_status(self, text, color=TEXT_SUB, dot=None):
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{color}; font-size:12px; font-family:{FONT_FAMILY};")
        self.dot.set_color(dot or color)

    def _set_busy(self, busy):
        self._busy = busy
        self.start_btn.setText("扫描中…" if busy else "开始扫描")
        self.start_btn.setEnabled(not busy)

    # ---------- 扫描流程
    def _start_scan(self):
        if self._busy:
            return
        self._text = ""
        self.setWindowTitle("屏幕二维码扫描")
        self.result.setText("正在读取屏幕…")
        self.result.setStyleSheet(
            f"color:#A9B8CE; font-size:13px; font-family:{FONT_FAMILY};")
        self.save_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.scan.clear()
        self._set_busy(True)
        self._set_status("正在截取屏幕…", WARN, WARN)

        # 先隐藏自身，确保截图里没有本窗口
        self.hide()
        QTimer.singleShot(170, self._capture_then_decode)

    def _capture_then_decode(self):
        shot = DesktopScanner.capture()
        if shot is None or shot.isNull():
            self.show()
            self._set_busy(False)
            self._set_status("无法读取屏幕，请检查系统权限", DANGER, DANGER)
            return

        self._shot = shot
        text, rect = DesktopScanner.decode(shot)
        if text:
            self.show()
            self._present(shot, rect, text)
            return

        # 全屏没找到 -> 让用户框选区域
        self._set_status("未自动识别，请框选二维码所在区域", WARN, WARN)
        self._ask_region(shot)

    def _ask_region(self, shot):
        self._selector = RegionSelector(shot)
        self._selector.selected.connect(self._on_region_selected)
        self._selector.cancelled.connect(self._on_region_cancelled)
        self._selector.show()
        self._selector.raise_()
        self._selector.activateWindow()
        self._selector.setFocus()

    def _close_selector(self):
        if self._selector is not None:
            sel = self._selector
            self._selector = None
            sel.hide()
            sel.close()

    def _on_region_cancelled(self):
        self._close_selector()
        self._set_busy(False)
        self.show()
        self.raise_()
        self.activateWindow()
        self._set_status("已取消扫描", TEXT_SUB, "#B7C4D6")

    def _on_region_selected(self, rect: QRect):
        shot = self._shot
        self._close_selector()
        self.show()
        self.raise_()
        self.activateWindow()
        if shot is None or rect.isEmpty():
            self._set_busy(False)
            return

        crop = shot.copy(rect)
        text, box = DesktopScanner.decode(crop)
        if text:
            self._present(crop, box, text)
        else:
            self.scan.set_frame(crop, contain=True)
            self.scan.set_success(False)
            self._set_busy(False)
            self.result.setText("未识别到二维码")
            self._set_status("该区域未识别到二维码，请重试", DANGER, DANGER)

    def _present(self, pixmap: QPixmap, box: QRect, text: str):
        """展示截图 + 扫描动画，短暂停顿后给出结果"""
        view = pixmap
        if box is not None and not box.isEmpty():
            view = pixmap.copy(box.adjusted(-24, -24, 24, 24).intersected(pixmap.rect()))
        self.scan.set_frame(view, contain=True)
        self.scan.start_anim()
        self._set_status("正在识别二维码…", PRIMARY, PRIMARY)
        QTimer.singleShot(450, lambda: self._finish_ok(text))

    def _finish_ok(self, text):
        self.scan.stop_anim()
        self._set_busy(False)
        self.start_btn.setText("重新扫描")
        self._accept_result(text)

    def _accept_result(self, text):
        self._text = text
        pretty = text if len(text) <= 300 else text[:300] + " …"
        self.result.setText(pretty)
        self.result.setStyleSheet(
            f"color:{TEXT}; font-size:13px; font-family:{FONT_FAMILY};")
        self.result.setToolTip(text)
        self.scan.set_success(True)
        self.save_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        self.open_btn.setEnabled(DesktopScanner.is_link(text))
        if DesktopScanner.is_link(text):
            self._set_status("识别成功，点「打开链接」即可跳转", SUCCESS, SUCCESS)
        else:
            self._set_status("识别成功（内容不是网址）", SUCCESS, SUCCESS)
        QApplication.clipboard().setText(text)
        self.setWindowTitle("屏幕二维码扫描 · 已识别")

    # ---------- 结果操作
    def _paste_image(self):
        qimg = QApplication.clipboard().image()
        if qimg is None or qimg.isNull():
            self._set_status("剪贴板中没有图片，可先截图再粘贴", WARN, WARN)
            return
        pm = QPixmap.fromImage(qimg)
        text, box = DesktopScanner.decode(pm)
        if text:
            self.scan.stop_anim()
            self.scan.set_frame(pm, contain=True)
            self._accept_result(text)
        else:
            self.scan.set_frame(pm, contain=True)
            self.scan.set_success(False)
            self._set_status("图片中未识别到二维码", DANGER, DANGER)

    def _copy(self):
        if self._text:
            QApplication.clipboard().setText(self._text)
            self._set_status("已复制到剪贴板", SUCCESS, SUCCESS)

    def _open_link(self):
        url = self._text.strip()
        if not url:
            return
        if not re.match(r"^https?://", url, re.I):
            if url.lower().startswith("www."):
                url = "http://" + url
            else:
                self._set_status("该内容不是网页链接", WARN, WARN)
                return
        QDesktopServices.openUrl(QUrl(url))
        self._set_status("已在浏览器中打开", SUCCESS, SUCCESS)

    def _save(self):
        if not self._text:
            return
        base = (QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
                or os.path.expanduser("~"))
        name = f"二维码_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存识别结果", os.path.join(base, name),
            "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".txt"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._text + "\n")
            self._set_status(f"已保存：{os.path.basename(path)}", SUCCESS, SUCCESS)
        except Exception as e:
            self._set_status(f"保存失败：{e}", DANGER, DANGER)

    # ---------- 收尾
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self._close_selector()
        super().closeEvent(e)


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setFont(QFont("Microsoft YaHei UI", 10))

    icon = resource_path("assets", "icon.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
