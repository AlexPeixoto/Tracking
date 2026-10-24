"""Library locations: add a drive / folder, manage them, rescan."""

from __future__ import annotations

import os

import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ... import categories, db, devices, library, platforms, scan, volumes
from ...scan import fs as scan_fs
from ...sources import steam

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
        with db.session() as conn:
            names = {r["uid"]: r["name"] for r in conn.execute(
                "SELECT uid, name FROM volumes WHERE name IS NOT NULL")}
        for v in volumes.list_volumes():
            size = volumes.size_of(v)
            if volumes.is_system_partition(v, size):
                continue  # Windows' boot / recovery partitions
            where = "no drive letter" if v.mount.startswith("\\\\?\\") else v.mount
            gb = f", {size / 1024**3:,.0f} GB" if size else ""
            self.drive.addItem(f"{names.get(v.uid) or v.label or '(no label)'} — {where}{gb}", v.mount)
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


class AddDeviceDialog(QDialog):
    """A folder on an Android device (phone, handheld) plugged in by USB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Android device")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        intro = QLabel("Plug the device in by USB (and unlock it). Its games are read from the folder "
                       "you pick; they show up in the library but are played on the device.")
        intro.setWordWrap(True)
        form.addRow(intro)

        self.storages: list[devices.Storage] = []
        self.storage = QComboBox()
        self.storage.currentIndexChanged.connect(self._load_folders)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Look for devices again")
        refresh.clicked.connect(self._load_storages)
        row = QHBoxLayout()
        row.addWidget(self.storage, 1)
        row.addWidget(refresh)
        form.addRow("Device", row)

        self.folder = QComboBox()
        self.folder.setEditable(True)  # a deeper folder can be typed: "Emulation/roms"
        form.addRow("Folder", self.folder)

        self.category = QComboBox()
        for k, v in CATEGORY_LABELS.items():
            self.category.addItem(v, k)
        self.category.setCurrentIndex(max(0, self.category.findData("game")))
        form.addRow("Contains", self.category)
        self.platform = QComboBox()
        self.platform.addItem("Detect from folders / files", None)
        for p in platforms.NAMES:
            self.platform.addItem(p, p)
        form.addRow("Platform", self.platform)

        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        form.addRow(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("Add and scan")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._load_storages()

    def _load_storages(self) -> None:
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.storages = devices.list_storages()
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.storage.blockSignals(True)
        self.storage.clear()
        for st in self.storages:
            gb = f" ({st.size / 1024**3:,.0f} GB)" if st.size else ""
            self.storage.addItem(st.label + gb)
        self.storage.blockSignals(False)
        self.ok.setEnabled(bool(self.storages))
        if not devices.available():
            self.status.setText("Reading Android devices needs Windows.")
        elif not self.storages:
            self.status.setText("No device found. Plug it in, unlock it, and if it asks, allow "
                                "file access (“File transfer” / MTP). Then Refresh.")
        else:
            self.status.setText("")
        self._load_folders()

    def _load_folders(self) -> None:
        self.folder.clear()
        st = self.selected()
        if not st:
            return
        try:
            names = devices.list_folders(st)
        except Exception as e:  # unplugged meanwhile
            self.status.setText(f"Couldn't read {st.label}: {e}")
            names = []
        self.folder.addItem("(whole storage)", "")
        for n in names:
            self.folder.addItem(n, n)
        # Where emulator front-ends keep games, if there is one.
        guess = next((i for i in range(self.folder.count())
                      if (self.folder.itemData(i) or "").lower() in ("emulation", "roms", "games")), 0)
        self.folder.setCurrentIndex(guess)

    def selected(self) -> devices.Storage | None:
        i = self.storage.currentIndex()
        return self.storages[i] if 0 <= i < len(self.storages) else None

    def values(self) -> tuple[devices.Storage | None, str, str, str | None]:
        text = self.folder.currentText().strip()
        # A listed folder, "(whole storage)", or a path typed in ("Emulation/roms").
        folder = "" if text == "(whole storage)" else text.replace("\\", "/").strip("/")
        return self.selected(), folder, self.category.currentData(), self.platform.currentData()


class AddSteamDialog(QDialog):
    """A Steam account as a location: its games and play time land in Games / PC."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Steam account")
        self.setMinimumWidth(560)
        self.account: steam.Account | None = None
        form = QFormLayout(self)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Profile name, profile URL or SteamID64")
        form.addRow("Account", self.name)
        name_hint = QLabel("The name in your profile address (steamcommunity.com/id/<b>name</b>), "
                           "the whole address, or the 17-digit SteamID64. Your sign-in name "
                           "isn't public, so it may not work.")
        name_hint.setWordWrap(True)
        name_hint.setObjectName("Muted")
        form.addRow("", name_hint)

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.key.setPlaceholderText("Needed for your games and play time")
        with db.session() as conn:
            self.key.setText(db.get_setting(conn, steam.KEY_SETTING))
        form.addRow("Web API key", self.key)
        key_hint = QLabel(
            f"Steam only shares owned games and play time with a Web API key. It's free: sign "
            f"in at <a style='color:#5b8cff' href='{steam.KEY_URL}'>{steam.KEY_URL}</a>, "
            "enter any domain name (e.g. localhost) and copy the key here. It's saved in "
            "Settings and never included in exports.")
        key_hint.setWordWrap(True)
        key_hint.setOpenExternalLinks(True)
        key_hint.setObjectName("Muted")
        form.addRow("", key_hint)

        self.wishlist = QCheckBox("Also put my Steam wish list on “Want to acquire”")
        self.wishlist.setChecked(True)
        form.addRow("", self.wishlist)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Add and import")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self):
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.account = steam.resolve(self.name.text())
        except steam.SteamError as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Add Steam account", str(e))
            return
        except requests.RequestException as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Add Steam account", f"Couldn't reach Steam: {e}")
            return
        QGuiApplication.restoreOverrideCursor()
        key = self.key.text().strip()
        if not key:
            ok = QMessageBox.question(
                self, "No Web API key",
                f"Found {self.account.name}. Without a Web API key Steam won't share the "
                "games you own or your play time"
                + (", only your wish list." if self.wishlist.isChecked() else ".")
                + f"\n\nGet a free key at {steam.KEY_URL}. Add the account anyway?")
            if ok != QMessageBox.Yes:
                return
        elif self.account.private:
            QMessageBox.information(
                self, "Private profile",
                f"{self.account.name}'s profile isn't public. Steam lists its games only if "
                "the key belongs to this same account or its “Game details” are set to Public "
                "(Steam › Edit Profile › Privacy Settings).")
        with db.session() as conn:
            if key:
                db.set_setting(conn, steam.KEY_SETTING, key)
        self.accept()


class LocationsDialog(QDialog):
    """List of library roots with their drive status."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library locations")
        self.resize(820, 380)
        self.added_ids: list[int] = []    # scan right away
        self.rescan_ids: list[int] = []   # open the rescan dialog for these
        self.renamed = False
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
        add_device = QPushButton("Add Android device…")
        add_device.setToolTip("A phone or handheld (AYN Thor, Retroid...) plugged in by USB")
        add_device.clicked.connect(self._add_device)
        add_steam = QPushButton("Add Steam account…")
        add_steam.setToolTip("Import the games you own on Steam, with play time")
        add_steam.clicked.connect(self._add_steam)
        rescan = QPushButton("Rescan selected")
        rescan.clicked.connect(self._rescan)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        rename = QPushButton("Rename…")
        rename.setToolTip("Give the selected drive / device a name of your own (double-click works too)")
        rename.clicked.connect(self._rename)
        self.table.cellDoubleClicked.connect(lambda row, col: col == 0 and self._rename())
        for b in (add, add_device, add_steam, rename, rescan, remove):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._load()

    def _load(self):
        known = volumes.list_volumes()
        with db.session() as conn:
            rows = conn.execute(
                "SELECT r.*, COALESCE(v.name, v.label) AS label, v.label AS system_label, v.serial, "
                "(SELECT COUNT(*) FROM items i WHERE i.root_id=r.id) n "
                "FROM roots r JOIN volumes v ON v.uid=r.volume_uid ORDER BY label, r.rel_path"
            ).fetchall()
            mounts = {r["volume_uid"]: scan.mount_for(conn, r["volume_uid"], known) for r in rows}
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            mount = mounts[r["volume_uid"]]
            cat = CATEGORY_LABELS.get(r["category"], r["category"])
            if r["platform"]:
                cat += f" / {r['platform']}"
            if steam.is_account(r["volume_uid"]):
                status = f"Steam account  ·  {r['n']} games"
            elif r["volume_uid"].startswith(devices.PREFIX):
                status = f"Android device  ·  {r['n']} items"
            elif mount:
                status = f"online at {mount}  ·  {r['n']} items"
            else:
                status = f"offline  ·  {r['n']} items"
            cells = [
                r["label"] or "(no label)",
                "" if steam.is_account(r["volume_uid"]) else "/" + r["rel_path"],
                cat,
                status,
                r["volume_uid"],
            ]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, r["id"])
                it.setData(Qt.UserRole + 1, r["volume_uid"])
                if c == 0 and r["label"] != r["system_label"]:
                    it.setToolTip(f"Your name. The drive calls itself “{r['system_label'] or '(no label)'}”.")
                self.table.setItem(i, c, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

    def _rename(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) != 1:
            QMessageBox.information(self, "Rename", "Select one location to rename its drive.")
            return
        cell = self.table.item(rows.pop(), 0)
        uid = cell.data(Qt.UserRole + 1)
        with db.session() as conn:
            v = conn.execute("SELECT label, name FROM volumes WHERE uid=?", (uid,)).fetchone()
            shared = conn.execute("SELECT COUNT(*) FROM roots WHERE volume_uid=?", (uid,)).fetchone()[0]
        note = f" (all {shared} locations on it)" if shared > 1 else ""
        name, ok = QInputDialog.getText(
            self, "Rename", f"A name for this drive / device{note}. Leave it empty to use "
            f"its own label (“{v['label'] or 'none'}”).",
            text=v["name"] or v["label"] or "")
        if not ok:
            return
        with db.session() as conn:
            library.rename_volume(conn, uid, None if name.strip() == (v["label"] or "") else name)
        self.renamed = True
        self._load()

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

    def _add_device(self):
        dlg = AddDeviceDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        st, folder, category, platform = dlg.values()
        if not st:
            return
        with db.session() as conn:
            rid = scan.add_device_root(conn, st, folder, category, platform)
        self.added_ids.append(rid)
        self._load()

    def _add_steam(self):
        dlg = AddSteamDialog(self)
        if dlg.exec() != QDialog.Accepted or not dlg.account:
            return
        with db.session() as conn:
            rid = steam.add_account(conn, dlg.account, wishlist=dlg.wishlist.isChecked())
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
                "SELECT r.id, r.rel_path, r.volume_uid, COALESCE(v.name, v.label) AS label FROM roots r "
                "JOIN volumes v ON v.uid=r.volume_uid ORDER BY label, r.rel_path").fetchall()
            for r in rows:
                mount = scan.mount_for(conn, r["volume_uid"], known)
                snap = None if mount else scan_fs.info(conn, r["id"])
                if steam.is_account(r["volume_uid"]):
                    text = f"{r['label']}   — games and play time"
                else:
                    text = f"{r['label'] or '(no label)'}  /{r['rel_path']}"
                    if mount:
                        text += f"   — {mount}"
                    elif snap:
                        text += (f"   — offline: from its snapshot ({snap[1]:,} files, "
                                 f"{snap[0][:10]})")
                    else:
                        text += "   — offline (no snapshot yet: scan it once while connected)"
                it = QListWidgetItem(text)
                it.setData(Qt.UserRole, r["id"])
                if mount or snap:
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
        note = QLabel("Missing items are never deleted: they stay in the library, flagged as missing. "
                      "Unplugged drives are re-processed from their snapshot (what their last scan "
                      "saw), so new detection rules apply without plugging them in; new or deleted "
                      "files show up once you rescan with the drive connected.")
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
