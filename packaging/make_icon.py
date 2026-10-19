"""Render the app icon to build/tracking.ico (all Windows sizes) for the exe."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from tracking.ui.theme import draw_icon  # noqa: E402


def main(out: Path) -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841 - QPixmap needs an application
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    draw_icon(256).save(buf, "PNG")
    out.parent.mkdir(parents=True, exist_ok=True)
    png = out.with_suffix(".png")
    png.write_bytes(bytes(buf.data()))
    Image.open(png).save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", out)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/tracking.ico"))
