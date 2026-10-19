"""Dark theme: palette, stylesheet and a few colour helpers."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from .. import categories


class Palette:
    bg = QColor("#14161b")
    panel = QColor("#1b1e25")
    sidebar = QColor("#101216")
    text = QColor("#e8e9ed")
    text_muted = QColor("#9096a3")
    accent = QColor("#5b8cff")
    border = QColor("#2a2e38")
    border_hover = QColor("#5a6070")


_PAL = Palette()


def current() -> Palette:
    return _PAL


def category_gradient(category: str) -> tuple[QColor, QColor]:
    c1, c2 = categories.get(category).colors
    return QColor(c1), QColor(c2)


def rating_color(rating: float | None, rating_max: float | None) -> QColor:
    if rating is None:
        return QColor("#888")
    pct = rating / (rating_max or 10)
    if pct >= 0.75:
        return QColor("#6fd36f")
    if pct >= 0.5:
        return QColor("#f2c94c")
    return QColor("#f26b5b")


STYLESHEET = """
QMainWindow, QDialog { background: #14161b; }
QWidget { color: #e8e9ed; font-size: 10pt; }
QToolTip { background: #1b1e25; color: #e8e9ed; border: 1px solid #2a2e38; }

#Sidebar { background: #101216; border: none; padding-top: 8px; }
#Sidebar::item { height: 32px; padding-left: 6px; border-radius: 6px; margin: 1px 8px; }
#Sidebar::item:hover { background: #1d2029; }
#Sidebar::item:selected { background: #24324f; color: #ffffff; }
#Sidebar::branch { background: transparent; }

#Grid { background: #14161b; border: none; }
#Detail { background: #1b1e25; border-left: 1px solid #2a2e38; }
#DetailTitle { font-size: 16pt; font-weight: 600; }
#Muted { color: #9096a3; }
#Big { font-size: 13pt; font-weight: 600; }
#ItemPage { background: #14161b; }
QTableWidget#Episodes { background: #1b1e25; border: 1px solid #2a2e38; border-radius: 8px; }
QTableWidget#Episodes::item { padding: 2px 6px; }
#BulkBar { background: #1d2a45; border: 1px solid #3b6ef5; border-radius: 8px; }
#LogView { font-family: Consolas, monospace; font-size: 9pt; background: #101216; }
#PageTitle { font-size: 24pt; font-weight: 700; }
#Section { color: #6f7685; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px; padding-top: 8px; }
#Chip { background: #1b1e25; border: 1px solid #2a2e38; border-radius: 10px; }
#ChipValue { font-size: 15pt; font-weight: 700; }
QPushButton#Done { padding: 8px 18px; font-weight: 600; }
QPushButton#Done:checked { background: #1f7a4d; border-color: #2a9d63; color: white; }
QCheckBox::indicator { width: 16px; height: 16px; }

QLineEdit, QComboBox, QSpinBox {
    background: #1b1e25; border: 1px solid #2a2e38; border-radius: 6px; padding: 5px 8px;
}
QLineEdit:focus, QComboBox:focus { border-color: #5b8cff; }
QComboBox QAbstractItemView { background: #1b1e25; selection-background-color: #24324f; }
QPushButton, QToolButton {
    background: #232733; border: 1px solid #2a2e38; border-radius: 6px; padding: 6px 12px;
}
QPushButton:hover, QToolButton:hover { background: #2b3040; }
QPushButton#Primary { background: #3b6ef5; border-color: #3b6ef5; color: white; }
QPushButton#Primary:hover { background: #5b8cff; }
QToolBar { background: #14161b; border: none; border-bottom: 1px solid #2a2e38; spacing: 6px; padding: 6px; }
QStatusBar { background: #101216; color: #9096a3; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #2f3442; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QTableWidget { background: #1b1e25; gridline-color: #2a2e38; border: 1px solid #2a2e38; }
QHeaderView::section { background: #232733; border: none; padding: 6px; }
QMenu { background: #1b1e25; border: 1px solid #2a2e38; }
QMenu::item { padding: 6px 24px; }
QMenu::item:selected { background: #24324f; }
QSplitter::handle { background: #2a2e38; }
"""


def draw_icon(size: int = 256) -> QPixmap:
    """App icon: three poster cards in the category colours on a dark tile."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    bg = QLinearGradient(0, 0, size, size)
    bg.setColorAt(0, QColor("#2a3550"))
    bg.setColorAt(1, QColor("#12151c"))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)
    cards = [("anime", -14, 62, 70), ("movie", 0, 128, 58), ("game", 14, 194, 70)]  # icon art
    for cat, angle, cx, top in cards:
        c1, c2 = category_gradient(cat)
        g = QLinearGradient(0, 0, 0, 120 * s)
        g.setColorAt(0, c1.lighter(115))
        g.setColorAt(1, c1)
        p.save()
        p.translate(cx * s, (top + 60) * s)
        p.rotate(angle)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(-30 * s, -60 * s, 60 * s, 120 * s), 9 * s, 9 * s)
        p.restore()
    # Check mark: "tracking" what you've finished.
    pen = QPen(QColor("white"), 14 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline([QPointF(96 * s, 196 * s), QPointF(122 * s, 220 * s), QPointF(170 * s, 166 * s)])
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(draw_icon(size))
    return icon


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, _PAL.bg)
    pal.setColor(QPalette.Base, _PAL.panel)
    pal.setColor(QPalette.AlternateBase, _PAL.bg)
    pal.setColor(QPalette.Text, _PAL.text)
    pal.setColor(QPalette.WindowText, _PAL.text)
    pal.setColor(QPalette.Button, QColor("#232733"))
    pal.setColor(QPalette.ButtonText, _PAL.text)
    pal.setColor(QPalette.Highlight, _PAL.accent)
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.PlaceholderText, _PAL.text_muted)
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
