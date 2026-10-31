"""Share your library: a package the Android app (and anyone you send it to) can browse."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from ... import package

ABOUT = ("A read-only copy of your library for the Tracking app on Android, or to send to "
         "someone: titles, covers, scores, episodes, what you finished and your Planning board. "
         "It leaves out file paths, drive names, notes, settings and API keys. Copy it to the "
         "phone (USB, Google Drive, a chat) and open it in the app.")


class ShareDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Share library (Android app)")
        self.setMinimumWidth(560)
        form = QFormLayout(self)
        about = QLabel(ABOUT)
        about.setWordWrap(True)
        about.setObjectName("Muted")
        form.addRow(about)
        self.name = QLineEdit("My library")
        form.addRow("Name", self.name)
        self.covers = QCheckBox("Covers (made small for phones; most of the size)")
        self.covers.setChecked(True)
        form.addRow("", self.covers)
        self.wishlist = QCheckBox("Want to acquire list")
        self.wishlist.setChecked(True)
        form.addRow("", self.wishlist)
        row = QHBoxLayout()
        self.path = QLineEdit(os.path.join(os.path.expanduser("~"), "Desktop", "library" + package.SUFFIX))
        pick = QPushButton("Save as…")
        pick.clicked.connect(self._pick)
        row.addWidget(self.path, 1)
        row.addWidget(pick)
        form.addRow("File", row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _pick(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save library package", self.path.text(),
                                              f"Library package (*{package.SUFFIX} *.zip)")
        if path:
            self.path.setText(path)

    @property
    def dest(self) -> Path:
        p = self.path.text().strip()
        return Path(p if p.lower().endswith(".zip") else p + package.SUFFIX)
