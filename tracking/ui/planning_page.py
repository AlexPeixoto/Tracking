"""Planning: a board of what's next, what you're on, what's parked, and what you finished
this year - games, anime, movies and TV together, or one section at a time.

  Up next     your queue, in your order (drag to reorder)
  Playing     what you're on; Play / playing an episode moves things here by itself
  On hold     started, parked for later
  Done        this year's Completed log; dropping a card here marks it completed

Dropped items (right-click › Drop) leave the board and the Backlog without counting
as completed; the footer lists them.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import categories, completions, db, library
from .grid import cover_pixmap, fmt_hours, fmt_played

THUMB = QSize(54, 76)
MIME = "application/x-tracking-plan"
IdRole = Qt.UserRole + 1          # item id (None for Done entries outside the library)
DONE = "done"
COLUMNS = [(library.PLAN_NEXT, "Up next"), (library.PLAN_PLAYING, "Playing / Watching"),
           (library.PLAN_HOLD, "On hold"), (DONE, "Done this year")]
SETTING = "plan_sections"
FILTERS = [("", "All"), ("game", "Games"), ("anime", "Anime"), ("movie", "Movies"), ("tv", "TV")]


def remaining_hours(item: db.Item) -> float:
    """Roughly how long is left: HLTB minus time played, episodes not played yet, runtime."""
    if item.category == "game":
        hltb = item.hltb_hours or 0
        return max(hltb - (item.playtime_min or 0) / 60, 0) if hltb else 0
    total = item.total_hours or 0
    if item.kind != "movie" and item.video_count and item.last_episode:
        left = max(item.video_count - item.last_episode, 0)
        return total * left / item.video_count
    return total


def progress(item: db.Item) -> str:
    """"12h of ~60h", "Ep 7 / 24", "2h 10m", "not owned yet"."""
    if item.source == db.WANTED:
        return "not owned yet"
    if item.category == "game":
        played, hltb = item.playtime_min or 0, item.hltb_hours
        if played and hltb:
            return f"{fmt_played(played)} of ~{hltb:.0f}h"
        if played:
            return f"{fmt_played(played)} played"
        return f"~{hltb:.0f}h" if hltb else (item.platform or "")
    if item.kind != "movie" and item.video_count > 1:
        if item.last_episode:
            return f"Ep {item.last_episode} / {item.video_count}"
        return f"{item.video_count} eps · {fmt_hours(item.total_hours, item.category)}"
    return fmt_hours(item.total_hours, item.category)


def _thumb(poster: str | None, title: str, category: str) -> QIcon:
    pm = cover_pixmap(poster, THUMB.width(), THUMB.height())
    if pm is not None:
        return QIcon(pm)
    pm = QPixmap(THUMB)
    pm.fill(QColor(categories.get(category).colors[0]))
    p = QPainter(pm)
    f = QFont()
    f.setPixelSize(16)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(pm.rect(), Qt.AlignCenter, "".join(w[0] for w in title.split()[:2]).upper())
    p.end()
    return QIcon(pm)


class PlanList(QListWidget):
    """One column. Drags carry item ids; the board decides what a drop means."""
    dropped = Signal(object, int)          # item ids, row dropped at

    def __init__(self, column: str, parent=None):
        super().__init__(parent)
        self.column = column
        self.setIconSize(THUMB)
        self.setViewMode(QListView.ListMode)
        self.setSpacing(3)
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)   # the board rebuilds itself; nothing is "moved"
        self.setMinimumWidth(230)

    def mimeTypes(self):
        return [MIME]

    def mimeData(self, items):
        ids = [str(i.data(IdRole)) for i in items if i.data(IdRole) is not None]
        data = QMimeData()
        data.setData(MIME, ",".join(ids).encode())
        return data

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        raw = bytes(e.mimeData().data(MIME)).decode()
        ids = [int(x) for x in raw.split(",") if x]
        idx = self.indexAt(e.position().toPoint())
        row = idx.row() if idx.isValid() else self.count()
        e.setDropAction(Qt.CopyAction)
        e.accept()
        if ids:
            # After the drag finishes: rebuilding the lists inside it would pull the rug.
            QTimer.singleShot(0, lambda: self.dropped.emit(ids, row))


class PlanningPage(QWidget):
    open_item = Signal(int)
    play = Signal(int)
    changed = Signal()                     # the board changed the library (reload counts)
    show_items = Signal(str, list)         # heading, ids (the Dropped list)

    def __init__(self, parent=None):
        super().__init__(parent)
        with db.session() as conn:
            saved = db.get_setting(conn, SETTING)
        # The sections shown; empty = all of them. Several can be on ("Anime" + "TV").
        self.sections: set[str] = {s for s in saved.split(",") if s} if saved else set()
        self.items: dict[int, db.Item] = {}

        head = QHBoxLayout()
        self.heading = QLabel("Planning")
        self.heading.setStyleSheet("font-size: 18pt; font-weight: 600;")
        head.addWidget(self.heading)
        head.addSpacing(16)
        self.chips: dict[str, QPushButton] = {}
        for key, label in FILTERS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("Chip")
            b.setToolTip("Show everything" if not key else f"Show {label} (combine with others)")
            b.clicked.connect(lambda _=False, k=key: self.toggle_section(k))
            head.addWidget(b)
            self.chips[key] = b
        self._sync_chips()
        self.setStyleSheet("QPushButton#Chip { padding: 4px 12px; border-radius: 12px; }"
                           "QPushButton#Chip:checked { background: #4f7cff; color: white; "
                           "border-color: #4f7cff; }")
        head.addStretch(1)
        self.dropped_btn = QPushButton()
        self.dropped_btn.setToolTip("Things you gave up on: off the board and the Backlog")
        self.dropped_btn.clicked.connect(self._show_dropped)
        head.addWidget(self.dropped_btn)

        cols = QHBoxLayout()
        cols.setSpacing(12)
        self.lists: dict[str, PlanList] = {}
        self.titles: dict[str, QLabel] = {}
        for key, label in COLUMNS:
            box = QVBoxLayout()
            title = QLabel(label)
            title.setStyleSheet("font-weight: 600; font-size: 11pt;")
            title.setTextFormat(Qt.RichText)
            lst = PlanList(key)
            lst.dropped.connect(lambda ids, row, k=key: self.drop(ids, k, row))
            lst.itemActivated.connect(lambda it: it.data(IdRole) and self.open_item.emit(it.data(IdRole)))
            lst.setContextMenuPolicy(Qt.CustomContextMenu)
            lst.customContextMenuRequested.connect(lambda pos, w=lst: self._menu(w, pos))
            box.addWidget(title)
            box.addWidget(lst, 1)
            cols.addLayout(box, 1)
            self.lists[key], self.titles[key] = lst, title

        hint = QLabel("Drag cards between columns (or within Up next to reorder). Dropping into "
                      "Done marks it completed; Play and playing an episode move things to "
                      "Playing by themselves. Right-click for more.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 8)
        lay.addLayout(head)
        lay.addLayout(cols, 1)
        lay.addWidget(hint)

    # ------------------------------------------------------------------ showing

    def toggle_section(self, key: str) -> None:
        """A chip was clicked: "All" clears the others; the rest switch on / off."""
        if not key:
            self.sections = set()
        else:
            self.sections ^= {key}
            if self.sections == {k for k, _ in FILTERS if k}:
                self.sections = set()  # every section on = "All"
        with db.session() as conn:
            db.set_setting(conn, SETTING, ",".join(sorted(self.sections)))
        self._sync_chips()
        self.refresh()

    def _sync_chips(self) -> None:
        for key, b in self.chips.items():
            b.setChecked(not self.sections if not key else key in self.sections)

    def _shown(self, category: str) -> bool:
        return not self.sections or category in self.sections

    def refresh(self) -> None:
        with db.session() as conn:
            all_items = db.list_items(conn)
            done = completions.list_year(conn, date.today().year)
        self.items = {i.id: i for i in all_items}
        mine = [i for i in all_items if self._shown(i.category)]
        by_state: dict[str, list[db.Item]] = {k: [] for k, _ in COLUMNS}
        for i in mine:
            if i.plan_state in by_state:
                by_state[i.plan_state].append(i)
        by_state[library.PLAN_NEXT].sort(key=lambda i: (i.plan_position is None, i.plan_position or 0))
        for key, label in COLUMNS:
            lst = self.lists[key]
            lst.clear()
            if key == DONE:
                entries = [c for c in sorted(done, key=lambda c: c.completed_on, reverse=True)
                           if self._shown(c.category)]
                for c in entries:
                    when = c.date_text() + (f" · {c.platform}" if c.platform else "")
                    it = QListWidgetItem(_thumb(c.poster, c.title, c.category), f"{c.title}\n{when}")
                    it.setData(IdRole, c.item_id)
                    lst.addItem(it)
                self.titles[key].setText(f"{label} <span style='color:#8a8f9c'>{len(entries)}</span>")
                continue
            cards = by_state[key]
            for i in cards:
                it = QListWidgetItem(_thumb(i.poster, i.title, i.category), f"{i.title}\n{progress(i)}")
                it.setData(IdRole, i.id)
                it.setToolTip(f"{i.title}\n{categories.get(i.category).singular}"
                              + (f" · {i.platform}" if i.platform else ""))
                lst.addItem(it)
            hours = sum(remaining_hours(i) for i in cards)
            extra = f" · ~{hours:,.0f} h" if hours else ""
            self.titles[key].setText(f"{label} <span style='color:#8a8f9c'>{len(cards)}{extra}</span>")
        dropped = [i for i in mine if i.plan_state == library.PLAN_DROPPED]
        self.dropped_btn.setText(f"Dropped ({len(dropped)})")
        self.dropped_btn.setEnabled(bool(dropped))

    # ------------------------------------------------------------------ changes

    def drop(self, ids: list[int], column: str, row: int = -1) -> None:
        ids = [i for i in ids if i in self.items]
        if not ids:
            return
        with db.session() as conn:
            if column == DONE:
                # Wish-list items aren't yours yet: "Got it" first, then they can be finished.
                library.set_completed(conn, [i for i in ids if not self.items[i].completed
                                             and self.items[i].source != db.WANTED], True)
            else:
                library.set_plan(conn, ids, column, row if column == library.PLAN_NEXT else None)
        self.refresh()
        self.changed.emit()

    def _menu(self, lst: PlanList, pos) -> None:
        ids = [it.data(IdRole) for it in lst.selectedItems() if it.data(IdRole)]
        if not ids:
            return
        menu = QMenu(self)
        if len(ids) == 1:
            menu.addAction("Open", lambda: self.open_item.emit(ids[0]))
            item = self.items.get(ids[0])
            if item and item.source != db.WANTED and (item.category == "game" or item.video_count):
                menu.addAction("▶  Play", lambda: self.play.emit(ids[0]))
            menu.addSeparator()
        for key, label in COLUMNS:
            if key != lst.column:
                menu.addAction(f"Move to {label}", lambda k=key: self.drop(ids, k))
        menu.addSeparator()
        menu.addAction("Drop (gave up on it)", lambda: self._set(ids, library.PLAN_DROPPED))
        if lst.column != DONE:
            menu.addAction("Take off the board", lambda: self._set(ids, None))
        menu.exec(lst.viewport().mapToGlobal(pos))

    def _set(self, ids: list[int], state: str | None) -> None:
        with db.session() as conn:
            library.set_plan(conn, ids, state)
        self.refresh()
        self.changed.emit()

    def _show_dropped(self) -> None:
        ids = [i.id for i in self.items.values() if i.plan_state == library.PLAN_DROPPED
               and self._shown(i.category)]
        self.show_items.emit("Dropped", ids)
