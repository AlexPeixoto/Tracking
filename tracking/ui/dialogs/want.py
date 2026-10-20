"""Search for something you want and add it to the "Want to acquire" list.

Search uses the first keyless-capable service for the section (Steam store for
games, IMDb for movies / TV, AniList for anime; see providers.search). It runs
on a background thread so the window never freezes; thumbnails arrive after.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ... import categories, db, library, providers
from ...providers.base import session

THUMB = QSize(68, 96)


class _Signals(QObject):
    results = Signal(int, str, list)     # search number, service label, candidates
    failed = Signal(int, str)
    thumb = Signal(int, int, QImage)     # search number, row, image


class _Search(QRunnable):
    """A search off the UI thread; ``search(query)`` returns (service label, candidates)."""

    def __init__(self, number: int, query: providers.Query, search=providers.search):
        super().__init__()
        self.number, self.query, self.search = number, query, search
        self.signals = _Signals()

    def run(self) -> None:
        try:
            source, found = self.search(self.query)
        except Exception as e:
            self.signals.failed.emit(self.number, str(e))
            return
        self.signals.results.emit(self.number, source, found)
        for row, c in enumerate(found):  # thumbnails after the list is on screen
            if not c.image_url:
                continue
            try:
                data = session().get(c.image_url, timeout=15).content
            except Exception:
                continue
            img = QImage.fromData(data)
            if not img.isNull():
                self.signals.thumb.emit(self.number, row,
                                        img.scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class WantDialog(QDialog):
    def __init__(self, category: str = "game", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Want to acquire")
        self.resize(640, 620)
        self.category = category
        self.added: list[int] = []
        self._search_no = 0
        self._candidates: list[providers.Candidate] = []

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.kind = QComboBox()
        for c in categories.CATEGORIES:
            if c.key != "unknown":
                self.kind.addItem(c.label, c.key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(category)))
        self.kind.currentIndexChanged.connect(self._search)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Name of the game, anime, movie or show…")
        self.text.returnPressed.connect(self._search)
        go = QPushButton("Search")
        go.clicked.connect(self._search)
        row.addWidget(self.kind)
        row.addWidget(self.text, 1)
        row.addWidget(go)
        lay.addLayout(row)

        self.status = QLabel("Type a name and press Enter.")
        self.status.setObjectName("Muted")
        lay.addWidget(self.status)

        self.results = QListWidget()
        self.results.setIconSize(THUMB)
        self.results.setSpacing(2)
        self.results.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results.itemDoubleClicked.connect(lambda _: self._add_selected())
        self.results.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.results, 1)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add selected")
        self.add_btn.setObjectName("Primary")
        self.add_btn.clicked.connect(self._add_selected)
        self.add_typed = QPushButton()
        self.add_typed.clicked.connect(self._add_as_typed)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.add_typed)
        buttons.addStretch(1)
        buttons.addWidget(self.add_btn)
        buttons.addWidget(close)
        lay.addLayout(buttons)
        hint = QLabel("Ctrl/Shift-click to pick several. Added items get covers and scores like the "
                      "rest of your library, and move out of this list on their own when a scan "
                      "finds them on disk.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.text.textChanged.connect(self._update_buttons)
        self._update_buttons()

    # ------------------------------------------------------------------ search

    def _search(self) -> None:
        text = self.text.text().strip()
        self.category = self.kind.currentData()
        if len(text) < 2:
            return
        self._search_no += 1
        self.results.clear()
        self._candidates = []
        self.status.setText(f"Searching for “{text}”…")
        with db.session() as conn:
            settings = db.all_settings(conn)
        job = _Search(self._search_no, providers.Query(text, category=self.category, settings=settings))
        job.signals.results.connect(self._show_results)
        job.signals.failed.connect(self._show_error)
        job.signals.thumb.connect(self._show_thumb)
        QThreadPool.globalInstance().start(job)

    def _show_results(self, number: int, source: str, found: list) -> None:
        if number != self._search_no:
            return  # an older search finishing late
        self._candidates = found
        placeholder = QPixmap(THUMB)
        placeholder.fill(Qt.transparent)
        for c in found:
            year = f" ({c.year})" if c.year else ""
            item = QListWidgetItem(QIcon(placeholder), f"{c.title}{year}\n{c.detail}")
            item.setToolTip(c.url or "")
            self.results.addItem(item)
        self.status.setText(f"{len(found)} results from {source}" if found
                            else f"Nothing found{' on ' + source if source else ''}. "
                                 "You can still add it as typed.")
        self._update_buttons()

    def _show_error(self, number: int, message: str) -> None:
        if number == self._search_no:
            self.status.setText(f"Search failed: {message}. You can still add it as typed.")

    def _show_thumb(self, number: int, row: int, img: QImage) -> None:
        if number == self._search_no and row < self.results.count():
            self.results.item(row).setIcon(QIcon(QPixmap.fromImage(img)))

    # ------------------------------------------------------------------ add

    def _update_buttons(self) -> None:
        text = self.text.text().strip()
        self.add_btn.setEnabled(bool(self.results.selectedItems()))
        self.add_typed.setText(f"Add “{text}” as typed" if text else "Add as typed")
        self.add_typed.setEnabled(len(text) >= 2)

    def _add_selected(self) -> None:
        rows = sorted(self.results.row(i) for i in self.results.selectedItems())
        with db.session() as conn:
            for r in rows:
                c = self._candidates[r]
                self.added.append(library.add_wanted(conn, self.category, c.title, c.year,
                                                     c.steam_appid, c.url))
        if self.added:
            self.accept()

    def _add_as_typed(self) -> None:
        with db.session() as conn:
            self.added.append(library.add_wanted(conn, self.kind.currentData(), self.text.text()))
        self.category = self.kind.currentData()
        self.accept()
