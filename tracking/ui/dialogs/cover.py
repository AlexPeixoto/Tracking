"""Search cover…: look a game up on IGDB with any name and pick one of its covers
(each edition / port has its own). The cover is downloaded by whoever opened this."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QThreadPool
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ... import db, providers
from ...providers import igdb
from .want import _Search

COVER = QSize(132, 176)


class CoverDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Search cover on IGDB · {title}")
        self.resize(820, 680)
        self.url: str | None = None  # the picked cover, once accepted
        self._no = 0
        self._found: list[providers.Candidate] = []
        with db.session() as conn:
            self.settings = db.all_settings(conn)

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.text = QLineEdit(title)
        self.text.returnPressed.connect(self._search)
        go = QPushButton("Search")
        go.clicked.connect(self._search)
        row.addWidget(self.text, 1)
        row.addWidget(go)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.results = QListWidget()
        self.results.setViewMode(QListView.IconMode)
        self.results.setIconSize(COVER)
        self.results.setGridSize(QSize(COVER.width() + 24, COVER.height() + 58))
        self.results.setResizeMode(QListView.Adjust)
        self.results.setMovement(QListView.Static)
        self.results.setWordWrap(True)
        self.results.setSpacing(4)
        self.results.itemDoubleClicked.connect(lambda _: self._use())
        self.results.itemSelectionChanged.connect(
            lambda: self.use_btn.setEnabled(bool(self.results.selectedItems())))
        lay.addWidget(self.results, 1)

        buttons = QHBoxLayout()
        self.use_btn = QPushButton("Use this cover")
        self.use_btn.setObjectName("Primary")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self._use)
        close = QPushButton("Cancel")
        close.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(self.use_btn)
        buttons.addWidget(close)
        lay.addLayout(buttons)

        if igdb.configured(self.settings):
            self._search()
        else:
            go.setEnabled(False)
            self.status.setText("IGDB needs a free Client ID and Secret: make a Twitch app at "
                                "dev.twitch.tv/console/apps, then add both in Settings › Games.")

    def _search(self) -> None:
        text = self.text.text().strip()
        if len(text) < 2 or not igdb.configured(self.settings):
            return
        self._no += 1
        self.results.clear()
        self._found = []
        self.status.setText("Searching IGDB…")
        job = _Search(self._no, providers.Query(text, category="game", settings=self.settings),
                      search=lambda q: ("IGDB", igdb.search_covers(q.title, self.settings)), size=COVER)
        job.signals.results.connect(self._show)
        job.signals.failed.connect(lambda n, e: n == self._no and self.status.setText(f"Search failed: {e}"))
        job.signals.thumb.connect(self._thumb)
        QThreadPool.globalInstance().start(job)

    def _show(self, number: int, _source: str, found: list) -> None:
        if number != self._no:
            return
        self._found = found
        blank = QPixmap(COVER)
        blank.fill(Qt.transparent)
        for c in found:
            year = f" ({c.year})" if c.year else ""
            it = QListWidgetItem(QIcon(blank), f"{c.title}{year}")
            it.setToolTip(f"{c.title}{year}\n{c.detail}" if c.detail else f"{c.title}{year}")
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.results.addItem(it)
        self.status.setText(f"{len(found)} covers. Double-click one to use it." if found else
                            "Nothing found. Try the original or store name, or fewer words.")

    def _thumb(self, number: int, row: int, img: QImage) -> None:
        if number == self._no and row < self.results.count():
            self.results.item(row).setIcon(QIcon(QPixmap.fromImage(img)))

    def _use(self) -> None:
        rows = [self.results.row(i) for i in self.results.selectedItems()]
        if rows:
            self.url = self._found[rows[0]].image_url
            self.accept()
