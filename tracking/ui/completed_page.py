"""The Completed section: what you finished, one year at a time.

Covers in your order (drag to rearrange; the exported image follows it), with
add / edit / remove, year CSV import and export, and "Export year as image".
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from .. import categories, completions, db, platforms
from .year_image import ExportYearDialog

COVER = QSize(120, 168)
IdRole = Qt.UserRole + 1


def _placeholder(title: str) -> QPixmap:
    pix = QPixmap(COVER)
    pix.fill(QColor("#2a2a36"))
    p = QPainter(pix)
    f = QFont()
    f.setPixelSize(13)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#d8d8e0"))
    p.drawText(pix.rect().adjusted(8, 8, -8, -8), Qt.AlignCenter | Qt.TextWordWrap, title)
    p.end()
    return pix


def _cover(c: completions.Completion) -> QPixmap:
    path = c.poster_path()
    pix = QPixmap(path) if path else QPixmap()
    if pix.isNull():
        return _placeholder(c.title)
    scaled = pix.scaled(COVER, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x, y = (scaled.width() - COVER.width()) // 2, (scaled.height() - COVER.height()) // 2
    return scaled.copy(max(0, x), max(0, y), COVER.width(), COVER.height())


MONTHS = "JFMAMJJASOND"


class MonthChart(QWidget):
    """Twelve small bars: how many things you finished each month."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.counts = [0] * 12
        self.setFixedSize(12 * 18, 46)

    def set_counts(self, counts: list[int]) -> None:
        self.counts = counts
        self.setToolTip("\n".join(f"{date(2000, m + 1, 1):%B}: {n}" for m, n in enumerate(counts)))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top = max(self.counts) or 1
        f = QFont()
        f.setPixelSize(9)
        p.setFont(f)
        for m, n in enumerate(self.counts):
            x = m * 18 + 3
            h = round(30 * n / top)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#4f7cff") if n else QColor("#2a2d36"))
            p.drawRoundedRect(x, 32 - max(h, 2), 12, max(h, 2), 2, 2)
            p.setPen(QColor("#8a8f9c"))
            p.drawText(x, 34, 12, 12, Qt.AlignCenter, MONTHS[m])
        p.end()


def stats_text(s: completions.YearStats) -> str:
    """"14 finished · 9 games, 3 anime, 2 movies · 312 h" """
    if not s.count:
        return ""
    def low(s: str) -> str:  # "TV Shows" -> "TV shows", "Movie" -> "movie"
        return " ".join(w if w.isupper() else w.lower() for w in s.split())

    labels = {c.key: (low(c.singular), low(c.label)) for c in categories.CATEGORIES}
    parts = []
    for key, n in sorted(s.by_category.items(), key=lambda kv: -kv[1]):
        one, many = labels.get(key, (key, key))
        parts.append(f"{n} {one if n == 1 else many}")
    text = f"<b>{s.count}</b> finished · " + ", ".join(parts)
    if s.timed:
        text += f" · <b>{s.hours:,.0f} h</b>"
        if s.timed < s.count:
            text += f" <span style='color:#8a8f9c'>(time known for {s.timed})</span>"
    return text


class CompletionDialog(QDialog):
    """Add or edit one entry: name, date (or just the year), section, platform, notes."""

    def __init__(self, entry: completions.Completion | None = None, year: int | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit completed entry" if entry else "Add completed entry")
        self.entry = entry
        form = QFormLayout(self)
        self.title = QLineEdit(entry.title if entry else "")
        self.title.setPlaceholderText("Name of the game, movie, show…")
        self.when = QDateEdit()
        self.when.setDisplayFormat("dd/MM/yyyy")
        self.when.setCalendarPopup(True)
        self.year_only = QCheckBox("Only the year is known")
        self.year = QSpinBox()
        self.year.setRange(1970, 2100)
        day = entry.day if entry else None
        if entry and not day:
            self.year_only.setChecked(True)
            self.year.setValue(entry.year)
            self.when.setDate(QDate(entry.year, 12, 31))
        else:
            d = day or (date.today() if not year or year == date.today().year else date(year, 12, 31))
            self.when.setDate(QDate(d.year, d.month, d.day))
            self.year.setValue(d.year)
        date_row = QHBoxLayout()
        date_row.addWidget(self.when)
        date_row.addWidget(self.year)
        date_row.addWidget(self.year_only)
        self.year_only.toggled.connect(self._year_only)
        self._year_only(self.year_only.isChecked())

        self.category = QComboBox()
        for c in categories.CATEGORIES:
            if c.key != "unknown":
                self.category.addItem(c.singular, c.key)
        self.category.setCurrentIndex(max(0, self.category.findData(entry.category if entry else "game")))
        self.platform = QComboBox()
        self.platform.setEditable(True)
        self.platform.addItem("")
        self.platform.addItems([p.name for p in platforms.PLATFORMS])
        self.platform.setCurrentText(entry.platform or "" if entry else "")
        self.notes = QPlainTextEdit(entry.notes or "" if entry else "")
        self.notes.setFixedHeight(70)

        form.addRow("Name", self.title)
        form.addRow("Completed on", date_row)
        form.addRow("Section", self.category)
        form.addRow("Platform", self.platform)
        form.addRow("Notes", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(460, self.sizeHint().height())

    def _year_only(self, on: bool) -> None:
        self.when.setVisible(not on)
        self.year.setVisible(on)

    def completed_on(self) -> str:
        if self.year_only.isChecked():
            return f"{self.year.value():04d}"
        return self.when.date().toPython().isoformat()

    def values(self) -> dict:
        return {"title": self.title.text().strip(), "completed_on": self.completed_on(),
                "category": self.category.currentData(),
                "platform": self.platform.currentText().strip() or None,
                "notes": self.notes.toPlainText().strip() or None}

    def _ok(self) -> None:
        if not self.title.text().strip():
            self.title.setFocus()
            return
        self.accept()


class CompletedPage(QWidget):
    changed = Signal()                 # entries added / removed / moved between years
    open_item = Signal(int)            # library item of an entry
    fetch_covers = Signal(object)      # list of entry ids, or None for every missing one

    def __init__(self, parent=None):
        super().__init__(parent)
        self.year: int | None = None
        self.entries: list[completions.Completion] = []

        head = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setObjectName("Heading")
        self.count = QLabel()
        self.count.setObjectName("Muted")
        add = QPushButton("+ Add…")
        add.setObjectName("Primary")
        add.setToolTip("Add something you finished (Ctrl+N)")
        add.clicked.connect(self.add)
        imp = QPushButton("Import CSV…")
        imp.setToolTip("A year file like 2023.csv: one “name, DD/MM/YYYY” per line")
        imp.clicked.connect(self.import_csv)
        self.export_btn = QToolButton()
        self.export_btn.setText("Export year")
        self.export_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.export_btn)
        menu.addAction("As image…", self.export_image)
        menu.addAction("As CSV…", self.export_csv)
        self.export_btn.setMenu(menu)
        self.covers_btn = QPushButton("Fetch missing covers")
        self.covers_btn.clicked.connect(lambda: self.fetch_covers.emit(None))
        self.sort_btn = QPushButton("Order by date")
        self.sort_btn.setToolTip("Forget your dragged order for this year")
        self.sort_btn.clicked.connect(self._reset_order)
        head.addWidget(self.heading)
        head.addSpacing(10)
        head.addWidget(add)
        head.addSpacing(10)
        head.addWidget(self.count, 0, Qt.AlignBottom)
        head.addStretch(1)
        for w in (imp, self.export_btn, self.covers_btn, self.sort_btn):
            head.addWidget(w)

        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(COVER)
        self.list.setGridSize(QSize(COVER.width() + 24, COVER.height() + 58))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        # Double-click or Enter (connecting itemDoubleClicked too would open it twice)
        self.list.itemActivated.connect(lambda it: self.edit(it.data(IdRole)))
        self.list.model().rowsMoved.connect(self._moved)
        self.stats = QLabel()
        self.stats.setTextFormat(Qt.RichText)
        self.chart = MonthChart()
        stats_row = QHBoxLayout()
        stats_row.addWidget(self.stats, 1)
        stats_row.addWidget(self.chart)
        self.hint = QLabel()
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 8)
        lay.addLayout(head)
        lay.addLayout(stats_row)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.hint)
        QShortcut(QKeySequence.Delete, self.list, activated=self.remove_selected)

    # ------------------------------------------------------------------ showing

    def show_year(self, year: int | None) -> None:
        self.year = year
        self.refresh()

    def refresh(self) -> None:
        with db.session() as conn:
            self.entries = completions.list_year(conn, self.year)
            stats = completions.year_stats(conn, self.year)
        self.stats.setText(stats_text(stats))
        self.chart.set_counts(stats.by_month)
        self.chart.setVisible(self.year is not None and stats.count > stats.undated)
        self.heading.setText(f"Completed in {self.year}" if self.year else "Completed")
        missing = sum(1 for c in self.entries if not c.poster_path())
        n = len(self.entries)
        self.count.setText(f"{n} {'entry' if n == 1 else 'entries'}"
                           + (f" · {missing} without cover" if missing else ""))
        self.list.clear()
        for c in self.entries:
            text = f"{c.title}\n{c.date_text()}" + (f" · {c.platform}" if c.platform else "")
            it = QListWidgetItem(QIcon(_cover(c)), text)
            it.setData(IdRole, c.id)
            it.setToolTip(f"{c.title}\n{c.date_text()}" + (f"\n{c.notes}" if c.notes else ""))
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.list.addItem(it)
        by_year = self.year is not None
        self.list.setDragDropMode(QAbstractItemView.InternalMove if by_year
                                  else QAbstractItemView.NoDragDrop)
        self.export_btn.setEnabled(by_year and n > 0)
        self.sort_btn.setVisible(by_year and any(c.position is not None for c in self.entries))
        self.covers_btn.setVisible(missing > 0)
        self.hint.setText(
            "Drag covers to change their order (the image uses it). Double-click to edit. "
            "Marking something completed in your library adds it here with today's date."
            if by_year else
            "Every year, newest first. Pick a year in the sidebar to arrange it or export it as an "
            "image.")

    def _selected(self) -> list[int]:
        return [it.data(IdRole) for it in self.list.selectedItems()]

    def _menu(self, pos) -> None:
        ids = self._selected()
        if not ids:
            return
        menu = QMenu(self)
        if len(ids) == 1:
            menu.addAction("Edit…", lambda: self.edit(ids[0]))
            entry = next((c for c in self.entries if c.id == ids[0]), None)
            if entry and entry.item_id:
                menu.addAction("Open in library", lambda: self.open_item.emit(entry.item_id))
            menu.addAction("Choose cover image…", lambda: self.choose_cover(ids[0]))
        menu.addAction("Look up cover again", lambda: self.fetch_covers.emit(ids))
        menu.addSeparator()
        menu.addAction("Remove", self.remove_selected)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ changes

    def add(self) -> None:
        dlg = CompletionDialog(year=self.year, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        with db.session() as conn:
            cid = completions.add(conn, v["title"], v["completed_on"], v["category"], v["platform"],
                                  notes=v["notes"])
            needs = completions.get(conn, cid).poster_path() is None
        if self.year and int(v["completed_on"][:4]) != self.year:
            self.year = int(v["completed_on"][:4])
        self.changed.emit()
        if needs:
            self.fetch_covers.emit([cid])

    def edit(self, cid: int) -> None:
        with db.session() as conn:
            entry = completions.get(conn, cid)
        if not entry:
            return
        dlg = CompletionDialog(entry, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        with db.session() as conn:
            completions.update(conn, cid, **v)
        self.changed.emit()
        if v["title"] != entry.title and not entry.item_id:
            self.fetch_covers.emit([cid])

    def remove_selected(self) -> None:
        ids = self._selected()
        if not ids:
            return
        what = self.list.selectedItems()[0].text().split("\n")[0] if len(ids) == 1 else f"{len(ids)} entries"
        if QMessageBox.question(self, "Remove", f"Remove {what} from Completed?") != QMessageBox.Yes:
            return
        with db.session() as conn:
            completions.delete(conn, ids)
        self.changed.emit()

    def choose_cover(self, cid: int) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Cover image", "", "Images (*.jpg *.jpeg *.png *.webp)")
        if path:
            with db.session() as conn:
                completions.set_cover_file(conn, cid, path)
            self.refresh()

    def _moved(self, *_) -> None:
        ids = [self.list.item(i).data(IdRole) for i in range(self.list.count())]
        with db.session() as conn:
            completions.set_order(conn, ids)
        self.sort_btn.setVisible(True)

    def _reset_order(self) -> None:
        with db.session() as conn:
            completions.reset_order(conn, self.year)
        self.refresh()

    # ------------------------------------------------------------------ files

    def import_csv(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import completed list", "", "CSV (*.csv *.txt);;All files (*)")
        if not paths:
            return
        section = categories.get("game")
        if self.year is None and self.entries:
            section = categories.get(self.entries[0].category)
        msgs, total = [], completions.ImportResult()
        new_ids: list[int] = []
        with db.session() as conn:
            for p in paths:
                res = completions.import_csv(conn, p, section.key)
                msgs.append(f"{p.rsplit('/', 1)[-1]}: {res.summary()}")
                msgs += [f"   {e}" for e in res.errors[:8]]
                total.years |= res.years
                new_ids += res.new_ids
            needs = [cid for cid in new_ids if completions.get(conn, cid).poster_path() is None]
        if len(total.years) == 1:
            self.year = next(iter(total.years))
        self.changed.emit()
        QMessageBox.information(self, "Import", "\n".join(msgs) + (
            f"\n\nLooking up covers for {len(needs)} entries in the background." if needs else ""))
        if needs:
            self.fetch_covers.emit(needs)

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export year", f"{self.year}.csv", "CSV (*.csv)")
        if path:
            with db.session() as conn:
                completions.export_csv(conn, self.year, path)

    def export_image(self) -> None:
        ExportYearDialog(self.year, self.entries, self).exec()
