"""Settings: logging, data folder, export / import, emulators, and each service's keys."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ... import archive, categories, db, launcher, log, paths, platforms, providers
from .. import grid, workers

LOG_LEVEL_HELP = {
    "DEBUG": "every scanner decision and service match (use when something is misdetected)",
    "INFO": "scans, re-links and fetch summaries",
    "WARNING": "only problems",
    "ERROR": "only failures",
}


class SettingsDialog(QDialog):
    """API keys, one tab per category. Services listed under "No key needed" always run."""


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

            self.offline_style = QComboBox()
            for key, label in grid.OFFLINE_STYLES:
                self.offline_style.addItem(label, key)
            current = db.get_setting(conn, "offline_style", grid.OFFLINE_TEXT)
            self.offline_style.setCurrentIndex(max(0, self.offline_style.findData(current)))
            gform.addRow("Disconnected drives", self.offline_style)

            self.workers = QSpinBox()
            self.workers.setRange(1, workers.MAX_WORKERS)
            self.workers.setValue(workers.metadata_workers(conn))
            self.workers.setSuffix(" at a time")
            gform.addRow("Metadata downloads", self.workers)
            hint = QLabel("How many items to fetch ratings and covers for in parallel. Each site's "
                          "own speed limit is still respected, so more doesn't hit any site harder; "
                          "1 = one by one.")
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            gform.addRow("", hint)
            player_row = QHBoxLayout()
            self.player = QLineEdit(db.get_setting(conn, launcher.PLAYER_KEY))
            self.player.setPlaceholderText("System default (or mpv / VLC / MPC-HC…)")
            pick_player = QPushButton("Browse…")
            pick_player.clicked.connect(lambda: self._pick_emulator(self.player, "videos"))
            self.player_args = QLineEdit(db.get_setting(conn, launcher.PLAYER_ARGS_KEY))
            self.player_args.setPlaceholderText('"{file}"')
            self.player_args.setMaximumWidth(160)
            player_row.addWidget(self.player, 1)
            player_row.addWidget(pick_player)
            player_row.addWidget(self.player_args)
            gform.addRow("Video player", player_row)
            backup_row = QHBoxLayout()
            self.auto_backup = QComboBox()
            for key, label in (("off", "Off"), ("daily", "Daily"), ("weekly", "Weekly")):
                self.auto_backup.addItem(label, key)
            self.auto_backup.setCurrentIndex(max(0, self.auto_backup.findData(
                db.get_setting(conn, archive.AUTO, archive.AUTO_DEFAULT) or archive.AUTO_DEFAULT)))
            self.backup_keep = QSpinBox()
            self.backup_keep.setRange(1, 50)
            self.backup_keep.setPrefix("keep ")
            self.backup_keep.setValue(int(db.get_setting(conn, archive.AUTO_KEEP, "5") or 5))
            self.backup_images = QCheckBox("with covers")
            self.backup_images.setToolTip("Bigger, but nothing to fetch again after a restore")
            self.backup_images.setChecked(db.get_setting(conn, archive.AUTO_IMAGES) == "1")
            backup_now = QPushButton("Back up now")
            backup_now.clicked.connect(self._backup_now)
            open_backups = QPushButton("Open folder")
            open_backups.clicked.connect(lambda: open_in_explorer(archive.backups_dir()))
            for w in (self.auto_backup, self.backup_keep, self.backup_images, backup_now, open_backups):
                backup_row.addWidget(w)
            backup_row.addStretch(1)
            gform.addRow("Automatic backups", backup_row)
            last = db.get_setting(conn, archive.AUTO_LAST)
            self.backup_note = QLabel(f"Last: {last.replace('T', ' ')[:16]}" if last else "None yet")
            self.backup_note.setObjectName("Muted")
            gform.addRow("", self.backup_note)
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
            open_btn.clicked.connect(lambda: open_in_explorer(paths.data_dir()))
            export_btn = QPushButton("Export…")
            export_btn.clicked.connect(self._export)
            import_btn = QPushButton("Import…")
            import_btn.clicked.connect(self._import)
            for b in (open_btn, export_btn, import_btn):
                row.addWidget(b)
            row.addStretch(1)
            gform.addRow("", row)
            portable = ("Portable mode is on: data lives next to Tracking.exe."
                        if paths.data_dir() == paths.portable_dir() else
                        f"Portable mode: create a folder named “data” next to Tracking.exe "
                        f"({paths.portable_dir()}); the app then keeps everything there.")
            note = QLabel("Export saves the library (items, completion, notes, scores, settings "
                          "and API keys) to one .zip, with or without the cover images. Import "
                          "either replaces your library with an export or adds its items to "
                          "yours. " + portable)
            note.setObjectName("Muted")
            note.setWordWrap(True)
            gform.addRow("", note)
            tabs.addTab(general, "General")
            tabs.addTab(self._emulators_tab(conn), "Emulators")
            for cat in categories.CATEGORIES:
                services = providers.for_category(cat.key)
                if not services:
                    continue
                page = QWidget()
                form = QFormLayout(page)
                keyless = ", ".join(p.label for p in services if not p.required)
                free = QLabel(f"<b>Works without a key:</b> {keyless or 'nothing yet'}")
                free.setWordWrap(True)
                form.addRow(free)
                for setting in providers.settings_for(cat.key):
                    key, label, help_text = setting.key, setting.label, setting.help
                    e = QLineEdit(db.get_setting(conn, key))
                    if setting.secret:
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
                tabs.addTab(page, cat.label)
        note = QLabel("After adding keys, use “Update metadata” or Refetch on an item to pull "
                      "the new scores.")
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
            db.set_setting(conn, "metadata_workers", str(self.workers.value()))
            db.set_setting(conn, "offline_style", self.offline_style.currentData())
            db.set_setting(conn, launcher.PLAYER_KEY, self.player.text().strip())
            db.set_setting(conn, archive.AUTO, self.auto_backup.currentData())
            db.set_setting(conn, archive.AUTO_KEEP, str(self.backup_keep.value()))
            db.set_setting(conn, archive.AUTO_IMAGES, "1" if self.backup_images.isChecked() else "0")
            db.set_setting(conn, launcher.PLAYER_ARGS_KEY, self.player_args.text().strip())
            for plat, (path, args, unzip) in self.emulators.items():
                db.set_setting(conn, launcher.emulator_key(plat), path.text().strip())
                db.set_setting(conn, launcher.emulator_args_key(plat), args.text().strip())
                db.set_setting(conn, launcher.emulator_unzip_key(plat), "1" if unzip.isChecked() else "0")
        log.set_level(self.level.currentData())
        self.accept()

    def _emulators_tab(self, conn) -> QWidget:
        """One row per console: emulator, its arguments ({file} = the game), unzip first."""
        in_library = {r["p"] for r in conn.execute(
            "SELECT DISTINCT COALESCE(user_platform, platform) p FROM items WHERE category='game'")}
        names = [p.name for p in platforms.PLATFORMS if p.name != "PC"
                 and (p.name in in_library or p.name in launcher.SUGGESTED)]
        page = QWidget()
        outer = QVBoxLayout(page)
        intro = QLabel("Pick the emulator for each console, and Play starts games with it. "
                       "<b>{file}</b> in the arguments is the game's ROM or disc. Zipped images "
                       "are unpacked to a cache first when “Unzip” is ticked.")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        body = QWidget()
        grid_ = QGridLayout(body)
        grid_.setColumnStretch(1, 3)
        grid_.setColumnStretch(3, 2)
        for col, head in enumerate(("Platform", "Emulator (.exe)", "", "Arguments", "Unzip")):
            h = QLabel(f"<b>{head}</b>")
            grid_.addWidget(h, 0, col)
        self.emulators = {}
        for row, plat in enumerate(names, 1):
            label = QLabel(plat + ("  ·" if plat in in_library else ""))
            label.setToolTip("In your library" if plat in in_library else "")
            path = QLineEdit(db.get_setting(conn, launcher.emulator_key(plat)))
            path.setPlaceholderText(launcher.SUGGESTED.get(plat, ""))
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _=False, e=path, p=plat: self._pick_emulator(e, p))
            args = QLineEdit(db.get_setting(conn, launcher.emulator_args_key(plat)))
            args.setPlaceholderText(launcher.DEFAULT_ARGS.get(plat, '"{file}"'))
            unzip = QCheckBox()
            unzip.setChecked(db.get_setting(conn, launcher.emulator_unzip_key(plat), "1") == "1")
            for col, w in enumerate((label, path, browse, args, unzip)):
                grid_.addWidget(w, row, col)
            self.emulators[plat] = (path, args, unzip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        note = QLabel("· = you have games for it. Empty arguments use the usual ones for that "
                      "emulator (shown greyed).")
        note.setObjectName("Muted")
        outer.addWidget(note)
        return page

    def _backup_now(self) -> None:
        try:
            path = archive.auto_backup(force=True)
        except Exception as e:
            QMessageBox.warning(self, "Backup", f"Backup failed: {e}")
            return
        self.backup_note.setText(f"Saved {path.name}")

    def _pick_emulator(self, edit: QLineEdit, platform: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Emulator for {platform}", edit.text(),
                                              "Programs (*.exe);;All files (*)")
        if path:
            edit.setText(os.path.normpath(path))

    def _export(self):
        box = QMessageBox(self)
        box.setWindowTitle("Export library")
        box.setText("What should the export include?")
        box.setInformativeText("The database alone is small; covers can be fetched again "
                               "with “Update metadata” after importing it.")
        creds = QCheckBox("Include API keys and other service credentials")
        creds.setToolTip("Leave this off for an export you might share with someone.")
        box.setCheckBox(creds)
        with_images = box.addButton("Database + images", QMessageBox.AcceptRole)
        db_only = box.addButton("Database only", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (with_images, db_only):
            return
        images = box.clickedButton() is with_images
        name = f"Tracking-{'library' if images else 'database'}-{date.today():%Y-%m-%d}.zip"
        target, _ = QFileDialog.getSaveFileName(self, "Export library", str(Path.home() / name),
                                                "Zip files (*.zip)")
        if not target:
            return
        try:
            n = archive.export_zip(Path(target), images=images, credentials=creds.isChecked())
        except Exception as e:
            QMessageBox.warning(self, "Export", f"Export failed: {e}")
            return
        size = Path(target).stat().st_size / 1024 / 1024
        QMessageBox.information(self, "Export", f"Saved {n} files ({size:.1f} MB) to\n{target}")

    def _import(self):
        main = self.parent()
        if getattr(main, "job", None) or getattr(main, "queue", None):
            QMessageBox.information(self, "Import", "Wait for the running scan / metadata job to "
                                    "finish (or cancel it) before importing.")
            return
        source, _ = QFileDialog.getOpenFileName(self, "Import library", str(Path.home()),
                                                "Zip files (*.zip)")
        if not source:
            return
        try:
            manifest = archive.read_manifest(Path(source))
        except archive.ArchiveError as e:
            QMessageBox.warning(self, "Import", str(e))
            return
        box = QMessageBox(self)
        box.setWindowTitle("Import library")
        box.setText("Replace your library, or add this export's items to it?")
        when = (manifest.get("exported_at") or "")[:10]
        box.setInformativeText(
            f"Export{' from ' + when if when else ''}, "
            f"{'with' if manifest.get('images') else 'without'} cover images.\n\n"
            "Replace: your current items, settings and covers are swapped for the export's.\n"
            "Add: new items are added; for items you already have, your edits and notes stay, "
            "and it counts as completed if either copy is.")
        add = box.addButton("Add to my library", QMessageBox.AcceptRole)
        replace = box.addButton("Replace my library", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (add, replace):
            return
        mode = "replace" if box.clickedButton() is replace else "merge"
        if mode == "replace" and QMessageBox.question(
                self, "Replace library",
                "This permanently replaces your current library. Export it first if you might "
                "want it back.\n\nReplace it now?") != QMessageBox.Yes:
            return
        try:
            result = archive.import_zip(Path(source), mode)
        except Exception as e:
            QMessageBox.warning(self, "Import", f"Import failed: {e}")
            return
        QMessageBox.information(self, "Import", f"Import done: {result}.")
        if hasattr(main, "after_import"):
            main.after_import(result)
        # The fields in this dialog now show the old settings; don't save them over the import.
        self.reject()


def open_in_explorer(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)
