"""Full-page view of one item: play, completion, scores, play time, summary, edits."""

from __future__ import annotations

import html
import os
from dataclasses import replace

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import categories, db, devices, enrich, library, platforms
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


class _UpcomingSignals(QObject):
    done = Signal(int, list)          # item id, [Upcoming]


class _UpcomingJob(QRunnable):
    """"Coming up" for one item, off the UI thread (it may go online)."""

    def __init__(self, item: db.Item):
        super().__init__()
        self.item = item
        self.signals = _UpcomingSignals()

    def run(self) -> None:
        from .. import upcoming

        self.signals.done.emit(self.item.id, upcoming.for_item(self.item))


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
    play = Signal(int)
    find_match = Signal(int)         # search services by hand
    search_cover = Signal(int)       # pick a cover on IGDB
    play_episode = Signal(int, object, object)  # item id, season, episode

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
        # No cover found: the most useful next step, right where the cover would be.
        self.nomatch_btn = QPushButton("Nothing found – Find match…")
        self.nomatch_btn.setObjectName("Primary")
        self.nomatch_btn.setToolTip("Search the services with another name (the store's spelling) "
                                    "and pick the right result")
        self.nomatch_btn.clicked.connect(lambda: self.find_match.emit(self.item.id))
        left.addWidget(self.nomatch_btn)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.clicked.connect(lambda: self.open_folder.emit(self.item.id))
        refetch = QPushButton("Refetch metadata")
        refetch.clicked.connect(lambda: self.refetch.emit(self.item.id))
        match = QPushButton("Find match…")
        match.setToolTip("Wrong cover / scores, or nothing found? Search with any name and pick")
        match.clicked.connect(lambda: self.find_match.emit(self.item.id))
        self.cover_btn = QPushButton("Search cover on IGDB…")
        self.cover_btn.setToolTip("Pick the cover yourself from IGDB (every edition and port); "
                                  "refetching keeps it")
        self.cover_btn.clicked.connect(lambda: self.search_cover.emit(self.item.id))
        delete = QPushButton("Remove from library")
        self.delete_btn = delete
        delete.clicked.connect(self._delete)
        for b in (self.open_btn, refetch, match, self.cover_btn, delete):
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
        self.play_btn = QPushButton("▶  Play")
        self.play_btn.setObjectName("Primary")
        self.play_btn.setToolTip("Start the game (Ctrl+Enter in the grid)")
        self.play_btn.clicked.connect(lambda: self.play.emit(self.item.id))
        self.launch_btn = QPushButton("Launch options…")
        self.launch_btn.setToolTip("Pick which file Play starts, and extra arguments")
        self.launch_btn.clicked.connect(self._launch_options)
        done_row.addWidget(self.play_btn)
        done_row.addWidget(self.launch_btn)
        done_row.addSpacing(12)
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
        self.play_info = QLabel()
        self.play_info.setObjectName("Muted")
        right.addWidget(self.play_info)
        # Coming up: next episode, airing, release date, Steam sale (filled in the background).
        self.upcoming_title = _section("Coming up")
        self.upcoming = QLabel()
        self.upcoming.setTextFormat(Qt.RichText)
        self.upcoming.setOpenExternalLinks(True)
        self.upcoming.setWordWrap(True)
        right.addWidget(self.upcoming_title)
        right.addWidget(self.upcoming)
        self._upcoming_jobs: list[_UpcomingJob] = []

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
        self.episodes.setToolTip("Double-click an episode you have to play it "
                                 "(others open their page)")
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
        self.e_store = QComboBox()
        self.e_store.setEditable(True)
        self.e_store.addItem("")
        self.e_store.addItems(library.STORES)
        self.e_store.lineEdit().setPlaceholderText("Xbox app, a console, a disc…")
        self.e_notes = QPlainTextEdit()
        self.e_notes.setPlaceholderText("Your notes…")
        self.e_notes.setFixedHeight(90)
        form.addRow("Title / search term", self.e_title)
        form.addRow("Category", self.e_category)
        form.addRow("Platform", self.e_platform)
        form.addRow("Year", self.e_year)
        self.store_label = QLabel("Played on")
        form.addRow(self.store_label, self.e_store)
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
        self.mount = mount
        with db.session() as conn:
            scores = db.get_scores(conn, item.id)

        pm = posters.get(item.poster, POSTER_W, POSTER_H)
        if pm:
            pm = pm.copy((pm.width() - POSTER_W) // 2, (pm.height() - POSTER_H) // 2, POSTER_W, POSTER_H)
        self.poster.setPixmap(pm or posters.placeholder(item, POSTER_W, POSTER_H))
        self.nomatch_btn.setVisible(not item.poster and item.source != db.STEAM)
        self.cover_btn.setVisible(item.category == "game")

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
        self._show_launch(item, mount)
        self._load_upcoming(item)

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
            if item.playtime_min is not None:
                # Steam's play time plus sessions started from here (db.ITEM_SELECT adds them)
                label = "Played" + (" (Steam)" if item.source == db.STEAM else "")
                cells.append((label, f"{item.playtime_min / 60:.1f} h"
                              if item.playtime_min >= 60 else f"{item.playtime_min:.0f} min"))
                last = max(item.last_played or "", item.last_launched or "")
                cells.append(("Last played", last[:10] or "never"))
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
        manual = self._by_hand = item.source in ("manual", db.WANTED)
        self.e_store.setVisible(manual)
        self.store_label.setVisible(manual)
        self.e_store.setEditText(item.store or "")

        wanted = item.source == db.WANTED
        self.delete_btn.setText("Remove from list" if wanted else "Remove from library")
        self.open_btn.setText("Open web page" if wanted or item.source in ("manual", "steam")
                              else "Open folder")
        if wanted:
            self.location.setText("<b>On your Want to acquire list</b><br>Use “Got it” when you have "
                                  "it, or it moves to your library by itself when a scan finds it.")
            self.open_btn.setEnabled(bool(item.external_url))
            return
        if item.source == "manual":
            where = f"Played on <b>{html.escape(item.store)}</b>" if item.store else "Added by hand"
            self.location.setText(f"<b>In your library</b> · {where}<br>No files on your drives; a "
                                  "scan that finds it on disk links them.")
            self.open_btn.setEnabled(bool(item.external_url))
            return
        if item.source == "steam":
            status = ("no longer in this account's library" if item.missing
                      else "in your Steam library")
            self.location.setText(f"<b>{drive_label}</b><br>{status}<br>If you also have it in a "
                                  "scanned folder, that copy under Games shows this play time too.")
            self.open_btn.setEnabled(bool(item.external_url))
            return
        if item.missing:
            status = "not found on its last scan"
        elif item.volume_uid.startswith(devices.PREFIX):
            status = "on an Android device (open and play it there)"
        elif mount:
            status = f"online at {mount}"
        else:
            status = "drive not connected"
        path = "/" + item.rel_path.replace("/", "/​")
        from .. import transfer

        with db.session() as conn:
            past = transfer.history(conn, item.id)[:4]
        moves = "".join(f"<br>{h['at'][:10]}: {h['detail']}" for h in past)
        self.location.setText(
            f"<b>{drive_label}</b> — {status}<br>{path}<br>"
            f"{item.file_count} files · {fmt_size(item.size_bytes)}"
            + (f"<br><i>Detected: {item.detected_how}</i>" if item.detected_how else "") + moves)
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
        self._episode_keys = [(e.season, e.number, e.on_disk) for e in eps]
        last = (item.last_episode_season or 1, item.last_episode) if item.last_episode else None
        for row, e in enumerate(eps):
            score = QTableWidgetItem(scores_fmt(e) if e.score is not None else "–")
            if e.score is not None:
                score.setForeground(theme.rating_color(e.score, e.score_max))
            title = e.title or ""
            if e.note:
                title += f"  ({e.note})"
            cells = [QTableWidgetItem(f"{e.number}"), QTableWidgetItem(title), score,
                     QTableWidgetItem(e.aired or ""), QTableWidgetItem("✓" if e.on_disk else "")]
            if last and ((e.season or 1), e.number) == last:
                cells[4].setText("✓  ▶" if e.on_disk else "▶")
                cells[4].setToolTip("The last one you played")
                for c in cells[:2]:
                    f = c.font()
                    f.setBold(True)
                    c.setFont(f)
            for col, cell in enumerate(cells):
                if col != 1:
                    cell.setTextAlignment(Qt.AlignCenter)
                self.episodes.setItem(row, col, cell)
        row_h = self.episodes.verticalHeader().defaultSectionSize()
        self.episodes.setFixedHeight(self.episodes.horizontalHeader().height()
                                     + row_h * min(max(len(eps), 1), 14) + 4)
        if not eps:
            self.episodes_info.setText("No episode list yet. TVmaze (TV) and MyAnimeList / Kitsu "
                                       "(anime) provide one without keys; AniDB / TMDB with a key. "
                                       "Use Refetch to try again.")
            return
        scored = [e for e in eps if e.score is not None]
        source = eps[0].source
        bits = [f"{len(eps)} episodes found in your files" if source == enrich.FILES_SOURCE
                else f"{len(eps)} episodes from {source}",
                f"{sum(e.on_disk for e in eps)} on disk"]
        if scored:
            avg = sum(e.score / (e.score_max or 10) for e in scored) / len(scored) * 10
            top = max(scored, key=lambda e: e.score / (e.score_max or 10))
            bits.insert(1, f"average {avg:.1f}/10")
            bits.insert(2, f"best: #{top.number} {top.title or ''} ({scores_fmt(top)})")
        self.episodes_info.setText("  ·  ".join(bits))

    def _open_episode(self, row: int, _col: int) -> None:
        keys = getattr(self, "_episode_keys", [])
        if row < len(keys) and keys[row][2]:  # on disk: play it
            self.play_episode.emit(self.item.id, keys[row][0], keys[row][1])
            return
        url = self._episode_urls[row] if row < len(self._episode_urls) else None
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _show_launch(self, item: db.Item, mount: str | None) -> None:
        """Play / Run setup, what it starts, and when you last played."""
        from .. import launcher
        from ..scan import install

        game = item.category == "game" and item.source != db.WANTED
        video = (categories.get(item.category).key in ("anime", "tv", "movie")
                 and item.source != db.WANTED and item.video_count > 0)
        self.play_btn.setVisible(game or video)
        self.launch_btn.setVisible(game and item.source != db.STEAM)
        self.play_info.setVisible(game or video)
        if video:
            self._show_video_play(item, mount)
            return
        if not game:
            return
        setup = item.install_state == install.INSTALLER
        self.play_btn.setText("▶  Run setup" if setup else "▶  Play")
        target = launcher.target_path(item, mount) if item.launch_path else None
        steam = launcher.via_steam(item)
        can = steam or (bool(item.launch_path) and (mount is not None or bool(target)))
        self.play_btn.setEnabled(can and not item.missing)
        # Played somewhere else (Xbox app, a console): nothing to start from here.
        self.play_btn.setVisible(can or not (item.source == "manual" and item.store))
        bits = []
        if item.install_state:
            bits.append(install.STATES.get(item.install_state, item.install_state))
        if item.launch_path and item.source != db.STEAM:
            bits.append(f"starts {item.launch_path.rsplit('/', 1)[-1]}")
        elif steam:
            bits.append("starts in Steam")
        elif item.source == "manual" and item.store:
            bits.append(f"played on {item.store}")
        if item.last_launched:
            n = item.launch_count
            bits.append(f"last played {item.last_launched[:10]}" + (f" · {n} times" if n > 1 else ""))
        if not item.launch_path and not steam and not (item.source == "manual" and item.store):
            bits.append("nothing to start found: Launch options › Choose file")
        elif item.volume_uid.startswith(devices.PREFIX):
            bits.append("on an Android device: play it there")
        elif not mount and not steam and item.source != "manual":
            bits.append("drive not connected")
        self.play_info.setText("  ·  ".join(bits))

    def _show_video_play(self, item: db.Item, mount: str | None) -> None:
        from .. import launcher

        with db.session() as conn:
            nxt = launcher.next_episode(conn, item)
        movie = item.kind == "movie" or item.category == "movie"
        if movie:
            self.play_btn.setText("▶  Play")
        elif nxt is None:
            self.play_btn.setText("▶  Start over")
        else:
            self.play_btn.setText(f"▶  Play episode {nxt.episode}" if nxt.episode is not None
                                  else "▶  Play")
        self.play_btn.setEnabled(bool(mount) and not item.missing)
        bits = []
        if item.last_episode is not None and not movie:
            bits.append(f"last played episode {item.last_episode}"
                        + (f" on {item.last_watched[:10]}" if item.last_watched else ""))
        elif item.last_watched:
            bits.append(f"last played {item.last_watched[:10]}")
        if nxt is None and not movie:
            bits.append("every episode played")
        if not mount:
            bits.append("drive not connected")
        self.play_info.setText("  ·  ".join(bits) or "Double-click an episode below to play it")

    def _load_upcoming(self, item: db.Item) -> None:
        self.upcoming_title.hide()
        self.upcoming.hide()
        if item.category not in ("tv", "anime", "game") or item.kind == "movie":
            return
        job = _UpcomingJob(item)
        job.signals.done.connect(self._show_upcoming)
        self._upcoming_jobs = [*self._upcoming_jobs[-4:], job]  # keep alive until it reports
        QThreadPool.globalInstance().start(job)

    def _show_upcoming(self, item_id: int, found: list) -> None:
        if not self.item or self.item.id != item_id or not found:
            return  # another item is shown now, or nothing to say
        lines = []
        for u in found:
            text = u.text if not u.url else f"<a style='color:#5b8cff' href='{u.url}'>{u.text}</a>"
            lines.append(f"<b>{text}</b>" if u.highlight else text)
        self.upcoming.setText("<br>".join(lines))
        self.upcoming_title.show()
        self.upcoming.show()

    def _launch_options(self) -> None:
        dlg = LaunchOptionsDialog(self.item, getattr(self, "mount", None), self)
        if dlg.exec() == QDialog.Accepted:
            self.changed.emit(self.item.id)

    def _set_done(self, done: bool, when: str | None) -> None:
        if self.item and self.item.source == db.WANTED:
            self.done_btn.setChecked(False)
            self.done_btn.setText("Got it (move to library)")
            self.done_when.setText("")
            return
        self.done_btn.setChecked(done)
        verb = categories.get(self.item.category if self.item else None).done
        self.done_btn.setText(f"✓  {verb}" if done else f"Mark as {verb.lower()}")
        self.done_when.setText(f"on {when[:10]}" if done and when else "")

    # ------------------------------------------------------------------ actions

    def _toggle_done(self, checked: bool) -> None:
        with db.session() as conn:
            if self.item.source == db.WANTED:
                library.acquire(conn, [self.item.id])
            else:
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
                notes=self.e_notes.toPlainText(),
                store=self.e_store.currentText() if getattr(self, "_by_hand", False) else None)
        self.changed.emit(self.item.id)
        if lookup_changed:
            self.refetch.emit(self.item.id)

    def _delete(self) -> None:
        wanted = self.item.source == db.WANTED
        question = (f"Remove “{self.item.title}” from your Want to acquire list?" if wanted else
                    f"Remove “{self.item.title}” from the library?\nFiles on disk are not touched; "
                    "a rescan of its location will add it back.")
        if QMessageBox.question(self, "Remove", question) != QMessageBox.Yes:
            return
        with db.session() as conn:
            library.remove_items(conn, [self.item.id])
        self.deleted.emit(self.item.id)


class LaunchOptionsDialog(QDialog):
    """Which file Play starts (detected, or one you pick) and extra arguments."""

    def __init__(self, item: db.Item, mount: str | None, parent=None):
        from .. import launcher

        super().__init__(parent)
        self.setWindowTitle(f"Launch options · {item.title}")
        self.setMinimumWidth(560)
        self.item, self.mount = item, mount
        form = QFormLayout(self)
        self.path = QLineEdit(launcher.target_path(item, mount) or item.launch_path or "")
        browse = QPushButton("Choose file…")
        browse.clicked.connect(self._browse)
        reset = QPushButton("Use detected")
        reset.setToolTip("Go back to the file the scan picked")
        reset.clicked.connect(self._reset)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        row.addWidget(reset)
        self.args = QLineEdit(item.launch_args or "")
        self.args.setPlaceholderText("e.g. -windowed -skipintro")
        form.addRow("Start", row)
        form.addRow("Extra arguments", self.args)
        hint = QLabel("PC games start this file in its own folder. Console games pass it to the "
                      "emulator from Settings › Emulators.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._reset_to_detected = False

    def _browse(self) -> None:
        start = self.path.text() or (self.mount or "")
        path, _ = QFileDialog.getOpenFileName(self, "What should Play start?", start,
                                              "Games (*.exe *.bat *.lnk *.iso *.chd *.cue *.zip "
                                              "*.nsp *.xci *.rvz *.wbfs *.cso *.pbp *.bin);;All files (*)")
        if path:
            self.path.setText(os.path.normpath(path))
            self._reset_to_detected = False

    def _reset(self) -> None:
        self._reset_to_detected = True
        with db.session() as conn:
            r = conn.execute("SELECT launch_path FROM items WHERE id=?", (self.item.id,)).fetchone()
        from .. import launcher

        detected = r["launch_path"] if r else None
        self.path.setText(launcher.target_path(
            replace(self.item, launch_path=detected), self.mount) or detected or "")

    def _save(self) -> None:
        from .. import launcher

        text = self.path.text().strip() or None
        current = launcher.target_path(self.item, self.mount)
        with db.session() as conn:
            if self._reset_to_detected or not text:
                launcher.set_launch(conn, self.item.id, None, self.args.text())
            elif current and os.path.normcase(text) == os.path.normcase(current):
                # Unchanged file: only the arguments change (your earlier pick stays).
                conn.execute("UPDATE items SET launch_args=? WHERE id=?",
                             (self.args.text().strip() or None, self.item.id))
            else:
                launcher.set_launch(conn, self.item.id, text, self.args.text())
        self.accept()
