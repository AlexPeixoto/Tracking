"""Library locations: add a drive / folder, manage them, rescan."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ... import categories, db, library, platforms, scan, volumes

# What a location can be set to hold: auto-detect or any section except Unknown.
CATEGORY_LABELS = {"auto": "Auto-detect",
                   **{c.key: c.label for c in categories.CATEGORIES if c.key != "unknown"}}


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
        for p in platforms.NAMES:
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
            mounts = {r["volume_uid"]: scan.mount_for(conn, r["volume_uid"], known) for r in rows}
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
            rid = scan.add_root(conn, path, category, platform)
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
            library.remove_locations(conn, ids, keep_items=box.clickedButton() is keep)
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
                mount = scan.mount_for(conn, r["volume_uid"], known)
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
