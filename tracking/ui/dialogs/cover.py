"""Search cover…: look a game up with any name and pick a cover, from every service that has
covers (providers/covers.py; IGDB too when its key is set). The cover is downloaded by whoever
opened this."""

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
from ...providers import covers, igdb
from .want import _Search

COVER = QSize(132, 176)


class CoverDialog(QDialog):
    def __init__(self, title: str, parent=None, platform: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Search cover · {title}")
        self.platform = platform
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

        self._search()

    def _search(self) -> None:
        text = self.text.text().strip()
        if len(text) < 2:
            return
        self._no += 1
        self.results.clear()
        self._found = []
        self.status.setText("Searching…")
        job = _Search(self._no, providers.Query(text, category="game", platform=self.platform,
                                                settings=self.settings),
                      search=lambda q: covers.search(q.title, q.platform, self.settings), size=COVER)
        job.signals.results.connect(self._show)
        job.signals.failed.connect(lambda n, e: n == self._no and self.status.setText(f"Search failed: {e}"))
        job.signals.thumb.connect(self._thumb)
        QThreadPool.globalInstance().start(job)

    def _show(self, number: int, source: str, found: list) -> None:
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
        text = (f"{len(found)} covers from {source}. Double-click one to use it." if found else
                "Nothing found. Try the original or store name, or fewer words.")
        if not igdb.configured(self.settings):
            text += (" For more (a cover per edition and port), add a free IGDB Client ID and Secret "
                     "in Settings › Games (a Twitch app from dev.twitch.tv/console/apps).")
        self.status.setText(text)

    def _thumb(self, number: int, row: int, img: QImage) -> None:
        if number == self._no and row < self.results.count():
            self.results.item(row).setIcon(QIcon(QPixmap.fromImage(img)))

    def _use(self) -> None:
        rows = [self.results.row(i) for i in self.results.selectedItems()]
        if rows:
            self.url = self._found[rows[0]].image_url
            self.accept()
