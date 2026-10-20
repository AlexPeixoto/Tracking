"""Poster grid: list model + a delegate that paints each item as a card."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .. import db, paths
from .. import scores as score_math
from . import theme

CARD_W, POSTER_H, TEXT_H = 168, 240, 58
ItemRole = Qt.UserRole + 1


def fmt_hours(hours: float | None, category: str) -> str:
    if not hours:
        return ""
    if category == "game":
        return f"{hours:.1f}h main" if hours < 10 else f"{hours:.0f}h main"
    h, m = divmod(round(hours * 60), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_played(minutes: float | None) -> str:
    """Steam play time: "45m", "6.5h", "120h"."""
    if minutes is None:
        return ""
    if minutes < 60:
        return f"{minutes:.0f}m"
    hours = minutes / 60
    return f"{hours:.1f}h" if hours < 10 else f"{hours:.0f}h"


def fmt_rating(item: db.Item) -> str:
    if item.rating is None:
        return ""
    if item.rating_max == 100:
        return f"{item.rating:.0f}"
    return f"{item.rating:.1f}"


class PosterCache:
    def __init__(self):
        self._cache: dict[tuple[str, int], QPixmap] = {}

    def get(self, name: str | None, w: int, h: int) -> QPixmap | None:
        if not name:
            return None
        key = (name, w)
        pm = self._cache.get(key)
        if pm is None:
            src = QPixmap(str(paths.posters_dir() / name))
            if src.isNull():
                return None
            # Fill the card (crop overflow) like a poster wall would.
            pm = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self._cache[key] = pm
        return pm

    def placeholder(self, item: db.Item, w: int, h: int) -> QPixmap:
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        p.setClipPath(path)
        draw_placeholder(p, QRect(0, 0, w, h), item)
        p.end()
        return pm

    def invalidate(self, name: str | None = None) -> None:
        if name is None:
            self._cache.clear()
        else:
            for k in [k for k in self._cache if k[0] == name]:
                del self._cache[k]


def draw_placeholder(p: QPainter, rect: QRect, item: db.Item) -> None:
    """Category-coloured gradient with the title's initials, for items without art."""
    c1, c2 = theme.category_gradient(item.category)
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0, c1)
    g.setColorAt(1, c2)
    p.fillRect(rect, g)
    initials = "".join(w[0] for w in item.title.split()[:2] if w[:1].isalnum()).upper() or "?"
    f = p.font()
    f.setPointSize(max(18, rect.width() // 6))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 200))
    p.drawText(rect, Qt.AlignCenter, initials)


posters = PosterCache()

# How a disconnected drive shows on cards (setting "offline_style").
OFFLINE_TEXT, OFFLINE_ICON, OFFLINE_NONE = "text", "icon", "none"
OFFLINE_STYLES = [(OFFLINE_TEXT, "Text over the cover (dimmed)"),
                  (OFFLINE_ICON, "Small icon, bottom-right"),
                  (OFFLINE_NONE, "Nothing")]


class ItemModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items: list[db.Item] = []
        self.offline_volumes: set[str] = set()
        self.offline_style = OFFLINE_TEXT  # Settings > General > "Disconnected drives"

    def set_items(self, items: list[db.Item]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        if role == ItemRole:
            return item
        if role == Qt.DisplayRole:
            return item.title
        if role == Qt.ToolTipRole:
            return f"{item.title}\n{item.rel_path}"
        return None

    def row_of(self, item_id: int) -> int:
        for i, it in enumerate(self.items):
            if it.id == item_id:
                return i
        return -1

    def replace_item(self, item: db.Item) -> None:
        row = self.row_of(item.id)
        if row >= 0:
            # Same file name may hold a re-downloaded image, so always drop the cache.
            posters.invalidate(self.items[row].poster)
            posters.invalidate(item.poster)
            self.items[row] = item
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)


class CardDelegate(QStyledItemDelegate):
    def __init__(self, model: ItemModel, parent=None):
        super().__init__(parent)
        self.model = model

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_W + 16, POSTER_H + TEXT_H + 16)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item: db.Item = index.data(ItemRole)
        if item is None:
            return
        pal = theme.current()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        r = option.rect.adjusted(8, 8, -8, -8)
        poster_rect = QRect(r.x(), r.y(), CARD_W, POSTER_H)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        offline = item.volume_uid in self.model.offline_volumes and not item.missing
        style = self.model.offline_style
        # Deleted files always show; a disconnected drive shows the way Settings says.
        dimmed = item.missing or (offline and style == OFFLINE_TEXT)
        offline_icon = offline and style == OFFLINE_ICON

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(poster_rect), 10, 10)
        p.setClipPath(clip)
        pm = posters.get(item.poster, CARD_W, POSTER_H)
        if pm:
            sx = (pm.width() - CARD_W) // 2
            sy = (pm.height() - POSTER_H) // 2
            p.drawPixmap(poster_rect, pm, QRect(sx, sy, CARD_W, POSTER_H))
        else:
            draw_placeholder(p, poster_rect, item)
        if dimmed:
            p.fillRect(poster_rect, QColor(0, 0, 0, 140))
        p.setClipping(False)

        if item.scores:
            # Keep the bottom-right corner free for the offline icon.
            self._score_chips(p, option, poster_rect, item, reserve=30 if offline_icon else 0)
        if offline_icon:
            self._offline_icon(p, poster_rect)

        if item.completed:
            badge = QRectF(poster_rect.x() + 8, poster_rect.y() + 8, 26, 26)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2a9d63"))
            p.drawEllipse(badge)
            f = QFont(option.font)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("white"))
            p.drawText(badge, Qt.AlignCenter, "✓")

        if dimmed:
            p.setFont(option.font)
            p.setPen(QColor("#eee"))
            p.drawText(poster_rect, Qt.AlignCenter, "missing" if item.missing else "drive offline")

        # Selection / hover outline
        if selected or hovered:
            pen = p.pen()
            pen.setColor(pal.accent if selected else pal.border_hover)
            pen.setWidthF(3 if selected else 1.5)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(poster_rect).adjusted(1, 1, -1, -1), 10, 10)

        # Title (two lines max) + meta line
        text_rect = QRect(r.x(), poster_rect.bottom() + 6, CARD_W, TEXT_H - 6)
        f = QFont(option.font)
        f.setBold(True)
        p.setFont(f)
        p.setPen(pal.text)
        fm = p.fontMetrics()
        title = item.title + (f" ({item.year})" if item.year and item.category == "movie" else "")
        lines = self._wrap(title, fm, CARD_W)
        y = text_rect.y()
        for ln in lines:
            p.drawText(QRect(text_rect.x(), y, CARD_W, fm.height()), Qt.AlignLeft, ln)
            y += fm.height()

        meta = [fmt_hours(item.total_hours, item.category)]
        if item.category == "game":
            # "PS2", "PC · installer", plus time played (Steam, or games started from here)
            where = item.platform or ""
            if item.install_state == "installer":
                where = f"{where} · installer".strip(" ·")
            if item.playtime_min:
                meta.append(f"{fmt_played(item.playtime_min)} played")
            if where and item.source != "steam":
                meta.insert(0, where)
        elif item.playtime_min is not None:
            meta.append(f"{fmt_played(item.playtime_min)} played")
        elif item.kind == "movie" and item.category in ("anime", "unknown"):
            meta.insert(0, "Movie")
        elif item.video_count > 1:
            # "Ep 7 / 24" once you've started it from here
            meta.insert(0, f"Ep {item.last_episode} / {item.video_count}" if item.last_episode
                        else f"{item.video_count} eps")
        if item.avg_score is not None:
            meta.insert(0, f"★ {item.avg_score / 10:.1f}")  # average of all scores, out of 10
        p.setFont(option.font)
        p.setPen(pal.text_muted)
        p.drawText(QRect(text_rect.x(), y + 1, CARD_W, fm.height()), Qt.AlignLeft,
                   "  ·  ".join(m for m in meta if m))
        p.restore()

    @staticmethod
    def _offline_icon(p: QPainter, poster: QRect) -> None:
        """Small "drive not connected" badge in the poster's bottom-right corner."""
        size = 24
        r = QRectF(poster.right() - size - 6, poster.bottom() - size - 6, size, size)
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 22, 27, 220))
        p.drawEllipse(r)
        # A little unplugged disk: drive body plus a slash.
        pen = QPen(QColor("#e0e3ea"), 1.6)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        body = QRectF(r.x() + 6, r.y() + 8, size - 12, size - 15)
        p.drawRoundedRect(body, 2, 2)
        p.drawPoint(QPointF(body.right() - 2.5, body.center().y()))
        pen.setColor(QColor("#f26b5b"))
        pen.setWidthF(2)
        p.setPen(pen)
        p.drawLine(QPointF(r.x() + 6, r.bottom() - 6), QPointF(r.right() - 6, r.y() + 6))
        p.restore()

    @staticmethod
    def _score_chips(p: QPainter, option, poster: QRect, item: db.Item, reserve: int = 0) -> None:
        """Every score as a small coloured chip along the bottom of the poster
        (best-known first), wrapping onto a second row if needed."""
        f = QFont(option.font)
        f.setBold(True)
        f.setPointSizeF(option.font.pointSizeF() * 0.8)
        p.setFont(f)
        fm = p.fontMetrics()
        h, gap, pad = fm.height() + 4, 4, 6
        chips = [(f"{score_math.SHORT.get(s.source, s.source)} {score_math.fmt(s)}", s)
                 for s in item.scores]
        rows: list[list] = [[]]
        width = 0
        for text, s in chips:
            w = fm.horizontalAdvance(text) + 2 * pad
            if rows[-1] and width + w > poster.width() - 2 * pad - reserve:
                if len(rows) == 2:
                    break
                rows.append([])
                width = 0
            rows[-1].append((text, s, w))
            width += w + gap
        # Darken the bottom of the poster so chips read on any artwork.
        band = len(rows) * (h + gap) + 2 * gap
        shade = QLinearGradient(0, poster.bottom() - band - 16, 0, poster.bottom())
        shade.setColorAt(0, QColor(0, 0, 0, 0))
        shade.setColorAt(1, QColor(0, 0, 0, 200))
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(poster), 10, 10)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRect(poster.x(), poster.bottom() - band - 16, poster.width(), band + 17), shade)
        p.restore()
        y = poster.bottom() - band + gap
        for row in rows:
            x = poster.x() + pad
            for text, s, w in row:
                chip = QRectF(x, y, w, h)
                p.setPen(Qt.NoPen)
                p.setBrush(theme.rating_color(s.value, s.max_value))
                p.drawRoundedRect(chip, 5, 5)
                p.setPen(QColor("#111"))
                p.drawText(chip, Qt.AlignCenter, text)
                x += w + gap
            y += h + gap

    @staticmethod
    def _wrap(text: str, fm, width: int) -> list[str]:
        """Greedy first line, everything else elided onto a second line."""
        words, first, i = text.split(), "", 0
        while i < len(words):
            trial = f"{first} {words[i]}".strip()
            if first and fm.horizontalAdvance(trial) > width:
                break
            first, i = trial, i + 1
        lines = [fm.elidedText(first, Qt.ElideRight, width)]
        rest = " ".join(words[i:])
        if rest:
            lines.append(fm.elidedText(rest, Qt.ElideRight, width))
        return lines
