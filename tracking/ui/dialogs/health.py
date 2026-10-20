"""Library health: what could use a look (no cover, no score, no platform, duplicates,
missing files), each with a button to show those items in the grid or fetch them again."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QLabel, QPushButton, QVBoxLayout,
)

from ... import library


class HealthDialog(QDialog):
    show_items = Signal(str, list)     # heading, item ids
    refetch = Signal(list)             # item ids

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library health")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        intro = QLabel("Things in your library that could use a look. <b>Show</b> lists them in "
                       "the main window, where you can fix several at once.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        for row, check in enumerate(library.health(items)):
            n = len(check.ids)
            count = QLabel(f"<span style='font-size:15pt; font-weight:600'>{n}</span>")
            count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            hint = check.hint.replace("\n", "<br>")
            # No word wrap: wrapped labels in a grid get too little height and are cut off.
            text = QLabel(f"<b>{check.label}</b><br><span style='color:#8a8f9c'>{hint}</span>")
            grid.addWidget(count, row, 0)
            grid.addWidget(text, row, 1)
            show = QPushButton("Show")
            show.setEnabled(n > 0)
            show.clicked.connect(lambda _=False, c=check: self._show(c))
            grid.addWidget(show, row, 2)
            if check.fetch:
                again = QPushButton("Fetch again")
                again.setEnabled(n > 0)
                again.setToolTip("Look these up again (in the background)")
                again.clicked.connect(lambda _=False, c=check: self._refetch(c))
                grid.addWidget(again, row, 3)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _show(self, check: library.HealthCheck) -> None:
        self.show_items.emit(check.label, check.ids)
        self.accept()

    def _refetch(self, check: library.HealthCheck) -> None:
        self.refetch.emit(check.ids)
        self.accept()
