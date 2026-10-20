"""The log viewer: recent log lines with level and text filters."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ... import log
from .settings import open_in_explorer


class LogDialog(QDialog):
    """Recent log lines, filterable by level and text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Log")
        self.resize(1100, 640)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.min_level = QComboBox()
        for lvl in log.LEVELS:
            self.min_level.addItem(f"{lvl} and above", lvl)
        self.min_level.currentIndexChanged.connect(self._fill)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter (e.g. a title or folder name)…")
        self.filter.textChanged.connect(self._fill)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._load)
        folder = QPushButton("Open log folder")
        folder.clicked.connect(self._open_folder)
        top.addWidget(self.min_level)
        top.addWidget(self.filter, 1)
        top.addWidget(refresh)
        top.addWidget(folder)
        lay.addLayout(top)
        self.view = QPlainTextEdit()
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.view)
        hint = QLabel("Set the level to DEBUG in Settings › General and rescan: every "
                      "classification decision then shows up here with its reason.")
        hint.setObjectName("Muted")
        lay.addWidget(hint)
        self._load()

    def _open_folder(self):
        open_in_explorer(log.log_dir())

    def _load(self):
        self.lines = log.tail(5000)
        self._fill()

    def _fill(self):
        want = log.LEVELS.index(self.min_level.currentData())
        needle = self.filter.text().strip().lower()
        out = []
        for ln in self.lines:
            parts = ln.split(None, 3)
            lvl = parts[2] if len(parts) > 2 else "INFO"
            if lvl in log.LEVELS and log.LEVELS.index(lvl) < want:
                continue
            if needle and needle not in ln.lower():
                continue
            out.append(ln.rstrip("\n"))
        self.view.setPlainText("\n".join(out))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
