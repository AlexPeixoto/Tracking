"""Locations (library roots) and Settings (API keys) dialogs."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import backup, classify, db, log, paths, scanner, volumes

PLATFORMS = sorted(set(classify.PLATFORM_KEYS.values()))
CATEGORY_LABELS = {"auto": "Auto-detect", "anime": "Anime", "movie": "Movies", "game": "Games"}
LOG_LEVEL_HELP = {
    "DEBUG": "every scanner decision and service match (use when something is misdetected)",
    "INFO": "scans, re-links and fetch summaries",
    "WARNING": "only problems",
    "ERROR": "only failures",
}


class AddLocationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add location")
        self.setMinimumWidth(520)
        form = QFormLayout(self)

        # Whole drives, including ones without a drive letter.
        self.drive = QComboBox()
        self.drive.addItem("Pick a whole drive… (or browse for a folder below)", None)
        for v in volumes.list_volumes():
            where = "no drive letter" if v.mount.startswith("\\\\?\\") else v.mount
            self.drive.addItem(f"{v.label or '(no label)'} — {where}", v.mount)
        self.drive.currentIndexChanged.connect(
            lambda: self.drive.currentData() and self.path.setText(self.drive.currentData()))
        form.addRow("Drive", self.drive)

        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("Folder or drive, e.g. E:\\  or  D:\\Media\\Anime")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        form.addRow("Path", row)

        self.category = QComboBox()
        for k, v in CATEGORY_LABELS.items():
            self.category.addItem(v, k)
        self.category.currentIndexChanged.connect(self._toggle_platform)
        form.addRow("Contains", self.category)

        self.platform = QComboBox()
        self.platform.addItem("Detect from folders / files", None)
        for p in PLATFORMS:
            self.platform.addItem(p, p)
        form.addRow("Platform", self.platform)

        self.id_label = QLabel("")
        self.id_label.setObjectName("Muted")
        self.id_label.setWordWrap(True)
        form.addRow("", self.id_label)
        self.path.textChanged.connect(self._show_id)

        hint = QLabel(
            "Auto-detect understands folders named Anime / Movies / Games and platform "
            "folders such as PS2, Saturn or “Sony - PlayStation 3”. Pick a fixed category "
            "if this folder holds only one kind of thing."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Add and scan")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._toggle_platform()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder or drive", self.path.text())
        if d:
            self.path.setText(d)

    def _toggle_platform(self):
        self.platform.setEnabled(self.category.currentData() in ("auto", "game"))

    def _show_id(self, text: str):
        try:
            loc = volumes.identify(text) if text and os.path.isdir(text) else None
        except OSError:
            loc = None
        if loc:
            v = loc.volume
            self.id_label.setText(
                f"Stored as volume {v.uid} (serial {v.serial or '-'}, label “{v.label or '-'}”) "
                f"/ {loc.rel_path or '(root)'} — survives drive-letter changes."
            )
        else:
            self.id_label.setText("")

    def _accept(self):
        if not os.path.isdir(self.path.text()):
            QMessageBox.warning(self, "Add location", "That folder doesn't exist.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str | None]:
        return self.path.text(), self.category.currentData(), self.platform.currentData()


class LocationsDialog(QDialog):
    """List of library roots with their drive status."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library locations")
        self.resize(820, 380)
        self.added_ids: list[int] = []    # scan right away
        self.rescan_ids: list[int] = []   # open the rescan dialog for these
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Drive", "Folder", "Contains", "Status", "Volume id"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        add = QPushButton("Add…")
        add.setObjectName("Primary")
        add.clicked.connect(self._add)
        rescan = QPushButton("Rescan selected")
        rescan.clicked.connect(self._rescan)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (add, rescan, remove):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._load()

    def _load(self):
        known = volumes.list_volumes()
        with db.session() as conn:
            rows = conn.execute(
                "SELECT r.*, v.label, v.serial, "
                "(SELECT COUNT(*) FROM items i WHERE i.root_id=r.id) n "
                "FROM roots r JOIN volumes v ON v.uid=r.volume_uid ORDER BY v.label, r.rel_path"
            ).fetchall()
            mounts = {r["volume_uid"]: scanner.mount_for(conn, r["volume_uid"], known) for r in rows}
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            mount = mounts[r["volume_uid"]]
            cat = CATEGORY_LABELS.get(r["category"], r["category"])
            if r["platform"]:
                cat += f" / {r['platform']}"
            cells = [
                r["label"] or "(no label)",
                "/" + r["rel_path"],
                cat,
                f"online at {mount}  ·  {r['n']} items" if mount else f"offline  ·  {r['n']} items",
                r["volume_uid"],
            ]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, r["id"])
                self.table.setItem(i, c, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

    def _selected_ids(self) -> list[int]:
        return sorted({self.table.item(i.row(), 0).data(Qt.UserRole)
                       for i in self.table.selectedIndexes()})

    def _add(self):
        dlg = AddLocationDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        path, category, platform = dlg.values()
        with db.session() as conn:
            rid = scanner.add_root(conn, path, category, platform)
        self.added_ids.append(rid)
        self._load()

    def _rescan(self):
        self.rescan_ids.extend(self._selected_ids())
        self.accept()

    def _remove(self):
        ids = self._selected_ids()
        if not ids:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Remove location")
        box.setText("Stop scanning the selected locations?")
        box.setInformativeText(
            "Items already found there can stay in your library (with their completed state "
            "and notes), or be removed with the location. Files on disk are never touched.")
        keep = box.addButton("Keep items", QMessageBox.AcceptRole)
        drop = box.addButton("Remove items too", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (keep, drop):
            return
        with db.session() as conn:
            if box.clickedButton() is drop:
                conn.executemany("DELETE FROM items WHERE root_id=?", [(i,) for i in ids])
            conn.executemany("DELETE FROM roots WHERE id=?", [(i,) for i in ids])
        self._load()


class RescanDialog(QDialog):
    """Pick locations to rescan, and what to do with items that disappeared."""

    def __init__(self, preselect: list[int] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rescan")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Locations to rescan:"))
        self.list = QListWidget()
        known = volumes.list_volumes()
        with db.session() as conn:
            rows = conn.execute(
                "SELECT r.id, r.rel_path, r.volume_uid, v.label FROM roots r "
                "JOIN volumes v ON v.uid=r.volume_uid ORDER BY v.label, r.rel_path").fetchall()
            for r in rows:
                mount = scanner.mount_for(conn, r["volume_uid"], known)
                text = f"{r['label'] or '(no label)'}  /{r['rel_path']}"
                text += f"   — {mount}" if mount else "   — offline"
                it = QListWidgetItem(text)
                it.setData(Qt.UserRole, r["id"])
                if mount:
                    on = preselect is None or r["id"] in preselect
                    it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                else:
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                    it.setCheckState(Qt.Unchecked)
                self.list.addItem(it)
        lay.addWidget(self.list)

        self.missing_completed = QCheckBox("Mark items that are no longer found as completed")
        self.missing_completed.setToolTip(
            "If you delete things after watching / finishing them, a rescan can tick them off.")
        self.enrich = QCheckBox("Fetch metadata for new items")
        self.enrich.setChecked(True)
        lay.addWidget(self.missing_completed)
        lay.addWidget(self.enrich)
        note = QLabel("Missing items are never deleted: they stay in the library, flagged as missing.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        b.button(QDialogButtonBox.Ok).setText("Rescan")
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        lay.addWidget(b)

    def root_ids(self) -> list[int]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class SettingsDialog(QDialog):
    """API keys, one tab per category. Services listed under "No key needed" always run."""

    TABS = [
        ("Movies", "IMDb (posters + link, via IMDb search)", [
            ("omdb_key", "OMDb API key",
             "IMDb, Rotten Tomatoes and Metacritic scores. Free: omdbapi.com/apikey.aspx"),
            ("tmdb_key", "TMDB API key / token",
             "TMDB score, sharper posters, plot. Free: themoviedb.org/settings/api "
             "(either the API key or the read access token)"),
            ("trakt_client_id", "Trakt Client ID",
             "Trakt score (movies and anime). Free app: trakt.tv/oauth/applications/new, "
             "redirect URI urn:ietf:wg:oauth:2.0:oob"),
        ]),
        ("Anime", "MyAnimeList (Jikan), AniList, Kitsu", [
            ("anidb_client", "AniDB client name",
             "AniDB score (the headline rating when set). Register an HTTP API client at "
             "anidb.net/software/add"),
            ("anidb_clientver", "AniDB client version", "The version you registered, usually 1."),
            ("trakt_client_id", "Trakt Client ID", "Same Client ID as on the Movies tab."),
        ]),
        ("Games", "HowLongToBeat (times + covers), Steam reviews (PC games, includes Metacritic)", [
            ("rawg_key", "RAWG API key",
             "Metacritic score, RAWG user score, description. Free: rawg.io/apidocs"),
            ("igdb_client_id", "IGDB Client ID",
             "IGDB critic + user ratings, covers, summaries; good for retro consoles. "
             "Free Twitch app: dev.twitch.tv/console/apps"),
            ("igdb_client_secret", "IGDB Client Secret", "From the same Twitch app."),
            ("opencritic_key", "OpenCritic RapidAPI key",
             "OpenCritic score and % recommended. Subscribe to “OpenCritic API” (free tier) "
             "on rapidapi.com"),
        ]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        self.edits: dict[str, list[QLineEdit]] = {}
        with db.session() as conn:
            general = QWidget()
            gform = QFormLayout(general)
            self.level = QComboBox()
            for lvl in log.LEVELS:
                self.level.addItem(f"{lvl} — {LOG_LEVEL_HELP[lvl]}", lvl)
            current = db.get_setting(conn, "log_level", "INFO")
            self.level.setCurrentIndex(log.LEVELS.index(current) if current in log.LEVELS else 1)
            gform.addRow("Log level", self.level)
            where = QLabel(f"Log file: {log.log_file()}")
            where.setObjectName("Muted")
            where.setWordWrap(True)
            where.setTextInteractionFlags(Qt.TextSelectableByMouse)
            gform.addRow("", where)

            data = QLabel(f"{paths.data_dir()}")
            data.setWordWrap(True)
            data.setTextInteractionFlags(Qt.TextSelectableByMouse)
            gform.addRow("Library data", data)
            row = QHBoxLayout()
            open_btn = QPushButton("Open data folder")
            open_btn.clicked.connect(lambda: _open_in_explorer(paths.data_dir()))
            backup_btn = QPushButton("Back up…")
            backup_btn.clicked.connect(self._backup)
            row.addWidget(open_btn)
            row.addWidget(backup_btn)
            row.addStretch(1)
            gform.addRow("", row)
            portable = ("Portable mode is on: data lives next to Tracking.exe."
                        if paths.data_dir() == paths.portable_dir() else
                        f"Portable mode: create a folder named “data” next to Tracking.exe "
                        f"({paths.portable_dir()}) and unzip a backup into it; the app "
                        f"then keeps everything there.")
            note = QLabel("A backup is one .zip with the library (items, completion, notes, "
                          "settings and API keys) and all covers. To restore or move to another "
                          "PC, close Tracking and unzip it into the data folder. " + portable)
            note.setObjectName("Muted")
            note.setWordWrap(True)
            gform.addRow("", note)
            tabs.addTab(general, "General")
            for tab_name, keyless, fields in self.TABS:
                page = QWidget()
                form = QFormLayout(page)
                free = QLabel(f"<b>No key needed:</b> {keyless}")
                free.setWordWrap(True)
                form.addRow(free)
                for key, label, help_text in fields:
                    e = QLineEdit(db.get_setting(conn, key))
                    if key.endswith(("_key", "_secret")):
                        e.setEchoMode(QLineEdit.PasswordEchoOnEdit)
                    # The same setting can appear on two tabs; keep them in sync.
                    for other in self.edits.get(key, []):
                        e.textEdited.connect(other.setText)
                        other.textEdited.connect(e.setText)
                    self.edits.setdefault(key, []).append(e)
                    form.addRow(label, e)
                    h = QLabel(help_text)
                    h.setWordWrap(True)
                    h.setObjectName("Muted")
                    form.addRow("", h)
                tabs.addTab(page, tab_name)
        note = QLabel("After adding keys, use “Update metadata” or Refetch on an item to pull the new scores.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _save(self):
        with db.session() as conn:
            for key, edits in self.edits.items():
                db.set_setting(conn, key, edits[0].text().strip())
            db.set_setting(conn, "log_level", self.level.currentData())
        log.set_level(self.level.currentData())
        self.accept()

    def _backup(self):
        default = str(Path.home() / f"Tracking-backup-{date.today():%Y-%m-%d}.zip")
        target, _ = QFileDialog.getSaveFileName(self, "Back up library", default, "Zip files (*.zip)")
        if not target:
            return
        try:
            n = backup.backup(Path(target))
        except Exception as e:
            QMessageBox.warning(self, "Back up", f"Backup failed: {e}")
            return
        size = Path(target).stat().st_size / 1024 / 1024
        QMessageBox.information(self, "Back up", f"Saved {n} files ({size:.1f} MB) to\n{target}")


def _open_in_explorer(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)


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
        if os.name == "nt":
            os.startfile(log.log_dir())

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
