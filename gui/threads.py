"""
Keep a running QThread alive after the widget that started it is gone.

Called by :mod:`PyXFocus.gui.app` (the trace) and
:mod:`PyXFocus.gui.tabs.sweep` (the sweep). Holds one piece of state, the set
of threads still running, and needs a QApplication before the first start().

Qt aborts the whole process when a QThread is destroyed while it is still
running, and a thread parented to a window is destroyed with that window.
Under PySide6 a window really is destroyed when its last Python reference
goes -- closing it, or quitting while a trace runs -- so the workers are
started without a parent and held here until they finish instead. A signal
connected to a method of a widget that has gone is disconnected by Qt, so a
late result is simply dropped.
"""

from PySide6 import QtCore

#: Every thread started through :func:`start` that has not finished yet.
_running = set()


class _Releaser(QtCore.QObject):
    """Lives on the main thread, so ``finished`` reaches it queued."""

    @QtCore.Slot()
    def release(self):
        # Runs on the main thread after run() has returned, so the last
        # reference is never dropped from inside the thread it refers to.
        _running.discard(self.sender())


_releaser = None


def start(thread):
    """Start ``thread``, holding a reference until its ``finished`` fires."""
    global _releaser
    if thread.parent() is not None:
        raise ValueError('a thread kept alive here must have no parent')
    if _releaser is None:
        _releaser = _Releaser()
    _running.add(thread)
    thread.finished.connect(_releaser.release)
    thread.start()


def running():
    """The threads still held, for a test or a quit handler to wait on."""
    return set(_running)


def wait_all():
    """Block until every held thread has finished, as the app quits."""
    for thread in list(_running):
        thread.wait()
