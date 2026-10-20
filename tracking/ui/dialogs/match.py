"""Find match: when an item found the wrong thing (or nothing), search every service
yourself with any name and pick the right result. The item is then looked up as that
title (and Steam app) from now on."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ... import db, library, providers
from .want import THUMB, _Search


class MatchDialog(QDialog):
    def __init__(self, item: db.Item, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Find match · {item.title}")
        self.resize(680, 640)
        self.item = item
        self._no = 0
        self._found: list[providers.Candidate] = []

        lay = QVBoxLayout(self)
        intro = QLabel("Search with any name (the store's spelling usually works best), then pick "
                       "the right result. The item is looked up as that from now on.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        self.text = QLineEdit(item.title)
        self.text.returnPressed.connect(self._search)
        go = QPushButton("Search")
        go.clicked.connect(self._search)
        row.addWidget(self.text, 1)
        row.addWidget(go)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        lay.addWidget(self.status)
        self.results = QListWidget()
        self.results.setIconSize(THUMB)
        self.results.setSpacing(2)
        self.results.setWordWrap(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.itemDoubleClicked.connect(lambda _: self._use())
        self.results.itemSelectionChanged.connect(
            lambda: self.use_btn.setEnabled(bool(self.results.selectedItems())))
        lay.addWidget(self.results, 1)
        buttons = QHBoxLayout()
        self.typed = QPushButton("Just search as typed")
        self.typed.setToolTip("Use the text above as the search title without picking a result")
        self.typed.clicked.connect(self._use_typed)
        self.use_btn = QPushButton("Use this match")
        self.use_btn.setObjectName("Primary")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self._use)
        close = QPushButton("Cancel")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.typed)
        buttons.addStretch(1)
        buttons.addWidget(self.use_btn)
        buttons.addWidget(close)
        lay.addLayout(buttons)
        self._search()

    def _query(self) -> providers.Query:
        with db.session() as conn:
            settings = {r["key"]: r["value"] or "" for r in conn.execute("SELECT key, value FROM settings")}
        i = self.item
        return providers.Query(self.text.text().strip(), category=i.category, kind=i.kind,
                               platform=i.platform, season=i.season, settings=settings)

    def _search(self) -> None:
        if len(self.text.text().strip()) < 2:
            return
        self._no += 1
        self.results.clear()
        self._found = []
        self.status.setText("Searching…")
        job = _Search(self._no, self._query(), search=lambda q: ("", providers.search_all(q)))
        job.signals.results.connect(self._show)
        job.signals.failed.connect(lambda n, e: self.status.setText(f"Search failed: {e}"))
        job.signals.thumb.connect(self._thumb)
        QThreadPool.globalInstance().start(job)

    def _show(self, number: int, _source: str, found: list) -> None:
        if number != self._no:
            return
        self._found = found
        blank = QPixmap(THUMB)
        blank.fill(Qt.transparent)
        for c in found:
            year = f" ({c.year})" if c.year else ""
            it = QListWidgetItem(QIcon(blank), f"{c.title}{year}\n{c.detail}")
            it.setToolTip(c.url or "")
            self.results.addItem(it)
        self.status.setText(f"{len(found)} results" if found else
                            "Nothing found. Try the original or store name, or fewer words.")

    def _thumb(self, number: int, row: int, img: QImage) -> None:
        if number == self._no and row < self.results.count():
            self.results.item(row).setIcon(QIcon(QPixmap.fromImage(img)))

    def _use(self) -> None:
        rows = [self.results.row(i) for i in self.results.selectedItems()]
        if not rows:
            return
        c = self._found[rows[0]]
        with db.session() as conn:
            library.use_match(conn, self.item, c.title, c.year, c.steam_appid)
        self.accept()

    def _use_typed(self) -> None:
        text = self.text.text().strip()
        if text:
            with db.session() as conn:
                library.use_match(conn, self.item, text)
            self.accept()
