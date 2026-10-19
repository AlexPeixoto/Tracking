import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_jobs_queue_instead_of_refusing(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    from tracking.ui import main_window
    from tracking.ui.workers import Job, ScanJob

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(main_window.QTimer, "singleShot", lambda *a: None)  # no first-run dialog
    win = main_window.MainWindow()
    ran: list[str] = []

    class Fake(Job):
        def __init__(self, name, secs):
            super().__init__()
            self.name, self.secs = name, secs

        def work(self, conn):
            time.sleep(self.secs)
            ran.append(self.name)
            return f"{self.name} done"

    class FakeScan(ScanJob, Fake):
        def __init__(self, name, secs):
            Fake.__init__(self, name, secs)

        work = Fake.work

    win.start_job(Fake("metadata", 0.5))
    win.start_job(Fake("later", 0.05))
    win.start_job(FakeScan("new drive scan", 0.05))  # jumps the queue
    assert "(+2 queued)" in win.status.text()

    deadline = time.time() + 10
    while (win.job or win.queue) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert ran == ["metadata", "new drive scan", "later"]
    assert win.status.text() == "later done"
