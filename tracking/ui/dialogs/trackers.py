"""Export for other trackers: pick the formats and a folder."""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ... import exporters

HOWTO = ("<b>MyAnimeList</b>: Profile › Import, choose “MyAnimeList Import” and this .xml "
         "(AniList and Kitsu import it too). <b>Letterboxd</b>: Settings › Import & Export › "
         "Import, once for the diary and once for the watchlist.")


class TrackersDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export for other trackers")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        self.boxes: dict[str, QCheckBox] = {}
        for key, label in exporters.KINDS.items():
            box = QCheckBox(label)
            box.setChecked(True)
            box.toggled.connect(self._update)
            self.boxes[key] = box
            lay.addWidget(box)
        row = QHBoxLayout()
        self.path = QLineEdit(os.path.join(os.path.expanduser("~"), "Desktop"))
        pick = QPushButton("Folder…")
        pick.clicked.connect(self._pick)
        row.addWidget(QLabel("Save to"))
        row.addWidget(self.path, 1)
        row.addWidget(pick)
        lay.addLayout(row)
        howto = QLabel(HOWTO)
        howto.setWordWrap(True)
        howto.setObjectName("Muted")
        lay.addWidget(howto)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Export")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._update()

    @property
    def folder(self) -> str:
        return self.path.text().strip()

    def kinds(self) -> set[str]:
        return {k for k, b in self.boxes.items() if b.isChecked()}

    def _pick(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Export to", self.folder)
        if d:
            self.path.setText(d)

    def _update(self, *_) -> None:
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.kinds()))
