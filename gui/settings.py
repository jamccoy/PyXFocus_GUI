"""
Remember the desktop between runs -- the only QSettings consumer in the app.

Everything Qt persists for us goes through :class:`AppSettings`, for two
reasons. Every key string appears exactly once, so a typo is a mistake in
one file rather than a value written to ``window/tab`` and read back from
``windows/tab``. And the underlying ``QSettings`` is injectable, so the test
suite runs against a temporary INI and can never write the developer's real
preferences -- a settings test that "passes" by mutating
``~/Library/Preferences`` has already done damage.

**Every read passes ``type=``.** This is not defensive style; it is the one
bug this module exists to prevent. Measured across a process boundary (write,
quit, relaunch -- the actual case) against an INI backend,
``value('ui/auto_trace')`` returns the *string* ``'false'``, which is truthy,
and ``value('window/tab')`` returns ``'3'``. The macOS native plist preserves
real types, so an untyped read works perfectly on this machine and fails only
on a Linux or Windows one. ``test_settings_typed_reads_survive_a_process_boundary``
is the tripwire, and it has to stay cross-process: QSettings caches in memory,
so an in-process read hands back the Python object that was written and passes
even when the code is wrong.

No second serializer. ``session/parameters`` holds exactly the mapping
:mod:`PyXFocus.gui.config` writes into a file's ``parameters`` block --
``json.dumps(params.to_dict())`` out, ``WolterParams.from_dict()`` in -- so a
stale session degrades field by field with notes, exactly as a stale file
does, and there is one place to change when a parameter is added.

This module may import :mod:`PyXFocus.gui.config`; config must never import
this one. ``config`` is Qt-free on purpose (the README promises PyQt5 is
needed "only if you want the GUI") and ``test_config_imports_without_qt``
keeps it that way.
"""

import json
import os
import sys

from PyQt5 import QtCore, QtWidgets

from PyXFocus.gui import config
from PyXFocus.gui import wolter


#: Identify the application to Qt. No organizationDomain, deliberately: Qt
#: derives the preferences filename from it, so setting one later moves
#: ~/Library/Preferences/com.pyxfocus.WolterExplorer.plist and silently
#: orphans every existing user's settings.
ORGANISATION = 'PyXFocus'
APPLICATION = 'WolterExplorer'
DISPLAY_NAME = 'Wolter-I Explorer'

RESET_FLAG = '--reset-settings'

#: Most recent configuration files to remember.
MAX_RECENT = 8

#: Bump whenever the toolbar or dock set changes, so Qt discards a state blob
#: saved against the old layout. Without this, the empty-toolbar blob written
#: today would restore a brand-new toolbar hidden on the first launch after
#: the upgrade -- a bug that looks like the toolbar was never implemented.
WINDOW_STATE_VERSION = 1

#: How much of a restored window, and of its title bar, must land on a screen.
#: Deliberately permissive: a window 90% off the bottom-right is still
#: perfectly usable if its title bar is grabbable, and recentring a window
#: someone parked at a screen edge on purpose is its own annoying bug.
MIN_VISIBLE_FRACTION = 0.25
TITLE_BAR_HEIGHT = 30

DEFAULT_SIZE = (1180, 780)


def apply_identity():
    """Tell Qt who we are, so QSettings knows where to write."""
    QtCore.QCoreApplication.setOrganizationName(ORGANISATION)
    QtCore.QCoreApplication.setApplicationName(APPLICATION)


def take_reset_flag(argv):
    """
    Pull ``--reset-settings`` out of ``argv``, returning ``(rest, wanted)``.

    Hand-rolled rather than argparse: argparse takes over ``-h`` and rejects
    the flags QApplication legitimately consumes (``-style``, ``-platform``),
    so adding it would break ways of starting the app that work today.
    """
    argv = list(argv)
    wanted = RESET_FLAG in argv
    while RESET_FLAG in argv:
        argv.remove(RESET_FLAG)
    return argv, wanted


# --------------------------------------------------------------------------
# Geometry helpers -- pure QRect work, so they need no QApplication and most
# of the risky logic in this module is testable without a display.
# --------------------------------------------------------------------------

def available_rects():
    """Usable area of every attached screen, menu bar and Dock excluded."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return []
    return [screen.availableGeometry() for screen in app.screens()]


def title_strip(rect, title_height=TITLE_BAR_HEIGHT):
    """
    The band that has to stay grabbable, given a window rect.

    Called before ``show()``, where Qt does not yet know the frame margins
    and ``frameGeometry()`` is still the client rect -- so the title bar is
    the band immediately *above* it. Testing the top of the client rect
    instead would pass a window whose title bar sits under the menu bar,
    which is the case worth catching.
    """
    return QtCore.QRect(rect.x(), rect.y() - title_height,
                        rect.width(), title_height)


def is_reachable(rect, rects, fraction=MIN_VISIBLE_FRACTION,
                 title_height=TITLE_BAR_HEIGHT):
    """
    True if a restored window lands somewhere the user can actually reach.

    ``restoreGeometry`` returns True whenever the blob merely parsed, so its
    return value is not a check on where the window ended up.

    Two tests, both needed: enough of the window is on some screen to be
    recognised, and enough of its **title bar** is -- a window whose body is
    visible but whose title bar is off the top cannot be moved or closed with
    the mouse. The title-bar clause does nearly all the work.

    Intersections are summed rather than unioned. Screens do not overlap in a
    sane layout, and the error direction of summing is "reachable", which is
    the cheap mistake: a false positive leaves a window where it was, a false
    negative merely recentres one that was fine.
    """
    if rect.isEmpty() or not rects:
        return False

    strip = title_strip(rect, title_height)
    body_area = float(rect.width() * rect.height())
    strip_area = float(strip.width() * strip.height())
    if body_area <= 0 or strip_area <= 0:
        return False

    seen_body = seen_strip = 0.
    for available in rects:
        part = rect.intersected(available)
        seen_body += part.width() * part.height()
        bar = strip.intersected(available)
        seen_strip += bar.width() * bar.height()

    return (seen_body >= fraction * body_area and
            seen_strip >= fraction * strip_area)


def recentre(window, rects=None):
    """
    Put a window back on the primary screen, at a size that fits it.

    Resizing matters as much as moving: a window saved at 2400x1400 on an
    external display and restored on a 1440x900 laptop is centred and still
    larger than the screen, with its title bar off the top again.

    ``QApplication.screenAt`` would be the natural choice but is Qt 5.10; we
    are on 5.9, so the primary screen it is.
    """
    if rects is None:
        rects = available_rects()
    target = rects[0] if rects else QtCore.QRect(0, 0, *DEFAULT_SIZE)

    size = window.size().boundedTo(target.size())
    if size.isEmpty():
        size = QtCore.QSize(*DEFAULT_SIZE).boundedTo(target.size())
    window.resize(size)
    window.move(
        target.x() + max(0, (target.width() - size.width()) // 2),
        target.y() + max(TITLE_BAR_HEIGHT,
                         (target.height() - size.height()) // 2))


def _dedupe_key(path):
    """
    The identity two recent-file entries are compared on.

    ``os.path.normcase`` is a no-op on POSIX -- it only lower-cases on
    Windows -- so the obvious ``normcase(abspath(...))`` does nothing about
    case on a default (case-insensitive) APFS volume, which is the only
    situation it would be reached for. Verified: on darwin
    ``normcase('/x/A.json') != normcase('/x/a.json')``. Case-fold explicitly
    on the two platforms whose default filesystem ignores case.

    Knowingly a heuristic: on a case-*sensitive* volume this merges two
    genuinely different files. Showing one file twice in a list of eight is
    the worse of the two harms.
    """
    path = os.path.normpath(os.path.abspath(path))
    return path.lower() if sys.platform in ('darwin', 'win32') else path


# --------------------------------------------------------------------------
# The facade
# --------------------------------------------------------------------------

class AppSettings(object):
    """Everything the app remembers between runs, behind one object."""

    GEOMETRY = 'window/geometry'
    #: The docs viewer's own geometry. A separate key, not a separate code
    #: path: it goes through restore_geometry/save_geometry like the main
    #: window, so the off-screen guard covers it too. A docs window restored
    #: onto a monitor that is no longer attached is exactly as lost.
    DOCS_GEOMETRY = 'window/docs_geometry'
    STATE = 'window/state'
    SPLITTER = 'window/splitter'
    TAB = 'window/tab'
    AUTO_TRACE = 'ui/auto_trace'
    LAST_DIR = 'files/last_dir'
    RECENT = 'files/recent'
    CONFIG_PATH = 'files/config_path'
    PARAMETERS = 'session/parameters'
    VERSION = 'session/version'
    MODIFIED = 'session/modified'

    def __init__(self, settings=None):
        self._settings = settings if settings is not None else QtCore.QSettings()

    @classmethod
    def for_file(cls, path):
        """A store backed by a specific INI file. For tests."""
        return cls(QtCore.QSettings(path, QtCore.QSettings.IniFormat))

    @staticmethod
    def default_file_name():
        """Where the real store lives, without opening it for writing."""
        return QtCore.QSettings(QtCore.QSettings.NativeFormat,
                                QtCore.QSettings.UserScope,
                                ORGANISATION, APPLICATION).fileName()

    def file_name(self):
        return self._settings.fileName()

    def sync(self):
        self._settings.sync()

    def reset(self):
        """
        Forget everything -- through Qt, never by deleting the file.

        On macOS the preferences daemon holds the plist in memory and
        rewrites it from its own cache, so removing the file appears to work
        and then the old settings come back, sometimes not until the next
        logout. ``clear()`` goes through the daemon, which is the only thing
        that actually forgets.
        """
        self._settings.clear()
        self._settings.sync()

    # -- typed access ------------------------------------------------------

    def _get(self, key, default, kind):
        """
        One read, always typed, with a default of the same type.

        Without ``type=``, an INI backend hands back ``'false'`` for a bool
        and ``'3'`` for an int -- and ``'false'`` is truthy, so auto-trace
        comes back on after being saved off.

        A typed read of unconvertible text raises (measured: ``TypeError``
        for ``type=int`` over ``'abc'``). Losing one remembered preference is
        not worth refusing to start, and the user's real work is in
        ``session/parameters``, which a crash here would take with it.
        """
        try:
            got = self._settings.value(key, default, type=kind)
        except (TypeError, ValueError):
            return default
        return default if got is None else got

    def _get_json(self, key, default):
        """
        A structured value stored as JSON text rather than natively.

        QSettings will happily store a Python list, and an INI file then
        contains ``@Variant(\\0\\0\\0\\t...)`` binary -- it round-trips, and
        the file stops being readable, which for a list of paths defeats the
        point. JSON text is identical in both backends.
        """
        text = self._get(key, '', str)
        if not text:
            return default
        try:
            return json.loads(text)
        except ValueError:
            return default

    def _set_json(self, key, value):
        self._settings.setValue(key, json.dumps(value, allow_nan=False))

    # -- window layout -----------------------------------------------------

    def restore_geometry(self, window, rects=None, key=None):
        """
        Put the window back where it was, if that is still somewhere real.

        Call **before** ``show()``: afterwards the window has already been
        mapped, so the user watches it jump.

        ``key`` selects which window: the main one by default, or
        :attr:`DOCS_GEOMETRY` for the documentation viewer.

        Returns True only when the saved rect was used as it stands; False
        means nothing was saved, or it was recentred.
        """
        blob = self._get(key or self.GEOMETRY, QtCore.QByteArray(),
                         QtCore.QByteArray)
        if blob.isEmpty() or not window.restoreGeometry(blob):
            return False

        # restoreGeometry restores the minimised state too, and an app that
        # launches straight into the Dock looks as broken as one that never
        # appears. Full screen is left alone -- that one was deliberate.
        window.setWindowState(window.windowState() & ~QtCore.Qt.WindowMinimized)

        if rects is None:
            rects = available_rects()
        if is_reachable(window.frameGeometry(), rects):
            return True
        recentre(window, rects)
        return False

    def save_geometry(self, window, key=None):
        self._settings.setValue(key or self.GEOMETRY, window.saveGeometry())

    def restore_window_state(self, window):
        """
        Restore toolbars and docks. Call **after** every one of them exists.

        A QToolBar without ``setObjectName`` is skipped by ``saveState()``
        with only a warning on stderr, which nobody reads.
        """
        blob = self._get(self.STATE, QtCore.QByteArray(), QtCore.QByteArray)
        if blob.isEmpty():
            return False
        return window.restoreState(blob, WINDOW_STATE_VERSION)

    def save_window_state(self, window):
        self._settings.setValue(self.STATE,
                                window.saveState(WINDOW_STATE_VERSION))

    def splitter_state(self):
        return self._get(self.SPLITTER, QtCore.QByteArray(), QtCore.QByteArray)

    def set_splitter_state(self, blob):
        self._settings.setValue(self.SPLITTER, blob)

    def tab(self, default=0):
        value = self._get(self.TAB, default, int)
        return value if value >= 0 else default

    def set_tab(self, index):
        self._settings.setValue(self.TAB, int(index))

    # -- widget state ------------------------------------------------------

    def auto_trace(self, default=True):
        return self._get(self.AUTO_TRACE, default, bool)

    def set_auto_trace(self, on):
        # bool(), not the raw argument: Qt.CheckState is an int, and a numpy
        # bool_ stores as something neither backend reads back as a bool.
        self._settings.setValue(self.AUTO_TRACE, bool(on))

    def modified(self, default=False):
        return self._get(self.MODIFIED, default, bool)

    def set_modified(self, flag):
        self._settings.setValue(self.MODIFIED, bool(flag))

    # -- files -------------------------------------------------------------

    def last_dir(self):
        return self._get(self.LAST_DIR, '', str)

    def set_last_dir(self, path):
        self._settings.setValue(self.LAST_DIR, str(path or ''))

    def config_path(self):
        return self._get(self.CONFIG_PATH, '', str)

    def set_config_path(self, path):
        self._settings.setValue(self.CONFIG_PATH, str(path or ''))

    def recent(self):
        """Recently opened configurations, newest first."""
        paths = self._get_json(self.RECENT, [])
        if not isinstance(paths, list):
            return []
        return [p for p in paths if isinstance(p, str)][:MAX_RECENT]

    def set_recent(self, paths):
        self._set_json(self.RECENT, list(paths)[:MAX_RECENT])

    def remember_recent(self, path):
        """Move ``path`` to the front of the recent list, deduped and capped."""
        path = os.path.abspath(path)
        key = _dedupe_key(path)
        kept = [p for p in self.recent() if _dedupe_key(p) != key]
        self.set_recent([path] + kept)

    # -- the session itself ------------------------------------------------

    def set_session_params(self, params):
        """
        Remember the current design, in the file format's own words.

        The value is exactly what :mod:`config` writes into a file's
        ``parameters`` block: one serializer, two destinations.
        """
        try:
            text = json.dumps(params.to_dict(), allow_nan=False)
        except ValueError:
            # This runs on quit. A non-finite value must cost the remembered
            # session, never the ability to quit.
            return
        self._settings.setValue(self.PARAMETERS, text)
        self._settings.setValue(self.VERSION, int(config.VERSION))

    def session_params(self, problems=None):
        """
        The design from the last session, or None to start from defaults.

        Goes through the same ``WolterParams.from_dict`` a configuration file
        does, so a session written before a parameter was added or removed
        degrades field by field with notes rather than taking the launch down
        -- the worst possible place for it.
        """
        if problems is None:
            problems = []

        text = self._get(self.PARAMETERS, '', str)
        if not text:
            return None

        version = self._get(self.VERSION, config.VERSION, int)
        if version > config.VERSION:
            # Written by a newer build sharing this store. Asymmetric for the
            # same reason config._migrate is: a future field's meaning is
            # unknowable, an older one is always readable.
            problems.append('the remembered session was written by a newer '
                            'version of the explorer; starting from defaults')
            return None

        try:
            data = json.loads(text)
        except ValueError:
            problems.append('the remembered session could not be read; '
                            'starting from defaults')
            return None
        if not isinstance(data, dict):
            problems.append('the remembered session is a %s, not an object; '
                            'starting from defaults' % type(data).__name__)
            return None

        if version < config.VERSION:
            # A session is the same parameter block a file carries, so it
            # earns the same migrations. Without this the version recorded
            # above is written and checked but never actually used for
            # anything, and an older session degrades field by field with
            # notes that a migration would have answered properly.
            data = config.migrate_parameters(data, version, problems)

        return wolter.WolterParams.from_dict(data, problems)
