"""Full-page view of one item: completion, scores, play time, summary, edits."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from .. import db, paths
from . import dialogs, theme
from .grid import fmt_hours, posters

POSTER_W, POSTER_H = 300, 430
SCORES_PER_ROW = 5
CATEGORY_NAMES = {"anime": "Anime", "movie": "Movie", "game": "Game", "unknown": "Unknown"}


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def fmt_score(value: float, max_value: float) -> str:
    if max_value == 100:
        return f"{value:.0f}%" if value <= 100 else f"{value:.0f}"
    return f"{value:.1f}/{max_value:.0f}" if max_value != 10 else f"{value:.1f}"


def _section(title: str) -> QLabel:
    lab = QLabel(title.upper())
    lab.setObjectName("Section")
    return lab


class ScoreChip(QFrame):
    def __init__(self, score: db.Score):
        super().__init__()
        self.setObjectName("Chip")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(0)
        color = theme.rating_color(score.value, score.max_value).name()
        val = QLabel(f"<span style='color:{color}'>{fmt_score(score.value, score.max_value)}</span>")
        val.setObjectName("ChipValue")
        src = QLabel(f"<a style='color:#9096a3;text-decoration:none' href='{score.url}'>{score.source} ↗</a>"
                     if score.url else score.source)
        src.setObjectName("Muted")
        src.setOpenExternalLinks(True)
        lay.addWidget(val)
        lay.addWidget(src)


class ItemPage(QScrollArea):
    back = Signal()
    changed = Signal(int)            # item edited (reload it)
    refetch = Signal(int)            # fetch metadata again
    open_folder = Signal(int)
    deleted = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setObjectName("ItemPage")
        self.item: db.Item | None = None

        body = QWidget()
        body.setObjectName("ItemPage")
        outer = QVBoxLayout(body)
        outer.setContentsMargins(24, 16, 24, 24)

        top = QHBoxLayout()
        back = QPushButton("←  Back")
        back.clicked.connect(self.back.emit)
        back.setShortcut("Esc")
        top.addWidget(back)
        top.addStretch(1)
        outer.addLayout(top)

        cols = QHBoxLayout()
        cols.setSpacing(32)
        outer.addLayout(cols, 1)

        # --- left: poster + actions
        left = QVBoxLayout()
        self.poster = QLabel()
        self.poster.setFixedSize(POSTER_W, POSTER_H)
        left.addWidget(self.poster)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.clicked.connect(lambda: self.open_folder.emit(self.item.id))
        refetch = QPushButton("Refetch metadata")
        refetch.clicked.connect(lambda: self.refetch.emit(self.item.id))
        delete = QPushButton("Remove from library")
        delete.clicked.connect(self._delete)
        for b in (self.open_btn, refetch, delete):
            left.addWidget(b)
        self.location = QLabel()
        self.location.setObjectName("Muted")
        self.location.setWordWrap(True)
        self.location.setFixedWidth(POSTER_W)
        self.location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left.addWidget(self.location)
        left_wrap = QWidget()
        left_wrap.setLayout(left)
        left_wrap.setFixedWidth(POSTER_W)
        left.setContentsMargins(0, 0, 0, 0)
        cols.addWidget(left_wrap, 0, Qt.AlignTop)

        # --- right: info
        right = QVBoxLayout()
        right.setSpacing(12)
        self.title = QLabel()
        self.title.setObjectName("PageTitle")
        self.title.setWordWrap(True)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("Muted")
        right.addWidget(self.title)
        right.addWidget(self.subtitle)

        done_row = QHBoxLayout()
        self.done_btn = QPushButton()
        self.done_btn.setCheckable(True)
        self.done_btn.setObjectName("Done")
        self.done_btn.clicked.connect(self._toggle_done)
        self.done_when = QLabel()
        self.done_when.setObjectName("Muted")
        done_row.addWidget(self.done_btn)
        done_row.addWidget(self.done_when)
        done_row.addStretch(1)
        right.addLayout(done_row)

        right.addWidget(_section("Scores"))
        self.scores = QGridLayout()  # wraps after SCORES_PER_ROW chips
        self.scores.setSpacing(10)
        scores_wrap = QWidget()
        scores_wrap.setLayout(self.scores)
        right.addWidget(scores_wrap)

        right.addWidget(_section("Time"))
        self.time_grid = QGridLayout()
        self.time_grid.setHorizontalSpacing(28)
        time_wrap = QWidget()
        time_wrap.setLayout(self.time_grid)
        right.addWidget(time_wrap)

        right.addWidget(_section("Summary"))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        right.addWidget(self.summary)

        right.addWidget(_section("Details"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.e_title = QLineEdit()
        self.e_category = QComboBox()
        for k, v in (("anime", "Anime"), ("movie", "Movies"), ("game", "Games"), ("unknown", "Unknown")):
            self.e_category.addItem(v, k)
        self.e_platform = QComboBox()
        self.e_platform.addItem("Unknown", None)
        for p in dialogs.PLATFORMS:
            self.e_platform.addItem(p, p)
        self.e_category.currentIndexChanged.connect(
            lambda: self.e_platform.setEnabled(self.e_category.currentData() == "game"))
        self.e_year = QSpinBox()
        self.e_year.setRange(0, 2100)
        self.e_year.setSpecialValueText("—")
        self.e_notes = QPlainTextEdit()
        self.e_notes.setPlaceholderText("Your notes…")
        self.e_notes.setFixedHeight(90)
        form.addRow("Title / search term", self.e_title)
        form.addRow("Category", self.e_category)
        form.addRow("Platform", self.e_platform)
        form.addRow("Year", self.e_year)
        form.addRow("Notes", self.e_notes)
        right.addLayout(form)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("Save changes")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn)
        right.addLayout(save_row)
        right.addStretch(1)
        cols.addLayout(right, 1)
        self.setWidget(body)

    # ------------------------------------------------------------------ fill

    def show_item(self, item: db.Item, mount: str | None, drive_label: str) -> None:
        self.item = item
        with db.session() as conn:
            scores = db.get_scores(conn, item.id)

        pm = posters.get(item.poster, POSTER_W, POSTER_H)
        if pm:
            pm = pm.copy((pm.width() - POSTER_W) // 2, (pm.height() - POSTER_H) // 2, POSTER_W, POSTER_H)
        self.poster.setPixmap(pm or posters.placeholder(item, POSTER_W, POSTER_H))

        self.title.setText(item.title)
        bits = [CATEGORY_NAMES.get(item.category, item.category)]
        if item.category == "anime" and item.kind:
            bits.append("Movie" if item.kind == "movie" else f"Series · season {item.season or 1}")
        if item.platform:
            bits.append(item.platform)
        if item.year:
            bits.append(str(item.year))
        if item.external_url:
            bits.append(f"<a style='color:#5b8cff' href='{item.external_url}'>web page ↗</a>")
        self.subtitle.setText("  ·  ".join(bits))
        self.subtitle.setOpenExternalLinks(True)

        self._set_done(item.completed, item.completed_at)

        while self.scores.count():
            w = self.scores.takeAt(0).widget()
            if w:
                w.deleteLater()
        for n, sc in enumerate(scores):
            self.scores.addWidget(ScoreChip(sc), n // SCORES_PER_ROW, n % SCORES_PER_ROW)
        if not scores:
            none = QLabel("No scores yet. Add API keys in Settings, then Refetch.")
            none.setObjectName("Muted")
            self.scores.addWidget(none, 0, 0)
        self.scores.setColumnStretch(SCORES_PER_ROW, 1)

        while self.time_grid.count():
            w = self.time_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        if item.category == "game":
            rows = [("Main story", item.hltb_hours), ("Main + extras", item.hltb_extra),
                    ("Completionist", item.hltb_complete)]
            cells = [(label, f"{h:.1f} h" if h else "—") for label, h in rows]
        else:
            cells = [("Total runtime", fmt_hours(item.total_hours, item.category) or "—")]
            if item.video_count and item.kind != "movie":
                avg = (item.duration_sec or 0) / item.video_count / 60
                cells.append(("Episodes" if item.category == "anime" else "Files", str(item.video_count)))
                if item.category == "anime" and avg:
                    cells.append(("Per episode", f"{avg:.0f} min"))
        for col, (label, value) in enumerate(cells):
            v = QLabel(value)
            v.setObjectName("Big")
            lab = QLabel(label)
            lab.setObjectName("Muted")
            self.time_grid.addWidget(v, 0, col)
            self.time_grid.addWidget(lab, 1, col)

        self.summary.setText(item.summary or "No summary.")

        self.e_title.setText(item.title)
        self.e_title.setPlaceholderText(item.detected_title)
        self.e_category.setCurrentIndex(max(0, self.e_category.findData(item.category)))
        idx = self.e_platform.findData(item.platform) if item.platform else 0
        self.e_platform.setCurrentIndex(max(0, idx))
        self.e_platform.setEnabled(item.category == "game")
        self.e_year.setValue(item.year or 0)
        self.e_notes.setPlainText(item.notes or "")

        if item.missing:
            status = "not found on its last scan"
        elif mount:
            status = f"online at {mount}"
        else:
            status = "drive not connected"
        path = "/" + item.rel_path.replace("/", "/​")
        self.location.setText(
            f"<b>{drive_label}</b> — {status}<br>{path}<br>"
            f"{item.file_count} files · {fmt_size(item.size_bytes)}"
            + (f"<br><i>Detected: {item.detected_how}</i>" if item.detected_how else ""))
        self.open_btn.setEnabled(bool(mount) and not item.missing)

    def _set_done(self, done: bool, when: str | None) -> None:
        self.done_btn.setChecked(done)
        verb = {"game": "Beaten", "anime": "Watched", "movie": "Watched"}.get(
            self.item.category if self.item else "", "Completed")
        self.done_btn.setText(f"✓  {verb}" if done else f"Mark as {verb.lower()}")
        self.done_when.setText(f"on {when[:10]}" if done and when else "")

    # ------------------------------------------------------------------ actions

    def _toggle_done(self, checked: bool) -> None:
        with db.session() as conn:
            db.set_completed(conn, [self.item.id], checked)
            item = db.get_item(conn, self.item.id)
        self.item = item
        self._set_done(item.completed, item.completed_at)
        self.changed.emit(item.id)

    def _save(self) -> None:
        it = self.item
        title = self.e_title.text().strip()
        category = self.e_category.currentData()
        platform = self.e_platform.currentData() if category == "game" else None
        year = self.e_year.value() or None
        lookup_changed = (title != it.title or category != it.category
                          or platform != it.platform or year != it.year)
        with db.session() as conn:
            conn.execute(
                "UPDATE items SET user_title=?, user_category=?, user_platform=?, year=?, notes=? "
                "WHERE id=?",
                (title if title and title != it.detected_title else None, category, platform,
                 year, self.e_notes.toPlainText().strip() or None, it.id),
            )
        self.changed.emit(it.id)
        if lookup_changed:
            self.refetch.emit(it.id)

    def _delete(self) -> None:
        if QMessageBox.question(
            self, "Remove from library",
            f"Remove “{self.item.title}” from the library?\nFiles on disk are not touched; "
            "a rescan of its location will add it back.",
        ) != QMessageBox.Yes:
            return
        with db.session() as conn:
            conn.execute("DELETE FROM items WHERE id=?", (self.item.id,))
        if self.item.poster:
            (paths.posters_dir() / self.item.poster).unlink(missing_ok=True)
        self.deleted.emit(self.item.id)
