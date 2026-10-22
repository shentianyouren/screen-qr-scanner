# -*- coding: utf-8 -*-
"""生成应用图标：assets/icon.png（256px）与多尺寸 assets/icon.ico。

用法：python scripts/make_icon.py
"""

import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 256.0, size / 256.0)

    path = QPainterPath()
    path.addRoundedRect(QRectF(10, 10, 236, 236), 54, 54)
    g = QLinearGradient(10, 10, 246, 246)
    g.setColorAt(0.0, QColor("#4A80FF"))
    g.setColorAt(1.0, QColor("#1B4FE0"))
    p.fillPath(path, g)
    p.setPen(Qt.NoPen)

    ink = QPainterPath()

    def finder(x, y, s=56):
        outer = QPainterPath()
        outer.addRoundedRect(QRectF(x, y, s, s), 15, 15)
        inner = QPainterPath()
        inner.addRoundedRect(QRectF(x + 14, y + 14, s - 28, s - 28), 7, 7)
        ink.addPath(outer.subtracted(inner))

    finder(44, 44)
    finder(156, 44)
    finder(44, 156)
    for x, y in ((156, 156), (184, 156), (212, 156), (156, 184),
                 (212, 184), (156, 212), (184, 212), (212, 212)):
        block = QPainterPath()
        block.addRoundedRect(QRectF(x, y, 18, 18), 5, 5)
        ink.addPath(block)

    p.fillPath(ink, QColor(255, 255, 255, 246))
    p.end()
    return img


def png_bytes(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data())


def main():
    os.makedirs(ASSETS, exist_ok=True)
    _ = QApplication(sys.argv)          # QPixmap/QImage 需要 GUI 应用实例

    images = [(s, png_bytes(draw(s))) for s in SIZES]
    draw(256).save(os.path.join(ASSETS, "icon.png"))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in images:
        w = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)

    out = os.path.join(ASSETS, "icon.ico")
    with open(out, "wb") as f:
        f.write(header + entries + blobs)
    print(f"saved {out} ({len(images)} sizes)")


if __name__ == "__main__":
    main()
