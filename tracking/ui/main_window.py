"""Main window: category sidebar, poster grid, and the full item page."""

from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListView, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSplitter, QStackedWidget, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import categories, completions, db, library, platforms, scan, volumes
from ..sources import steam
from . import dialogs
from .completed_page import CompletedPage
from .grid import OFFLINE_TEXT, CardDelegate, ItemModel, ItemRole
from .item_page import ItemPage
from .workers import CompletionCoversJob, EnrichJob, Job, ScanJob

SECTIONS = [(c.key, c.label) for c in categories.CATEGORIES]
SORTS = [("title", "Title"), ("avg", "Score: average"), ("best", "Score: highest"),
         ("time", "Total time"), ("short", "Shortest first"), ("played", "Time played"),
         ("recent", "Recently added")]
NavRole = Qt.UserRole + 10
WANT = "want"  # sidebar navigation: (WANT, category or None) is the "Want to acquire" list
COMPLETED = "completed"  # sidebar navigation: (COMPLETED, year or None)
FOCUS = "focus"  # (FOCUS, heading): a list picked elsewhere (Library health › Show)
BACKLOG = "backlog"  # sidebar navigation: (BACKLOG, category or None), owned and not finished
STEAM = "steam"  # sidebar navigation: (STEAM, None) is the games of your Steam accounts


def view_of(item: db.Item) -> str | None:
    """The sidebar part an item belongs to: WANT, STEAM or None (the library)."""
    return {db.WANTED: WANT, db.STEAM: STEAM}.get(item.source)


def in_backlog(item: db.Item) -> bool:
    """Yours (on disk or marked as got), not finished, not gone missing."""
    return view_of(item) is None and not item.completed and not item.missing


def played_order(item: db.Item) -> tuple:
    """"Time played" sort: most played first, never-played next, unknown last."""
    return (item.playtime_min is None, -(item.playtime_min or 0), item.title.lower())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tracking")
        self.resize(1400, 860)
        self.pool = QThreadPool.globalInstance()
        self.job: Job | None = None
        self.queue: list[Job] = []          # jobs waiting for the running one
        self.queue_warnings_kept = False
        self.status_text = "Ready"
        self.mounts: dict[str, str | None] = {}
        self.labels: dict[str, str] = {}
        self.nav: tuple[str | None, str | None] = (None, None)
        self.focus_ids: set[int] = set()  # the items a (FOCUS, heading) view shows
        self.warnings: list[str] = []
        self.all_items: list[db.Item] = []

        self._build_toolbar()
        self._build_body()
        self._build_shortcuts()
        self._build_status()
        self.refresh_volumes()
        self.reload()

        with db.session() as conn:
            empty = conn.execute("SELECT COUNT(*) n FROM roots").fetchone()["n"] == 0
        if empty:
            QTimer.singleShot(300, self.manage_locations)

    # ------------------------------------------------------------------ layout

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        def action(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            return a

        action("Locations…", self.manage_locations, "Ctrl+L")
        action("Rescan…", lambda: self.rescan(), "F5")
        action("Update metadata", lambda: self.start_job(EnrichJob(only_pending=True)))
        action("Want to acquire…",
               lambda: self.done_page.add() if self.nav[0] == COMPLETED else self.add_wanted(),
               "Ctrl+N")
        tb.addSeparator()
        export = QToolButton()
        export.setText("Export list…")
        export.setToolTip("Save a list of items as a spreadsheet (CSV)")
        export.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(export)
        menu.addAction("Items missing a cover or scores…", lambda: self.export_list(missing_only=True))
        menu.addAction("Items shown now…", lambda: self.export_list(missing_only=False))
        export.setMenu(menu)
        tb.addWidget(export)
        action("Library health…", self.open_health, "Ctrl+H")
        action("Settings…", self.open_settings, "Ctrl+,")
        action("Log", self.open_log, "Ctrl+Shift+L")

    def _build_body(self):
        split = QSplitter()
        split.setHandleWidth(1)
        self.setCentralWidget(split)

        self.sidebar = QTreeWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setIndentation(14)
        self.sidebar.setMinimumWidth(200)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.currentItemChanged.connect(self._nav_changed)
        split.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 1180])

        # --- grid page
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(16, 12, 8, 0)
        head = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setStyleSheet("font-size: 18pt; font-weight: 600;")
        self.count = QLabel()
        self.count.setObjectName("Muted")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self.apply_filter)
        self.sort = QComboBox()
        for key, label in SORTS:
            self.sort.addItem(f"Sort: {label}", key)
        self.sort.currentIndexChanged.connect(self.apply_filter)
        with db.session() as conn:
            hide_done = db.get_setting(conn, "hide_completed") == "1"
            hide_missing = db.get_setting(conn, "hide_missing") == "1"
            hide_no_cover = db.get_setting(conn, "hide_no_cover") == "1"
        self.hide_no_cover = QCheckBox("Hide no cover")
        self.hide_no_cover.setToolTip("Hide items that have no cover image")
        self.hide_no_cover.setChecked(hide_no_cover)
        self.hide_done = QCheckBox("Hide completed")
        self.hide_done.setChecked(hide_done)
        self.hide_missing = QCheckBox("Hide missing")
        self.hide_missing.setChecked(hide_missing)
        for cb, key in ((self.hide_done, "hide_completed"), (self.hide_missing, "hide_missing"),
                        (self.hide_no_cover, "hide_no_cover")):
            cb.toggled.connect(lambda on, key=key: self._save_filter(key, on))
        head.addWidget(self.heading)
        self.add_btn = QPushButton("+ Add…")
        self.add_btn.setObjectName("Primary")
        self.add_btn.setToolTip("Search for something you want and add it to this list (Ctrl+N)")
        self.add_btn.clicked.connect(
            lambda: self.add_steam_account() if self.nav[0] == STEAM else self.add_wanted())
        self.add_btn.hide()
        head.addSpacing(10)
        head.addWidget(self.add_btn)
        head.addSpacing(10)
        head.addWidget(self.count, 0, Qt.AlignBottom)
        head.addStretch(1)
        head.addWidget(self.hide_done)
        head.addWidget(self.hide_missing)
        head.addWidget(self.hide_no_cover)
        head.addSpacing(8)
        head.addWidget(self.search)
        head.addWidget(self.sort)
        cl.addLayout(head)

        # Shown while several cards are selected (Ctrl/Shift+click, Ctrl+A).
        self.bulk = QWidget()
        self.bulk.setObjectName("BulkBar")
        bl = QHBoxLayout(self.bulk)
        bl.setContentsMargins(10, 6, 10, 6)
        self.bulk_label = QLabel()
        bl.addWidget(self.bulk_label)
        bl.addStretch(1)
        move_btn = QPushButton("Move to ▾")
        move_btn.clicked.connect(lambda: self._move_menu(self._selected_ids()).exec(
            move_btn.mapToGlobal(move_btn.rect().bottomLeft())))
        done_btn = QPushButton("Mark completed")
        done_btn.clicked.connect(lambda: self.set_completed(self._selected_ids(), True))
        undone_btn = QPushButton("Mark not completed")
        undone_btn.clicked.connect(lambda: self.set_completed(self._selected_ids(), False))
        refetch_btn = QPushButton("Refetch")
        refetch_btn.clicked.connect(lambda: self.start_job(EnrichJob(self._selected_ids(), force=True)))
        clear_btn = QPushButton("Clear selection")
        clear_btn.clicked.connect(lambda: self.grid.clearSelection())
        for b in (move_btn, done_btn, undone_btn, refetch_btn, clear_btn):
            bl.addWidget(b)
        self.bulk.hide()
        cl.addWidget(self.bulk)

        self.model = ItemModel()
        self.grid = QListView()
        self.grid.setObjectName("Grid")
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(CardDelegate(self.model, self.grid))
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(0)
        self.grid.setMouseTracking(True)
        self.grid.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.grid.verticalScrollBar().setSingleStep(30)
        self.grid.setSelectionMode(QListView.ExtendedSelection)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.grid.clicked.connect(self._card_clicked)
        self.grid.selectionModel().selectionChanged.connect(self._selection_changed)
        self.grid.activated.connect(lambda idx: self.show_item(idx.data(ItemRole).id))
        cl.addWidget(self.grid)
        self.stack.addWidget(center)

        # --- item page
        self.page = ItemPage()
        self.page.back.connect(lambda: self.stack.setCurrentIndex(2 if self.nav[0] == COMPLETED else 0))
        self.page.changed.connect(self._item_changed)
        self.page.refetch.connect(lambda i: self.start_job(EnrichJob([i], force=True)))
        self.page.open_folder.connect(self.open_item)
        self.page.deleted.connect(self._item_deleted)
        self.stack.addWidget(self.page)

        # --- completed page (index 2)
        self.done_page = CompletedPage()
        self.done_page.heading.setStyleSheet("font-size: 18pt; font-weight: 600;")
        self.done_page.changed.connect(self._completions_changed)
        self.done_page.open_item.connect(self.show_item)
        self.done_page.fetch_covers.connect(lambda ids: self.start_job(CompletionCoversJob(ids)))
        self.stack.addWidget(self.done_page)

    def _build_shortcuts(self):
        """Keyboard: Space completes the selected cards, Ctrl+F searches, Esc / Alt+Left go
        back from an item page. (Arrows, Enter to open and Ctrl+A come with the grid.)"""
        QShortcut(QKeySequence(Qt.Key_Space), self.grid, self._toggle_selected,
                  context=Qt.WidgetShortcut)
        QShortcut(QKeySequence.Find, self, self._focus_search)
        for key in (QKeySequence(Qt.Key_Escape), QKeySequence(Qt.ALT | Qt.Key_Left)):
            QShortcut(key, self.page, self._back_from_page, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence(Qt.Key_Escape), self.search, self.search.clear,
                  context=Qt.WidgetShortcut)

    def _toggle_selected(self):
        ids = self._selected_ids()
        items = [i for i in (self._find(x) for x in ids) if i and view_of(i) != WANT]
        if items:
            # Mixed selection: complete them all; all completed: un-complete.
            self.set_completed([i.id for i in items], not all(i.completed for i in items))

    def _focus_search(self):
        if self.stack.currentIndex() != 0:
            self.stack.setCurrentIndex(0)
        self.search.setFocus()
        self.search.selectAll()

    def _back_from_page(self):
        self.stack.setCurrentIndex(2 if self.nav[0] == COMPLETED else 0)
        self.grid.setFocus()

    def _build_status(self):
        sb = self.statusBar()
        self.status = QLabel("Ready")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFlat(True)
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(lambda: self.job and self.job.cancel())
        self.warn_btn = QPushButton()
        self.warn_btn.setFlat(True)
        self.warn_btn.hide()
        self.warn_btn.clicked.connect(self._show_warnings)
        sb.addWidget(self.status, 1)
        sb.addPermanentWidget(self.warn_btn)
        sb.addPermanentWidget(self.cancel_btn)

    # ------------------------------------------------------------------ data

    def refresh_volumes(self):
        known = volumes.list_volumes()
        with db.session() as conn:
            rows = conn.execute("SELECT uid, label FROM volumes").fetchall()
            self.mounts = {r["uid"]: scan.mount_for(conn, r["uid"], known) for r in rows}
            self.labels = {r["uid"]: r["label"] or "(no label)" for r in rows}
        self.model.offline_volumes = {u for u, m in self.mounts.items() if not m}
        with db.session() as conn:
            self.model.offline_style = db.get_setting(conn, "offline_style", OFFLINE_TEXT)

    def reload(self):
        with db.session() as conn:
            self.all_items = db.list_items(conn)
            counts = db.category_counts(conn)
            plats = db.platform_counts(conn)
            wanted = db.category_counts(conn, wanted=True)
            steam_games = db.source_count(conn, db.STEAM)
            done_years = completions.years(conn)
        self._rebuild_sidebar(counts, plats, wanted, steam_games, done_years)
        self.apply_filter()
        if self.nav[0] == COMPLETED:
            self.done_page.show_year(self.nav[1])

    def _rebuild_sidebar(self, counts: dict[str, int], plats: dict[str, int],
                         wanted: dict[str, int] | None = None, steam_games: int = 0,
                         done_years: dict[int, int] | None = None):
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        everything = QTreeWidgetItem([f"All items   {sum(counts.values())}"])
        everything.setData(0, NavRole, (None, None))
        self.sidebar.addTopLevelItem(everything)
        current = everything
        for key, label in SECTIONS:
            node = QTreeWidgetItem([f"{label}   {counts.get(key, 0)}"])
            node.setData(0, NavRole, (key, None))
            f = node.font(0)
            f.setBold(True)
            node.setFont(0, f)
            self.sidebar.addTopLevelItem(node)
            if self.nav == (key, None):
                current = node
            if key == "game":
                for plat, n in plats.items():
                    child = QTreeWidgetItem([f"{plat}   {n}"])
                    child.setData(0, NavRole, ("game", plat))
                    node.addChild(child)
                    if self.nav == ("game", plat):
                        current = child
                node.setExpanded(True)

        # Backlog: what you have and haven't finished yet, per section.
        backlog: dict[str, int] = {}
        for i in self.all_items:
            if in_backlog(i):
                backlog[i.category] = backlog.get(i.category, 0) + 1
        node = QTreeWidgetItem([f"Backlog   {sum(backlog.values())}"])
        node.setData(0, NavRole, (BACKLOG, None))
        f = node.font(0)
        f.setBold(True)
        node.setFont(0, f)
        node.setToolTip(0, "What you have and haven't finished. Try Sort: Shortest first.")
        self.sidebar.addTopLevelItem(node)
        if self.nav == (BACKLOG, None):
            current = node
        for key, label in SECTIONS:
            if backlog.get(key):
                child = QTreeWidgetItem([f"{label}   {backlog[key]}"])
                child.setData(0, NavRole, (BACKLOG, key))
                node.addChild(child)
                if self.nav == (BACKLOG, key):
                    current = child

        # Completed: everything you finished, per year (also entries outside the library).
        done_years = done_years or {}
        done = QTreeWidgetItem([f"Completed   {sum(done_years.values())}"])
        done.setData(0, NavRole, (COMPLETED, None))
        f = done.font(0)
        f.setBold(True)
        done.setFont(0, f)
        done.setToolTip(0, "What you finished, by year. Import year CSVs, edit dates, "
                           "export a year as an image.")
        self.sidebar.addTopLevelItem(done)
        if self.nav == (COMPLETED, None):
            current = done
        for year, n in done_years.items():
            child = QTreeWidgetItem([f"{year}   {n}"])
            child.setData(0, NavRole, (COMPLETED, year))
            done.addChild(child)
            if self.nav == (COMPLETED, year):
                current = child
        done.setExpanded(True)

        # Steam: the games your Steam accounts own, apart from the library.
        node = QTreeWidgetItem([f"Steam   {steam_games}"])
        node.setData(0, NavRole, (STEAM, None))
        f = node.font(0)
        f.setBold(True)
        node.setFont(0, f)
        node.setToolTip(0, "Games from your Steam account, with play time "
                           "(Locations › Add Steam account)")
        self.sidebar.addTopLevelItem(node)
        if self.nav == (STEAM, None):
            current = node

        # "Want to acquire": things you don't have yet, per section.
        wanted = wanted or {}
        want = QTreeWidgetItem([f"Want to acquire   {sum(wanted.values())}"])
        want.setData(0, NavRole, (WANT, None))
        f = want.font(0)
        f.setBold(True)
        want.setFont(0, f)
        self.sidebar.addTopLevelItem(want)
        if self.nav == (WANT, None):
            current = want
        for key, label in SECTIONS:
            if key == "unknown":
                continue
            child = QTreeWidgetItem([f"{label}   {wanted.get(key, 0)}"])
            child.setData(0, NavRole, (WANT, key))
            want.addChild(child)
            if self.nav == (WANT, key):
                current = child
        want.setExpanded(True)
        if self.nav[0] != FOCUS:  # a Library health list isn't a sidebar entry
            self.sidebar.setCurrentItem(current)
        else:
            self.sidebar.setCurrentItem(None)
        self.sidebar.blockSignals(False)

    def _nav_changed(self, cur: QTreeWidgetItem | None, _prev=None):
        if cur is None:
            return
        self.nav = tuple(cur.data(0, NavRole))
        if self.nav[0] == COMPLETED:
            self.done_page.show_year(self.nav[1])
            self.stack.setCurrentIndex(2)
            return
        self.stack.setCurrentIndex(0)
        self.apply_filter()

    def _completions_changed(self):
        # The page may have moved to another year (an import, an edited date).
        self.nav = (COMPLETED, self.done_page.year)
        self.reload()

    def _save_filter(self, key: str, on: bool):
        with db.session() as conn:
            db.set_setting(conn, key, "1" if on else "0")
        self.apply_filter()

    def apply_filter(self):
        if self.nav[0] == FOCUS:
            self._apply_focus()
            return
        backlog_view = self.nav[0] == BACKLOG
        view = self.nav[0] if self.nav[0] in (WANT, STEAM) else None
        wanted_view, steam_view = view == WANT, view == STEAM
        category, platform = (self.nav[1], None) if (view or backlog_view) else self.nav
        needle = self.search.text().strip().lower()
        hide_done, hide_missing = self.hide_done.isChecked(), self.hide_missing.isChecked()
        hide_no_cover = self.hide_no_cover.isChecked()
        items = [
            i for i in self.all_items
            if view_of(i) == view
            and (not backlog_view or in_backlog(i))
            and (not category or i.category == category)
            and (not platform or (i.platform or db.UNKNOWN_PLATFORM) == platform)
            and (not needle or needle in i.title.lower() or needle in i.rel_path.lower())
            and not (hide_done and i.completed)
            and not (hide_missing and i.missing)
            and not (hide_no_cover and not i.poster)
        ]
        key = self.sort.currentData()
        if key in ("avg", "best"):
            # Unscored items last; ties broken by how many sites scored it.
            def score(i):
                v = i.avg_score if key == "avg" else i.best_score
                return (v is None, -(v or 0), -len(i.scores))
            items.sort(key=score)
        elif key == "time":
            items.sort(key=lambda i: -(i.total_hours or 0))
        elif key == "short":
            # Quickest to finish first; unknown length last.
            items.sort(key=lambda i: (i.total_hours is None, i.total_hours or 0, i.title.lower()))
        elif key == "played":
            items.sort(key=played_order)
        elif key == "recent":
            items.sort(key=lambda i: -i.id)

        heading = dict(SECTIONS).get(category, "All items") if category else "All items"
        if platform:
            heading = f"Games · {platform}"
        if wanted_view:
            heading = f"Want to acquire · {heading}" if category else "Want to acquire"
        if steam_view:
            heading = "Steam"
        if backlog_view:
            heading = f"Backlog · {heading}" if category else "Backlog"
        self.heading.setText(heading)
        self.add_btn.setVisible(wanted_view or steam_view)
        self.add_btn.setText("+ Add Steam account…" if steam_view else "+ Add…")
        self.add_btn.setToolTip("Import the games a Steam account owns, with play time"
                                if steam_view else "Search for something you want and add it "
                                "to this list (Ctrl+N)")
        if steam_view:
            played_h = sum(i.playtime_min or 0 for i in items) / 60
            done = sum(1 for i in items if i.completed)
            self.count.setText(f"{len(items)} games · {played_h:,.0f} h played · {done} completed")
        elif backlog_view:
            total_h = sum(i.total_hours or 0 for i in items)
            self.count.setText(f"{len(items)} not finished yet · {total_h:,.0f} h in all")
        elif wanted_view:
            total_h = sum(i.total_hours or 0 for i in items)
            self.count.setText(f"{len(items)} items · {total_h:,.0f} h if you got them all")
        else:
            done = sum(1 for i in items if i.completed)
            left_h = sum(i.total_hours or 0 for i in items if not i.completed)
            self.count.setText(f"{len(items)} items · {done} completed · {left_h:,.0f} h to go")
        self.model.set_items(items)

    def _apply_focus(self):
        needle = self.search.text().strip().lower()
        items = sorted((i for i in self.all_items if i.id in self.focus_ids
                        and (not needle or needle in i.title.lower())),
                       key=lambda i: (i.title.lower(), i.platform or ""))
        self.heading.setText(self.nav[1])
        self.add_btn.hide()
        self.count.setText(f"{len(items)} items · click a section in the sidebar to go back")
        self.model.set_items(items)

    def open_health(self):
        dlg = dialogs.HealthDialog(self.all_items, self)
        dlg.show_items.connect(self.show_focus)
        dlg.refetch.connect(lambda ids: self.start_job(EnrichJob(ids, force=True)))
        dlg.exec()

    def show_focus(self, heading: str, ids: list[int]):
        """Show just these items (from Library health), whatever their section."""
        self.focus_ids = set(ids)
        self.nav = (FOCUS, heading)
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentItem(None)  # so any sidebar click goes back
        self.sidebar.blockSignals(False)
        self.stack.setCurrentIndex(0)
        self.apply_filter()

    def _selected_ids(self) -> list[int]:
        return [i.data(ItemRole).id for i in self.grid.selectionModel().selectedIndexes()]

    def _find(self, item_id: int) -> db.Item | None:
        return next((i for i in self.all_items if i.id == item_id), None)

    def _item_changed(self, item_id: int):
        if item_id < 0:
            self.refresh_volumes()
            self.reload()
            return
        with db.session() as conn:
            item = db.get_item(conn, item_id)
        if not item:
            return
        old = self._find(item_id)
        self.all_items = [item if i.id == item_id else i for i in self.all_items]
        self.model.replace_item(item)
        if old and (old.category, old.platform, old.title, old.completed, old.source) != (
                item.category, item.platform, item.title, item.completed, item.source):
            self.reload()  # sidebar counts / filters / sort may change
        if self.stack.currentIndex() == 1 and self.page.item and self.page.item.id == item_id:
            self._fill_page(item)

    def _item_deleted(self, item_id: int):
        self.stack.setCurrentIndex(2 if self.nav[0] == COMPLETED else 0)
        self.reload()

    # ------------------------------------------------------------------ item page

    def _card_clicked(self, idx):
        if QApplication.keyboardModifiers() == Qt.NoModifier:
            self.show_item(idx.data(ItemRole).id)

    def show_item(self, item_id: int):
        item = self._find(item_id)
        if item:
            self._fill_page(item)
            self.stack.setCurrentIndex(1)

    def _fill_page(self, item: db.Item):
        self.page.show_item(item, self.mounts.get(item.volume_uid),
                            self.labels.get(item.volume_uid, ""))

    # ------------------------------------------------------------------ jobs

    def start_job(self, job: Job):
        """Run a job now, or queue it behind the running one. Scans go to the
        front of the queue so a newly added drive doesn't wait behind a long
        metadata fetch; Cancel stops only the running job."""
        if self.job:
            if isinstance(job, ScanJob):
                self.queue.insert(0, job)
            else:
                self.queue.append(job)
            self._show_progress(self.status_text)
            return
        self.job = job
        if not self.queue_warnings_kept:
            self.warnings.clear()
            self.warn_btn.hide()
        job.signals.progress.connect(self._show_progress)
        job.signals.item_changed.connect(self._item_changed)
        job.signals.warning.connect(self._add_warning)
        job.signals.finished.connect(self._job_done)
        self.cancel_btn.show()
        self._show_progress("Working…")
        self.pool.start(job)

    def closeEvent(self, event):
        """Stop background work on close; otherwise the process would linger
        invisibly until a long metadata fetch finished. Finished items are
        already saved; "Update metadata" picks up the rest next time."""
        self.queue.clear()
        if self.job:
            self.job.cancel()
            self.status.setText("Stopping…")
            QApplication.processEvents()
            # Jobs check for cancel between items; one item can take a few
            # seconds (rate limits, a slow drive), never more than a request timeout.
            self.pool.waitForDone(35_000)
        super().closeEvent(event)

    def _show_progress(self, text: str):
        self.status_text = text
        waiting = f"   (+{len(self.queue)} queued)" if self.queue else ""
        self.status.setText(text + waiting)

    def _job_done(self, summary: str):
        self.job = None
        self.cancel_btn.hide()
        self.status_text = summary
        self.status.setText(summary)
        self.refresh_volumes()
        self.reload()
        if self.queue:
            # Keep the previous job's warnings visible while the queue drains.
            self.queue_warnings_kept = True
            self.start_job(self.queue.pop(0))
            self.queue_warnings_kept = False

    def _add_warning(self, text: str):
        if text not in self.warnings:
            self.warnings.append(text)
        self.warn_btn.setText(f"⚠ {len(self.warnings)} warnings")
        self.warn_btn.show()

    def _show_warnings(self):
        QMessageBox.information(self, "Warnings", "\n".join(self.warnings[-40:]))

    # ------------------------------------------------------------------ actions

    def manage_locations(self):
        dlg = dialogs.LocationsDialog(self)
        dlg.exec()
        self.refresh_volumes()
        self.reload()
        if dlg.added_ids:
            self.start_job(ScanJob(dlg.added_ids))
        elif dlg.rescan_ids:
            self.rescan(dlg.rescan_ids)

    def rescan(self, preselect: list[int] | None = None):
        dlg = dialogs.RescanDialog(preselect, self)
        if dlg.exec() != QDialog.Accepted:
            return
        ids = dlg.root_ids()
        if ids:
            self.start_job(ScanJob(ids, enrich_after=dlg.enrich.isChecked(),
                                   missing_completed=dlg.missing_completed.isChecked()))

    def open_settings(self):
        if dialogs.SettingsDialog(self).exec():
            self.refresh_volumes()  # picks up display settings such as "offline_style"
            self.grid.viewport().update()

    def open_item(self, item_id: int):
        item = self._find(item_id)
        if item and item.source in (db.WANTED, "manual", "steam"):  # no files: its web page
            if item.external_url:
                QDesktopServices.openUrl(QUrl(item.external_url))
            return
        mount = self.mounts.get(item.volume_uid) if item else None
        if not item or not mount:
            QMessageBox.information(self, "Offline", "That drive isn't connected right now.")
            return
        path = volumes.join(mount, scan.fs_path(item.rel_path))
        if not os.path.exists(path):
            QMessageBox.information(self, "Missing", "It's no longer at its last known location.")
        elif os.name == "nt":
            if item.is_dir:
                os.startfile(path)
            else:
                subprocess.Popen(["explorer", "/select,", path])
        else:
            subprocess.Popen(["xdg-open", path if item.is_dir else os.path.dirname(path)])

    def set_completed(self, ids: list[int], done: bool):
        with db.session() as conn:
            library.set_completed(conn, ids, done)
        self.reload()

    def set_category(self, ids: list[int], category: str, platform: str | None = None):
        with db.session() as conn:
            library.move_items(conn, ids, category, platform)
        self.reload()
        self.start_job(EnrichJob(ids, force=True))

    def _context_menu(self, pos):
        ids = self._selected_ids()
        if not ids:
            return
        items = [self._find(i) for i in ids]
        menu = QMenu(self)
        if all(i.source == db.WANTED for i in items):
            if len(ids) == 1:
                menu.addAction("Open details", lambda: self.show_item(ids[0]))
                menu.addSeparator()
            menu.addAction("Got it (move to library)", lambda: self.acquire(ids))
            menu.addAction("Remove from list", lambda: self.remove_wanted(ids))
            menu.addAction("Refetch metadata", lambda: self.start_job(EnrichJob(ids, force=True)))
            menu.exec(self.grid.viewport().mapToGlobal(pos))
            return
        if len(ids) == 1:
            menu.addAction("Open details", lambda: self.show_item(ids[0]))
            menu.addAction("Open folder" if items[0].source == "disk" else "Open web page",
                           lambda: self.open_item(ids[0]))
            menu.addSeparator()
        if not all(i.completed for i in items):
            menu.addAction("Mark as completed", lambda: self.set_completed(ids, True))
        if any(i.completed for i in items):
            menu.addAction("Mark as not completed", lambda: self.set_completed(ids, False))
        menu.addAction("Refetch metadata", lambda: self.start_job(EnrichJob(ids, force=True)))
        menu.addMenu(self._move_menu(ids))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ want to acquire

    def add_wanted(self, category: str | None = None):
        category = category or (self.nav[1] if self.nav[0] == WANT else self.nav[0]) or "game"
        dlg = dialogs.WantDialog(category if category in dict(SECTIONS) else "game", self)
        if dlg.exec() != QDialog.Accepted or not dlg.added:
            return
        self.nav = (WANT, dlg.category)
        self.reload()
        self.start_job(EnrichJob(dlg.added, force=True))  # covers, scores, play time

    def add_steam_account(self):
        dlg = dialogs.AddSteamDialog(self)
        if dlg.exec() != QDialog.Accepted or not dlg.account:
            return
        with db.session() as conn:
            rid = steam.add_account(conn, dlg.account, wishlist=dlg.wishlist.isChecked())
        self.refresh_volumes()
        self.start_job(ScanJob([rid]))  # games + play time, then covers and scores

    def acquire(self, ids: list[int]):
        with db.session() as conn:
            library.acquire(conn, ids)
        self.reload()

    def remove_wanted(self, ids: list[int]):
        if QMessageBox.question(self, "Remove from list",
                                f"Remove {len(ids)} item(s) from your Want to acquire list?") \
                != QMessageBox.Yes:
            return
        with db.session() as conn:
            library.remove_items(conn, ids)
        self.stack.setCurrentIndex(0)
        self.reload()

    def _move_menu(self, ids: list[int]) -> QMenu:
        move = QMenu("Move to", self)
        for key, label in SECTIONS:
            if key == "game":
                games = move.addMenu("Games")
                games.addAction("Unknown platform", lambda: self.set_category(ids, "game", None))
                for p in platforms.NAMES:
                    games.addAction(p, lambda p=p: self.set_category(ids, "game", p))
            else:
                move.addAction(label, lambda k=key: self.set_category(ids, k))
        return move

    def _selection_changed(self, *_):
        n = len(self.grid.selectionModel().selectedIndexes())
        self.bulk.setVisible(n > 1)
        self.bulk_label.setText(f"{n} selected")

    def after_import(self, result):
        self.refresh_volumes()
        self.reload()
        if result.need_metadata and QMessageBox.question(
                self, "Fetch covers",
                f"{result.need_metadata} imported items have no cover yet. Fetch covers and "
                "scores for them now?") == QMessageBox.Yes:
            self.start_job(EnrichJob(only_pending=True))

    def export_list(self, missing_only: bool) -> None:
        if missing_only:
            items = [i for i in self.all_items if i.source != db.WANTED and library.missing_data(i)]
            name = "tracking-missing-data.csv"
        else:
            items = list(self.model.items)
            name = "tracking-list.csv"
        if not items:
            QMessageBox.information(self, "Export list", "Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export list", os.path.join(os.path.expanduser("~"), "Desktop", name),
            "Spreadsheet (*.csv)")
        if not path:
            return
        with db.session() as conn:
            n = library.export_list(conn, items, path)
        self.statusBar().showMessage(f"Exported {n} items to {path}", 8000)

    def open_log(self):
        dialogs.LogDialog(self).exec()
