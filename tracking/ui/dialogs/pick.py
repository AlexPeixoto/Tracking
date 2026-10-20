"""What should I play? One suggestion at a time from Up next and / or the Backlog,
narrowed by section, length and what's on connected drives."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ... import categories, db, library
from ..grid import cover_pixmap, fmt_hours

SETTING = "pick_options"
COVER_W, COVER_H = 210, 294


class PickDialog(QDialog):
    open_item = Signal(int)
    play = Signal(int)
    plan = Signal(list, str)          # item ids, plan state

    def __init__(self, items: list[db.Item], online: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("What should I play?")
        self.items, self.online = items, online
        self.current: db.Item | None = None
        self.recent: list[int] = []   # don't suggest the same thing twice in a row
        with db.session() as conn:
            try:
                saved = json.loads(db.get_setting(conn, SETTING) or "{}")
            except ValueError:
                saved = {}

        form = QFormLayout()
        self.source = QComboBox()
        for key, label in library.PICK_FROM.items():
            self.source.addItem(label, key)
        self.source.setCurrentIndex(max(0, self.source.findData(saved.get("source", "both"))))
        sections_row = QHBoxLayout()
        self.sections: dict[str, QCheckBox] = {}
        for c in categories.CATEGORIES:
            if c.key == "unknown":
                continue
            box = QCheckBox(c.label)
            box.setChecked(not saved.get("sections") or c.key in saved["sections"])
            self.sections[c.key] = box
            sections_row.addWidget(box)
        self.time = QComboBox()
        for key, (label, _, _) in library.PICK_TIME.items():
            self.time.addItem(label, key)
        self.time.setCurrentIndex(max(0, self.time.findData(saved.get("time", "any"))))
        self.connected = QCheckBox("Only what's on connected drives")
        self.connected.setChecked(saved.get("connected", False))
        self.favour = QCheckBox("Favour high scores")
        self.favour.setChecked(saved.get("favour", True))
        form.addRow("From", self.source)
        form.addRow("Sections", sections_row)
        form.addRow("Length", self.time)
        form.addRow("", self.connected)
        form.addRow("", self.favour)

        self.cover = QLabel()
        self.cover.setFixedSize(COVER_W, COVER_H)
        self.cover.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 16pt; font-weight: 600;")
        self.detail = QLabel()
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        self.pool = QLabel()
        self.pool.setObjectName("Muted")
        info = QVBoxLayout()
        info.addWidget(self.title)
        info.addWidget(self.detail)
        info.addStretch(1)
        info.addWidget(self.pool)
        card = QHBoxLayout()
        card.addWidget(self.cover)
        card.addLayout(info, 1)

        buttons = QHBoxLayout()
        again = QPushButton("🎲  Pick again")
        again.setObjectName("Primary")
        again.clicked.connect(self.roll)
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(lambda: self._send(self.open_item))
        self.play_btn = QPushButton("▶  Play")
        self.play_btn.clicked.connect(lambda: self._send(self.play))
        self.start_btn = QPushButton("Start it (Playing)")
        self.start_btn.setToolTip("Move it to Playing / Watching on the Planning board")
        self.start_btn.clicked.connect(lambda: self._plan(library.PLAN_PLAYING))
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for b in (again, self.open_btn, self.play_btn, self.start_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addSpacing(8)
        lay.addLayout(card)
        lay.addLayout(buttons)
        self.resize(640, 0)

        for w in (self.source, self.time):
            w.currentIndexChanged.connect(self.roll)
        for w in (self.connected, self.favour, *self.sections.values()):
            w.toggled.connect(self.roll)
        self.roll()

    def options(self) -> dict:
        chosen = [k for k, b in self.sections.items() if b.isChecked()]
        return {"source": self.source.currentData(), "time": self.time.currentData(),
                "sections": [] if len(chosen) == len(self.sections) else chosen,
                "connected": self.connected.isChecked(), "favour": self.favour.isChecked()}

    def roll(self, *_) -> None:
        o = self.options()
        with db.session() as conn:
            db.set_setting(conn, SETTING, json.dumps(o))
        pool = library.pick_candidates(self.items, o["source"], set(o["sections"]) or None, o["time"],
                                       self.online if o["connected"] else None)
        fresh = [i for i in pool if i.id not in self.recent] or pool
        self.current = library.pick(fresh, o["favour"])
        self.pool.setText(f"Picking from {len(pool)} " + ("thing" if len(pool) == 1 else "things"))
        self._show()

    def _show(self) -> None:
        i = self.current
        for b in (self.open_btn, self.play_btn, self.start_btn):
            b.setEnabled(i is not None)
        if i is None:
            self.cover.clear()
            self.title.setText("Nothing matches")
            self.detail.setText("Loosen the length or sections, or add things to Up next.")
            return
        self.recent = [i.id, *self.recent][:5]
        pm = cover_pixmap(i.poster, COVER_W, COVER_H)
        if pm:
            self.cover.setPixmap(pm)
        else:
            self.cover.setText(i.title)  # no cover: the name in its place
        self.title.setText(i.title)
        bits = [categories.get(i.category).singular]
        if i.platform:
            bits.append(i.platform)
        left = library.remaining_hours(i)
        if left:
            bits.append(f"~{fmt_hours(left, 'movie')} left" if i.category != "game"
                        else f"~{left:.0f} h left")
        if i.avg_score is not None:
            bits.append(f"★ {i.avg_score / 10:.1f}")
        why = "Next in your queue" if i.plan_state == library.PLAN_NEXT else "From your backlog"
        self.detail.setText("  ·  ".join(bits) + f"\n{why}")
        self.play_btn.setEnabled(i.category == "game" or i.video_count > 0)

    def _send(self, signal) -> None:
        if self.current:
            signal.emit(self.current.id)
            self.accept()

    def _plan(self, state: str) -> None:
        if self.current:
            self.plan.emit([self.current.id], state)
            self.accept()
