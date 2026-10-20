""""Export year as image": the year's covers in one grid picture.

The same idea as a montage of IGDB covers (a near-square grid, no gaps by
default), drawn with Qt so nothing else needs installing. Options: columns,
cover width and shape, gap, margin, background, a heading and labels; the
dialog shows the resulting size in pixels and a preview before saving.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .. import categories, completions, db
from .grid import load_scaled

SETTING = "year_image"
PRESETS = "year_image_presets"
STAMPS = {"none": "No stamp", "top-left": "Top left", "top-right": "Top right",
          "bottom-left": "Bottom left", "bottom-right": "Bottom right"}
SHAPES = {"3:4 (game box)": 4 / 3, "2:3 (movie poster)": 3 / 2, "IGDB cover (264×374)": 374 / 264,
          "Square": 1.0}
LABELS = {"none": "No labels", "title": "Title", "date": "Date", "both": "Title and date"}
BACKGROUNDS = {"transparent": "Transparent", "#000000": "Black", "#ffffff": "White",
               "#16161c": "Dark grey"}


@dataclass
class Options:
    columns: int = 0                 # 0 = automatic, nearly square
    cover_width: int = 264           # px, like IGDB's cover_big
    shape: str = "IGDB cover (264×374)"
    gap: int = 0
    margin: int = 0
    background: str = "transparent"  # or "#rrggbb"
    heading: bool = False
    heading_text: str = "Completed in {year}"
    labels: str = "none"
    crop: bool = True                # fill the cell (crop) or fit the whole cover
    stamp: str = "none"              # a small "2024 · 14 completed" badge in a corner
    stamp_text: str = "{year} · {count} completed"
    skip_no_cover: bool = False      # leave out entries without a cover (instead of a name tile)
    sections: list[str] = field(default_factory=list)  # e.g. ["game"]; empty = every section

    @property
    def cover_height(self) -> int:
        return round(self.cover_width * SHAPES.get(self.shape, 4 / 3))

    @classmethod
    def load(cls, conn) -> Options:
        try:
            data = json.loads(db.get_setting(conn, SETTING) or "{}")
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (ValueError, TypeError):
            return cls()

    def save(self, conn) -> None:
        db.set_setting(conn, SETTING, json.dumps(asdict(self)))


def load_presets(conn) -> dict[str, Options]:
    """Your named option sets ("Twitter grid", "Big poster"...)."""
    try:
        raw = json.loads(db.get_setting(conn, PRESETS) or "{}")
    except ValueError:
        return {}
    fields = Options.__dataclass_fields__
    return {name: Options(**{k: v for k, v in d.items() if k in fields}) for name, d in raw.items()}


def save_preset(conn, name: str, opts: Options | None) -> None:
    """Store ``opts`` under ``name``; None deletes it."""
    presets = {n: asdict(o) for n, o in load_presets(conn).items()}
    if opts is None:
        presets.pop(name, None)
    else:
        presets[name] = asdict(opts)
    db.set_setting(conn, PRESETS, json.dumps(presets))


def grid_columns(n: int, opts: Options) -> int:
    if opts.columns > 0:
        return opts.columns
    return max(1, math.ceil(math.sqrt(n)))


def _label_lines(c: completions.Completion, opts: Options) -> list[str]:
    return {"none": [], "title": [c.title], "date": [c.date_text()],
            "both": [c.title, c.date_text()]}[opts.labels]


def layout(n: int, opts: Options) -> tuple[int, int, int, int, int, int]:
    """(width, height, columns, rows, label height, heading height) in pixels."""
    cols = grid_columns(n, opts)
    rows = max(1, math.ceil(n / cols)) if n else 1
    label_h = 0
    if opts.labels != "none":
        lines = 2 if opts.labels == "both" else 1
        label_h = round(opts.cover_width * 0.085) * lines + 8
    heading_h = round(opts.cover_width * 0.32) if opts.heading else 0
    w = opts.margin * 2 + cols * opts.cover_width + (cols - 1) * opts.gap
    h = (opts.margin * 2 + heading_h + rows * (opts.cover_height + label_h)
         + (rows - 1) * opts.gap)
    return w, h, cols, rows, label_h, heading_h


def shown(entries: list[completions.Completion], opts: Options) -> list[completions.Completion]:
    """The entries that go in the picture."""
    return [c for c in entries
            if (not opts.skip_no_cover or c.poster_path())
            and (not opts.sections or c.category in opts.sections)]


def render(entries: list[completions.Completion], year: int, opts: Options) -> QImage:
    entries = shown(entries, opts)
    w, h, cols, _rows, label_h, heading_h = layout(len(entries), opts)
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    transparent = opts.background == "transparent"
    bg = QColor(0, 0, 0, 0) if transparent else QColor(opts.background)
    img.fill(bg)
    dark = transparent or bg.lightness() < 128
    ink = QColor("#f2f2f5") if dark else QColor("#15151a")

    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    if heading_h:
        f = QFont()
        f.setPixelSize(max(12, round(heading_h * 0.5)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(ink)
        p.drawText(QRect(opts.margin, opts.margin, w - 2 * opts.margin, heading_h),
                   Qt.AlignCenter, opts.heading_text.replace("{year}", str(year)))

    label_font = QFont()
    label_font.setPixelSize(max(9, round(opts.cover_width * 0.07)))
    cw, ch = opts.cover_width, opts.cover_height
    for i, c in enumerate(entries):
        r, col = divmod(i, cols)
        x = opts.margin + col * (cw + opts.gap)
        y = opts.margin + heading_h + r * (ch + label_h + opts.gap)
        cell = QRect(x, y, cw, ch)
        path = c.poster_path()
        pix = (load_scaled(path, cw, ch) if path else None) or QPixmap()
        if not pix.isNull():
            mode = Qt.KeepAspectRatioByExpanding if opts.crop else Qt.KeepAspectRatio
            scaled = pix.scaled(cw, ch, mode, Qt.SmoothTransformation)
            sx, sy = (scaled.width() - cw) // 2, (scaled.height() - ch) // 2
            if opts.crop:
                p.drawPixmap(cell, scaled, QRect(max(0, sx), max(0, sy), cw, ch))
            else:
                p.drawPixmap(x + (cw - scaled.width()) // 2, y + (ch - scaled.height()) // 2, scaled)
        else:  # no cover: a tile with the name, so the picture still lists it
            p.fillRect(cell, QColor("#2a2a36"))
            f = QFont()
            f.setPixelSize(max(10, round(cw * 0.09)))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#e8e8ee"))
            p.drawText(cell.adjusted(8, 8, -8, -8), Qt.AlignCenter | Qt.TextWordWrap, c.title)
        lines = _label_lines(c, opts)
        if lines:
            p.setFont(label_font)
            p.setPen(ink)
            p.drawText(QRectF(x, y + ch + 4, cw, label_h - 4), Qt.AlignHCenter | Qt.AlignTop,
                       "\n".join(p.fontMetrics().elidedText(t, Qt.ElideRight, cw) for t in lines))
    if opts.stamp != "none":
        _stamp(p, w, h, opts, opts.stamp_text.replace("{year}", str(year))
               .replace("{count}", str(len(entries))))
    p.end()
    return img


def _stamp(p: QPainter, w: int, h: int, opts: Options, text: str) -> None:
    f = QFont()
    f.setPixelSize(max(11, round(opts.cover_width * 0.075)))
    f.setBold(True)
    p.setFont(f)
    fm = p.fontMetrics()
    pad = max(4, f.pixelSize() // 2)
    bw, bh = fm.horizontalAdvance(text) + 2 * pad, fm.height() + pad
    inset = opts.margin + max(6, opts.cover_width // 30)
    x = inset if "left" in opts.stamp else w - inset - bw
    y = inset if "top" in opts.stamp else h - inset - bh
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 170))
    p.drawRoundedRect(QRectF(x, y, bw, bh), bh / 3, bh / 3)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRect(x, y, bw, bh), Qt.AlignCenter, text)


class ExportYearDialog(QDialog):
    """Options + live size / preview; saves PNG or JPG."""

    def __init__(self, year: int, entries: list[completions.Completion], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Export {year} as image")
        self.year, self.entries = year, entries
        with db.session() as conn:
            self.opts = Options.load(conn)

        form = QFormLayout()
        preset_row = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.setToolTip("Saved option sets")
        save_preset_btn = QPushButton("Save as…")
        save_preset_btn.clicked.connect(self._save_preset)
        self.del_preset = QPushButton("Delete")
        self.del_preset.clicked.connect(self._delete_preset)
        preset_row.addWidget(self.preset, 1)
        preset_row.addWidget(save_preset_btn)
        preset_row.addWidget(self.del_preset)
        form.addRow("Preset", preset_row)
        self.columns = QSpinBox()
        self.columns.setRange(0, 30)
        self.columns.setSpecialValueText("Automatic")
        self.cover_width = QSpinBox()
        self.cover_width.setRange(60, 1200)
        self.cover_width.setSuffix(" px")
        self.shape = QComboBox()
        self.shape.addItems(SHAPES)
        self.crop = QCheckBox("Fill the shape (crop covers that don't fit)")
        self.sections: dict[str, QCheckBox] = {}
        sections_row = QHBoxLayout()
        present = {c.category for c in entries}
        for c in categories.CATEGORIES:
            if c.key in present:
                box = QCheckBox(c.label)
                box.setChecked(True)
                self.sections[c.key] = box
                sections_row.addWidget(box)
        sections_row.addStretch(1)
        self.skip = QCheckBox("Leave out entries without a cover")
        self.skip.setToolTip("Otherwise they show as a tile with their name")
        self.gap = QSpinBox()
        self.gap.setRange(0, 200)
        self.gap.setSuffix(" px")
        self.margin = QSpinBox()
        self.margin.setRange(0, 400)
        self.margin.setSuffix(" px")
        bg_row = QHBoxLayout()
        self.background = QComboBox()
        for key, label in BACKGROUNDS.items():
            self.background.addItem(label, key)
        pick = QPushButton("Other…")
        pick.clicked.connect(self._pick_color)
        bg_row.addWidget(self.background, 1)
        bg_row.addWidget(pick)
        self.heading = QCheckBox("Heading")
        self.heading_text = QLineEdit()
        head_row = QHBoxLayout()
        head_row.addWidget(self.heading)
        head_row.addWidget(self.heading_text, 1)
        self.labels = QComboBox()
        for key, label in LABELS.items():
            self.labels.addItem(label, key)
        stamp_row = QHBoxLayout()
        self.stamp = QComboBox()
        for key, label in STAMPS.items():
            self.stamp.addItem(label, key)
        self.stamp_text = QLineEdit()
        self.stamp_text.setToolTip("{year} and {count} are filled in")
        stamp_row.addWidget(self.stamp)
        stamp_row.addWidget(self.stamp_text, 1)
        if len(self.sections) > 1:
            form.addRow("Include", sections_row)
        form.addRow("Columns", self.columns)
        form.addRow("Cover width", self.cover_width)
        form.addRow("Cover shape", self.shape)
        form.addRow("", self.crop)
        form.addRow("", self.skip)
        form.addRow("Gap between covers", self.gap)
        form.addRow("Margin", self.margin)
        form.addRow("Background", bg_row)
        form.addRow("Title", head_row)
        form.addRow("Under each cover", self.labels)
        form.addRow("Corner stamp", stamp_row)

        self.size = QLabel()
        self.size.setObjectName("Muted")
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(360, 360)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save = QPushButton("Save image…")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(save)
        buttons.addWidget(close)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(self.size)
        missing = sum(1 for c in entries if not c.poster_path())
        if missing:
            note = QLabel(f"{missing} entries have no cover yet: they show as a name tile, or tick "
                          "“Leave out entries without a cover”. “Fetch missing covers” on the "
                          "Completed page looks them up.")
            note.setWordWrap(True)
            note.setObjectName("Muted")
            left.addWidget(note)
        left.addStretch(1)
        left.addLayout(buttons)
        lay = QHBoxLayout(self)
        lay.addLayout(left)
        lay.addWidget(self.preview, 1)

        self._load_presets()
        self.preset.currentIndexChanged.connect(self._pick_preset)
        self._fill()
        for w in (self.columns, self.cover_width, self.gap, self.margin):
            w.valueChanged.connect(self._changed)
        for w in (self.shape, self.background, self.labels, self.stamp):
            w.currentIndexChanged.connect(self._changed)
        for w in (self.crop, self.heading, self.skip, *self.sections.values()):
            w.toggled.connect(self._changed)
        self.heading_text.textChanged.connect(self._changed)
        self.stamp_text.textChanged.connect(self._changed)
        self._changed()

    def _fill(self) -> None:
        o = self.opts
        self.columns.setValue(o.columns)
        self.cover_width.setValue(o.cover_width)
        self.shape.setCurrentText(o.shape if o.shape in SHAPES else next(iter(SHAPES)))
        self.crop.setChecked(o.crop)
        self.skip.setChecked(o.skip_no_cover)
        for key, box in self.sections.items():
            box.setChecked(not o.sections or key in o.sections)
        self.gap.setValue(o.gap)
        self.margin.setValue(o.margin)
        if self.background.findData(o.background) < 0:
            self.background.addItem(o.background, o.background)
        self.background.setCurrentIndex(self.background.findData(o.background))
        self.heading.setChecked(o.heading)
        self.heading_text.setText(o.heading_text)
        self.labels.setCurrentIndex(max(0, self.labels.findData(o.labels)))
        self.stamp.setCurrentIndex(max(0, self.stamp.findData(o.stamp)))
        self.stamp_text.setText(o.stamp_text)
        self.stamp_text.setCursorPosition(0)
        self.heading_text.setCursorPosition(0)

    def _read(self) -> Options:
        chosen = [k for k, box in self.sections.items() if box.isChecked()]
        return Options(
            columns=self.columns.value(), cover_width=self.cover_width.value(),
            shape=self.shape.currentText(), gap=self.gap.value(), margin=self.margin.value(),
            background=self.background.currentData(), heading=self.heading.isChecked(),
            heading_text=self.heading_text.text() or "Completed in {year}",
            labels=self.labels.currentData(), crop=self.crop.isChecked(),
            stamp=self.stamp.currentData(),
            stamp_text=self.stamp_text.text() or "{year} · {count} completed",
            skip_no_cover=self.skip.isChecked(),
            # all ticked = no filter, so a section added later is included by default
            sections=[] if len(chosen) == len(self.sections) else chosen)

    # ------------------------------------------------------------------ presets

    def _load_presets(self, select: str | None = None) -> None:
        with db.session() as conn:
            self.presets = load_presets(conn)
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem("Last used", None)
        for name in sorted(self.presets, key=str.lower):
            self.preset.addItem(name, name)
        self.preset.setCurrentIndex(max(0, self.preset.findData(select)))
        self.preset.blockSignals(False)
        self.del_preset.setEnabled(select is not None)

    def _pick_preset(self, _index=None) -> None:
        name = self.preset.currentData()
        self.del_preset.setEnabled(name is not None)
        if name is None:
            with db.session() as conn:
                self.opts = Options.load(conn)
        else:
            self.opts = self.presets[name]
        self._fill()
        self._changed()

    def _save_preset(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Save preset", "Name for these settings:",
                                        text=self.preset.currentData() or "")
        if ok and name.strip():
            with db.session() as conn:
                save_preset(conn, name.strip(), self._read())
            self._load_presets(select=name.strip())

    def _delete_preset(self) -> None:
        name = self.preset.currentData()
        if name and QMessageBox.question(self, "Delete preset", f"Delete “{name}”?") == QMessageBox.Yes:
            with db.session() as conn:
                save_preset(conn, name, None)
            self._load_presets()

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(parent=self)
        if c.isValid():
            name = c.name()
            if self.background.findData(name) < 0:
                self.background.addItem(name, name)
            self.background.setCurrentIndex(self.background.findData(name))

    def _changed(self, *_) -> None:
        self.opts = self._read()
        self.heading_text.setEnabled(self.opts.heading)
        self.stamp_text.setEnabled(self.opts.stamp != "none")
        n = len(shown(self.entries, self.opts))
        w, h, cols, rows, *_ = layout(n, self.opts)
        self.size.setText(f"{n} covers · {cols} × {rows} grid · "
                          f"<b>{w} × {h} px</b>")
        img = render(self.entries, self.year, self.opts)
        self.preview.setPixmap(QPixmap.fromImage(img).scaled(
            self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _save(self) -> None:
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save image", f"completed-{self.year}.png", "PNG image (*.png);;JPEG image (*.jpg)")
        if not path:
            return
        opts = self._read()
        jpg = path.lower().endswith((".jpg", ".jpeg")) or ("JPEG" in chosen and "." not in path[-5:])
        if jpg and opts.background == "transparent":
            opts.background = "#000000"  # JPEG has no transparency
        img = render(self.entries, self.year, opts)
        if jpg:
            img = img.convertToFormat(QImage.Format_RGB32)
        if not img.save(path, quality=92):
            QMessageBox.warning(self, "Export", f"Couldn't save {path}.")
            return
        with db.session() as conn:
            self._read().save(conn)
        self.accept()
