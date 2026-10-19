"""Full-page view of one item: completion, scores, play time, summary, edits."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import categories, db, library, platforms
from . import theme
from .grid import fmt_hours, posters

POSTER_W, POSTER_H = 300, 430


def scores_fmt(e: db.EpisodeRow) -> str:
    """Episode score as its site shows it: "8.4" (of 10) or "4.5/5" (MAL's poll)."""
    return f"{e.score:.1f}" if (e.score_max or 10) == 10 else f"{e.score:.1f}/{e.score_max:g}"
SCORES_PER_ROW = 5



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

        # Episodes (series only): number, title, score, air date, on disk.
        self.episodes_title = _section("Episodes")
        right.addWidget(self.episodes_title)
        self.episodes_info = QLabel()
        self.episodes_info.setObjectName("Muted")
        right.addWidget(self.episodes_info)
        self.episodes = QTableWidget(0, 5)
        self.episodes.setObjectName("Episodes")
        self.episodes.setHorizontalHeaderLabels(["#", "Title", "Score", "Aired", "On disk"])
        self.episodes.verticalHeader().hide()
        self.episodes.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.episodes.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.episodes.setShowGrid(False)
        self.episodes.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = self.episodes.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (0, 2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.episodes.cellDoubleClicked.connect(self._open_episode)
        self.episodes.setToolTip("Double-click an episode to open its page")
        right.addWidget(self.episodes)

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
        for c in categories.CATEGORIES:
            self.e_category.addItem(c.label, c.key)
        self.e_platform = QComboBox()
        self.e_platform.addItem("Unknown", None)
        for p in platforms.NAMES:
            self.e_platform.addItem(p, p)
        self.e_category.currentIndexChanged.connect(
            lambda: self.e_platform.setEnabled(categories.get(self.e_category.currentData()).platforms))
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
        cat = categories.get(item.category)
        bits = [cat.singular]
        if cat.episodic and item.kind:
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
        if item.scores:
            summary = QLabel(f"Average <b>{item.avg_score / 10:.1f}</b> · highest "
                             f"<b>{item.best_score / 10:.1f}</b> (out of 10, from {len(item.scores)} "
                             f"site{'s' if len(item.scores) > 1 else ''})")
            summary.setObjectName("Muted")
            self.scores.addWidget(summary, (len(item.scores) - 1) // SCORES_PER_ROW + 1, 0, 1,
                                  SCORES_PER_ROW)

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
                episodic = categories.get(item.category).episodic
                cells.append(("Episodes" if episodic else "Files", str(item.video_count)))
                if episodic and avg:
                    cells.append(("Per episode", f"{avg:.0f} min"))
        for col, (label, value) in enumerate(cells):
            v = QLabel(value)
            v.setObjectName("Big")
            lab = QLabel(label)
            lab.setObjectName("Muted")
            self.time_grid.addWidget(v, 0, col)
            self.time_grid.addWidget(lab, 1, col)

        self.summary.setText(item.summary or "No summary.")
        self._fill_episodes(item)

        self.e_title.setText(item.title)
        self.e_title.setPlaceholderText(item.detected_title)
        self.e_category.setCurrentIndex(max(0, self.e_category.findData(item.category)))
        idx = self.e_platform.findData(item.platform) if item.platform else 0
        self.e_platform.setCurrentIndex(max(0, idx))
        self.e_platform.setEnabled(categories.get(item.category).platforms)
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

    def _fill_episodes(self, item: db.Item) -> None:
        with db.session() as conn:
            eps = db.get_episodes(conn, item.id)
        show = categories.get(item.category).episodic and item.kind != "movie"
        for w in (self.episodes_title, self.episodes_info, self.episodes):
            w.setVisible(show)
        if not show:
            return
        self.episodes.setRowCount(len(eps))
        self._episode_urls = [e.url for e in eps]
        for row, e in enumerate(eps):
            score = QTableWidgetItem(scores_fmt(e) if e.score is not None else "–")
            if e.score is not None:
                score.setForeground(theme.rating_color(e.score, e.score_max))
            title = e.title or ""
            if e.note:
                title += f"  ({e.note})"
            cells = [QTableWidgetItem(f"{e.number}"), QTableWidgetItem(title), score,
                     QTableWidgetItem(e.aired or ""), QTableWidgetItem("✓" if e.on_disk else "")]
            for col, cell in enumerate(cells):
                if col != 1:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.episodes.setItem(row, col, cell)
        row_h = self.episodes.verticalHeader().defaultSectionSize()
        self.episodes.setFixedHeight(self.episodes.horizontalHeader().height()
                                     + row_h * min(max(len(eps), 1), 14) + 4)
        if not eps:
            self.episodes_info.setText("No episode list yet. TVmaze (TV) and MyAnimeList (anime) "
                                       "provide one without keys; AniDB / TMDB with a key. "
                                       "Use Refetch to try again.")
            return
        scored = [e for e in eps if e.score is not None]
        bits = [f"{len(eps)} episodes from {eps[0].source}",
                f"{sum(e.on_disk for e in eps)} on disk"]
        if scored:
            avg = sum(e.score / (e.score_max or 10) for e in scored) / len(scored) * 10
            top = max(scored, key=lambda e: e.score / (e.score_max or 10))
            bits.insert(1, f"average {avg:.1f}/10")
            bits.insert(2, f"best: #{top.number} {top.title or ''} ({scores_fmt(top)})")
        self.episodes_info.setText("  ·  ".join(bits))

    def _open_episode(self, row: int, _col: int) -> None:
        url = self._episode_urls[row] if row < len(self._episode_urls) else None
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _set_done(self, done: bool, when: str | None) -> None:
        self.done_btn.setChecked(done)
        verb = categories.get(self.item.category if self.item else None).done
        self.done_btn.setText(f"✓  {verb}" if done else f"Mark as {verb.lower()}")
        self.done_when.setText(f"on {when[:10]}" if done and when else "")

    # ------------------------------------------------------------------ actions

    def _toggle_done(self, checked: bool) -> None:
        with db.session() as conn:
            library.set_completed(conn, [self.item.id], checked)
            item = db.get_item(conn, self.item.id)
        self.item = item
        self._set_done(item.completed, item.completed_at)
        self.changed.emit(item.id)

    def _save(self) -> None:
        with db.session() as conn:
            lookup_changed = library.edit_item(
                conn, self.item, title=self.e_title.text(), category=self.e_category.currentData(),
                platform=self.e_platform.currentData(), year=self.e_year.value() or None,
                notes=self.e_notes.toPlainText())
        self.changed.emit(self.item.id)
        if lookup_changed:
            self.refetch.emit(self.item.id)

    def _delete(self) -> None:
        if QMessageBox.question(
            self, "Remove from library",
            f"Remove “{self.item.title}” from the library?\nFiles on disk are not touched; "
            "a rescan of its location will add it back.",
        ) != QMessageBox.Yes:
            return
        with db.session() as conn:
            library.remove_items(conn, [self.item.id])
        self.deleted.emit(self.item.id)
