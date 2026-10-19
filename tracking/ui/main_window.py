"""Main window: category sidebar, poster grid, and the full item page."""

from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListView, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSplitter, QStackedWidget, QToolBar, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import categories, db, library, platforms, scan, volumes
from . import dialogs
from .grid import CardDelegate, ItemModel, ItemRole
from .item_page import ItemPage
from .workers import EnrichJob, Job, ScanJob

SECTIONS = [(c.key, c.label) for c in categories.CATEGORIES]
SORTS = [("title", "Title"), ("avg", "Score: average"), ("best", "Score: highest"),
         ("time", "Total time"), ("recent", "Recently added")]
NavRole = Qt.UserRole + 10


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
        self.warnings: list[str] = []
        self.all_items: list[db.Item] = []

        self._build_toolbar()
        self._build_body()
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
        tb.addSeparator()
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
        self.hide_done = QCheckBox("Hide completed")
        self.hide_done.setChecked(hide_done)
        self.hide_missing = QCheckBox("Hide missing")
        self.hide_missing.setChecked(hide_missing)
        for cb, key in ((self.hide_done, "hide_completed"), (self.hide_missing, "hide_missing")):
            cb.toggled.connect(lambda on, key=key: self._save_filter(key, on))
        head.addWidget(self.heading)
        head.addSpacing(10)
        head.addWidget(self.count, 0, Qt.AlignBottom)
        head.addStretch(1)
        head.addWidget(self.hide_done)
        head.addWidget(self.hide_missing)
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
        self.page.back.connect(lambda: self.stack.setCurrentIndex(0))
        self.page.changed.connect(self._item_changed)
        self.page.refetch.connect(lambda i: self.start_job(EnrichJob([i], force=True)))
        self.page.open_folder.connect(self.open_item)
        self.page.deleted.connect(self._item_deleted)
        self.stack.addWidget(self.page)

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

    def reload(self):
        with db.session() as conn:
            self.all_items = db.list_items(conn)
            counts = db.category_counts(conn)
            plats = db.platform_counts(conn)
        self._rebuild_sidebar(counts, plats)
        self.apply_filter()

    def _rebuild_sidebar(self, counts: dict[str, int], plats: dict[str, int]):
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
        self.sidebar.setCurrentItem(current)
        self.sidebar.blockSignals(False)

    def _nav_changed(self, cur: QTreeWidgetItem | None, _prev=None):
        if cur is None:
            return
        self.nav = tuple(cur.data(0, NavRole))
        self.stack.setCurrentIndex(0)
        self.apply_filter()

    def _save_filter(self, key: str, on: bool):
        with db.session() as conn:
            db.set_setting(conn, key, "1" if on else "0")
        self.apply_filter()

    def apply_filter(self):
        category, platform = self.nav
        needle = self.search.text().strip().lower()
        hide_done, hide_missing = self.hide_done.isChecked(), self.hide_missing.isChecked()
        items = [
            i for i in self.all_items
            if (not category or i.category == category)
            and (not platform or (i.platform or db.UNKNOWN_PLATFORM) == platform)
            and (not needle or needle in i.title.lower() or needle in i.rel_path.lower())
            and not (hide_done and i.completed)
            and not (hide_missing and i.missing)
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
        elif key == "recent":
            items.sort(key=lambda i: -i.id)

        heading = dict(SECTIONS).get(category, "All items") if category else "All items"
        if platform:
            heading = f"Games · {platform}"
        self.heading.setText(heading)
        done = sum(1 for i in items if i.completed)
        left_h = sum(i.total_hours or 0 for i in items if not i.completed)
        self.count.setText(f"{len(items)} items · {done} completed · {left_h:,.0f} h to go")
        self.model.set_items(items)

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
        if old and (old.category, old.platform, old.title, old.completed) != (
                item.category, item.platform, item.title, item.completed):
            self.reload()  # sidebar counts / filters / sort may change
        if self.stack.currentIndex() == 1 and self.page.item and self.page.item.id == item_id:
            self._fill_page(item)

    def _item_deleted(self, item_id: int):
        self.stack.setCurrentIndex(0)
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
        dialogs.SettingsDialog(self).exec()

    def open_item(self, item_id: int):
        item = self._find(item_id)
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
        if len(ids) == 1:
            menu.addAction("Open details", lambda: self.show_item(ids[0]))
            menu.addAction("Open folder", lambda: self.open_item(ids[0]))
            menu.addSeparator()
        if not all(i.completed for i in items):
            menu.addAction("Mark as completed", lambda: self.set_completed(ids, True))
        if any(i.completed for i in items):
            menu.addAction("Mark as not completed", lambda: self.set_completed(ids, False))
        menu.addAction("Refetch metadata", lambda: self.start_job(EnrichJob(ids, force=True)))
        menu.addMenu(self._move_menu(ids))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

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

    def open_log(self):
        dialogs.LogDialog(self).exec()
