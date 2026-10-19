import logging
import sys


def selftest() -> int:
    """`Tracking.exe --selftest`: check the bundled pieces work, report to the log
    and to selftest.txt next to the log. Exit code 0 = all good."""
    import os
    import tempfile

    from . import log, titles
    from .scan import classify

    log.setup("INFO")
    results = []

    def check(name, fn):
        try:
            results.append((name, True, repr(fn())))
        except Exception as e:  # report every failure, not just the first
            results.append((name, False, f"{type(e).__name__}: {e}"))

    def guessit_ok():
        from guessit import guessit  # call it directly: parse_video hides errors

        g = guessit("[SubsMix] Ghost in the Shell - S01E05 - Decoy.mkv")
        assert (g.get("season"), g.get("episode")) == (1, 5), dict(g)
        return titles.parse_video("[SubsMix] Ghost in the Shell - S01E05 - Decoy.mkv")

    check("guessit", guessit_ok)
    check("rapidfuzz", lambda: __import__("rapidfuzz").fuzz.ratio("okami", "okami hd"))
    check("platform tags", lambda: classify.platform_from_tags("Game [01004E90149AA000][v0]"))

    def mediainfo():
        from pymediainfo import MediaInfo

        assert MediaInfo.can_parse(), "MediaInfo library not found"
        with tempfile.NamedTemporaryFile(suffix=".mkv", delete=False) as f:
            f.write(b"\x1a\x45\xdf\xa3")
        try:
            return len(MediaInfo.parse(f.name).tracks)
        finally:
            os.unlink(f.name)

    check("mediainfo", mediainfo)
    check("howlongtobeat", lambda: __import__("howlongtobeatpy").HowLongToBeat.__name__)
    check("qt", lambda: __import__("PySide6.QtWidgets").__version__)

    lines = [f"{'OK  ' if ok else 'FAIL'} {name}: {detail}" for name, ok, detail in results]
    for ln in lines:
        logging.getLogger("tracking").info("selftest %s", ln)
    (log.log_dir() / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if all(ok for _, ok, _ in results) else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    from PySide6.QtWidgets import QApplication

    from . import db, log
    from .ui import theme
    from .ui.main_window import MainWindow

    with db.session() as conn:
        log.setup(db.get_setting(conn, "log_level", "INFO"))
    logging.getLogger("tracking").info("Tracking starting")

    app = QApplication(sys.argv)
    app.setApplicationName("Tracking")
    app.setWindowIcon(theme.app_icon())
    theme.apply(app)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
