"""Settings: logging, data folder, export / import, and each service's keys."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ... import archive, categories, db, log, paths, providers
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
            db.set_setting(conn, "metadata_workers", str(self.workers.value()))
            db.set_setting(conn, "offline_style", self.offline_style.currentData())
        log.set_level(self.level.currentData())
        self.accept()

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
