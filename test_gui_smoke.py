#!/usr/bin/env python
"""
Qt-level smoke tests: settings persistence and window restore.

Kept separate from ``test_smoke.py`` on purpose. That file is the
post-build install check and imports no Qt at all, because the README
promises PyQt5 is needed "only if you want the GUI" -- so a user who
followed it and built the Fortran extensions must not see failures for an
optional dependency. Its runner also has no skip state, so a
``try/except ImportError: return`` there would show a green tick for a test
that never ran.

Run with::

    python -m PyXFocus.test_gui_smoke

Nothing here shows a window or runs a full trace unless it says so.
"""

from __future__ import print_function

import json
import os
import subprocess
import sys
import tempfile
import traceback

import numpy as np
from PyQt5 import QtCore, QtWidgets

from PyXFocus.gui import config
from PyXFocus.gui import settings as S
from PyXFocus.gui.wolter import WolterParams, PARAM_FIELDS

RESULTS = []

#: Recorded before anything runs. If a test ever constructs a bare
#: AppSettings() it would write the developer's real preferences, and a
#: mistake in this suite would be indistinguishable from a passing run --
#: until it had already overwritten someone's window layout.
_REAL_STORE = S.AppSettings.default_file_name()
_REAL_STORE_EXISTED = os.path.exists(_REAL_STORE)

APP = None


def _store():
    """A throwaway settings store backed by its own INI file."""
    return S.AppSettings.for_file(os.path.join(tempfile.mkdtemp(), 's.ini'))


def check(name, fn):
    try:
        fn()
    except Exception:
        RESULTS.append((name, False, traceback.format_exc().strip()))
        print('FAIL  %s' % name)
    else:
        RESULTS.append((name, True, ''))
        print('ok    %s' % name)


# -- settings, no QApplication needed --------------------------------------

def test_settings_defaults_when_empty():
    """A fresh store reports the documented defaults, not None."""
    store = _store()
    assert store.auto_trace() is True
    assert store.tab() == 0
    assert store.recent() == []
    assert store.config_path() == ''
    assert store.session_params() is None
    assert store.splitter_state().isEmpty()


def test_settings_round_trip_every_key():
    """
    Each key survives a new store object on the same file.

    Catches the classic bug of a key string that differs between the
    setter and the getter -- which no single-object test would find.
    """
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    a = S.AppSettings.for_file(path)
    a.set_auto_trace(False)
    a.set_tab(3)
    a.set_last_dir('/tmp/somewhere')
    a.set_config_path('/tmp/cfg.json')
    a.set_modified(True)
    a.set_recent(['/tmp/a.json', '/tmp/b.json'])
    a.set_session_params(WolterParams(r0=321.))
    a.sync()

    b = S.AppSettings.for_file(path)
    assert b.auto_trace() is False
    assert b.tab() == 3
    assert b.last_dir() == '/tmp/somewhere'
    assert b.config_path() == '/tmp/cfg.json'
    assert b.modified() is True
    assert b.recent() == ['/tmp/a.json', '/tmp/b.json']
    assert b.session_params().r0 == 321.


def test_settings_typed_reads_survive_a_process_boundary():
    """
    The bug this module exists to prevent.

    Must stay cross-process: QSettings caches in memory, so an in-process
    read hands back the Python object that was written and passes even with
    ``type=`` removed. Only a fresh interpreter reading the file exercises
    the conversion.
    """
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    store = S.AppSettings.for_file(path)
    store.set_auto_trace(False)
    store.set_tab(3)
    store.sync()

    out = subprocess.check_output([sys.executable, '-c', (
        'from PyQt5 import QtCore\n'
        'from PyXFocus.gui import settings as S\n'
        'st = S.AppSettings.for_file(%r)\n'
        'raw = QtCore.QSettings(%r, QtCore.QSettings.IniFormat)'
        '.value("ui/auto_trace")\n'
        'print(repr(st.auto_trace()), repr(st.tab()), repr(raw))\n'
    ) % (path, path)]).decode()

    auto, tab, raw = out.split()
    assert auto == 'False', 'typed bool came back as %s' % auto
    assert tab == '3', 'typed int came back as %s' % tab
    # The tripwire: if a future Qt starts returning a real bool here, this
    # tells you the type= guard is redundant rather than leaving a mystery.
    assert raw == "'false'", (
        'untyped read returned %s -- if this is now a real bool, the '
        'type= guard may be relaxed' % raw)


def test_settings_recent_files_cap_dedupe_and_case():
    """Newest first, capped, and case-folded where the filesystem ignores case."""
    store = _store()
    for i in range(12):
        store.remember_recent('/tmp/f%d.json' % i)
    recent = store.recent()
    assert len(recent) == S.MAX_RECENT
    assert recent[0].endswith('f11.json'), recent[0]

    store.remember_recent('/tmp/F5.json')
    store.remember_recent('/tmp/f5.json')
    hits = [p for p in store.recent() if p.lower().endswith('f5.json')]
    if sys.platform in ('darwin', 'win32'):
        assert len(hits) == 1, 'case-insensitive filesystem should dedupe: %s' % hits


def test_settings_recent_survives_a_corrupt_value():
    """A mangled preference must not stop the app starting."""
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    for junk in ('not json at all', '[1, 2]', '{}'):
        raw = QtCore.QSettings(path, QtCore.QSettings.IniFormat)
        raw.setValue(S.AppSettings.RECENT, junk)
        raw.sync()
        assert S.AppSettings.for_file(path).recent() == [], junk


def test_settings_session_round_trip_including_unseeded():
    """
    All fifteen fields, and seed=None in particular.

    seed=None means "do not seed" and is a different trace from seeding
    with zero. QSettings has no null, which is why the session is stored as
    one JSON string rather than fifteen keys.
    """
    store = _store()
    original = WolterParams(r0=300., offaxis=2.5, sec_dy=0.3,
                            num_rays=12345, seed=None)
    store.set_session_params(original)
    back = store.session_params()
    for name in PARAM_FIELDS:
        assert getattr(original, name) == getattr(back, name), name
    assert back.seed is None


def test_settings_session_uses_one_serializer():
    """The stored value is exactly config's parameters block."""
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    store = S.AppSettings.for_file(path)
    params = WolterParams(r0=275., sec_rx=1.5)
    store.set_session_params(params)
    store.sync()
    raw = QtCore.QSettings(path, QtCore.QSettings.IniFormat).value(
        S.AppSettings.PARAMETERS, '', type=str)
    assert json.loads(raw) == config.config_payload(params)['parameters']


def test_settings_session_degrades_like_a_file():
    """A stale session loses fields with notes, rather than the launch."""
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    raw = QtCore.QSettings(path, QtCore.QSettings.IniFormat)
    raw.setValue(S.AppSettings.PARAMETERS,
                 '{"r0": 300.0, "coating": "Ir", "psi": "banana"}')
    raw.sync()
    problems = []
    params = S.AppSettings.for_file(path).session_params(problems)
    assert params.r0 == 300.
    assert params.psi == WolterParams().psi
    assert not hasattr(params, 'coating')
    assert problems


def test_settings_session_refuses_a_future_version():
    """A session written by a newer build starts from defaults, with a note."""
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    raw = QtCore.QSettings(path, QtCore.QSettings.IniFormat)
    raw.setValue(S.AppSettings.PARAMETERS, json.dumps(WolterParams().to_dict()))
    raw.setValue(S.AppSettings.VERSION, config.VERSION + 1)
    raw.sync()
    problems = []
    assert S.AppSettings.for_file(path).session_params(problems) is None
    assert any('newer' in note for note in problems)


def test_settings_reset_forgets_everything():
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    store = S.AppSettings.for_file(path)
    store.set_tab(3)
    store.set_auto_trace(False)
    store.reset()
    fresh = S.AppSettings.for_file(path)
    assert fresh.tab() == 0 and fresh.auto_trace() is True


def test_settings_take_reset_flag():
    """The flag is consumed; Qt's own flags are left alone."""
    argv, wanted = S.take_reset_flag(['app', S.RESET_FLAG, '-platform', 'x'])
    assert wanted is True and argv == ['app', '-platform', 'x']
    argv, wanted = S.take_reset_flag(['app', '-style', 'fusion'])
    assert wanted is False and argv == ['app', '-style', 'fusion']


def test_settings_key_strings_are_unique():
    """Cheap guard against a copy-paste that aliases two settings."""
    keys = [getattr(S.AppSettings, n) for n in dir(S.AppSettings)
            if n.isupper() and isinstance(getattr(S.AppSettings, n), str)]
    assert len(keys) == len(set(keys)), 'duplicate key string: %s' % keys
    assert all('/' in key for key in keys), keys


# -- geometry reachability, pure QRect work --------------------------------

def test_reachable_accepts_an_ordinary_window():
    screen = [QtCore.QRect(0, 0, 1440, 900)]
    assert S.is_reachable(QtCore.QRect(100, 100, 800, 600), screen)


def test_reachable_rejects_a_vanished_monitor():
    screen = [QtCore.QRect(0, 0, 1440, 900)]
    assert not S.is_reachable(QtCore.QRect(3000, 100, 800, 600), screen)


def test_reachable_rejects_a_buried_title_bar():
    """
    The load-bearing clause.

    A window whose body is fully on screen but whose title bar is above the
    usable area cannot be moved or closed with the mouse. Without this test
    the title-bar check looks redundant and gets deleted.
    """
    screen = [QtCore.QRect(0, 0, 1440, 900)]
    assert not S.is_reachable(QtCore.QRect(100, 5, 800, 600), screen)


def test_reachable_accepts_a_window_spanning_two_screens():
    screens = [QtCore.QRect(0, 0, 1440, 900), QtCore.QRect(1440, 0, 1440, 900)]
    assert S.is_reachable(QtCore.QRect(1300, 100, 800, 600), screens)


# -- widget-level ----------------------------------------------------------

def test_geometry_blob_round_trips_through_a_widget():
    """The blob must come back as a QByteArray, not a str."""
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    window = QtWidgets.QMainWindow()
    window.resize(900, 700)
    S.AppSettings.for_file(path).save_geometry(window)

    blob = S.AppSettings.for_file(path).splitter_state.__self__._get(
        S.AppSettings.GEOMETRY, QtCore.QByteArray(), QtCore.QByteArray)
    assert hasattr(blob, 'isEmpty') and not blob.isEmpty()
    other = QtWidgets.QMainWindow()
    assert other.restoreGeometry(blob)


def test_recentre_fits_a_smaller_screen():
    """A window saved on a big display must fit the one it reopens on."""
    window = QtWidgets.QMainWindow()
    window.resize(2400, 1400)
    screen = QtCore.QRect(0, 0, 1440, 900)
    S.recentre(window, [screen])
    assert window.width() <= screen.width()
    assert window.height() <= screen.height()


def test_app_restores_a_session_and_traces_exactly_once():
    """
    The only test that catches the double trace.

    set_params emits `changed`, which arms the 250 ms debounce; the
    deferred singleShot then adds a second. Both have to be suppressed.
    """
    import PyXFocus.gui.app as app_module

    traces = []
    original = app_module.MainWindow.run_trace

    def counted(self):
        traces.append(1)
        return original(self)

    app_module.MainWindow.run_trace = counted
    try:
        path = os.path.join(tempfile.mkdtemp(), 's.ini')
        first = app_module.MainWindow(S.AppSettings.for_file(path))
        _settle()
        # Silence the first window before touching its fields: set_params
        # emits `changed`, which arms its 250 ms debounce, and that timer
        # would fire while the second window is settling and be counted
        # against it.
        first.auto_box.setChecked(False)
        first.panel.set_params(WolterParams(r0=444., offaxis=3., seed=99))
        first.tabs.setCurrentIndex(2)
        first.save_settings()
        first._timer.stop()

        del traces[:]
        second = app_module.MainWindow(S.AppSettings.for_file(path))
        assert not second._timer.isActive(), 'restore left the debounce armed'
        _settle()

        params = second.panel.params()
        assert len(traces) == 1, 'launch scheduled %d traces' % len(traces)
        assert params.r0 == 444. and params.offaxis == 3. and params.seed == 99
        assert second.auto_box.isChecked() is False
        assert second.tabs.currentIndex() == 2
    finally:
        app_module.MainWindow.run_trace = original


def test_repeated_traces_do_not_use_a_deleted_worker():
    """
    Regression: workers are deleteLater-ed, so the reference must be dropped.

    Otherwise the isRunning() guard in run_trace touches a destroyed C++
    object and raises RuntimeError on the second trace -- once the event
    loop has actually processed the deletion, which is why this settles
    between traces rather than tracing back to back.
    """
    import PyXFocus.gui.app as app_module
    path = os.path.join(tempfile.mkdtemp(), 's.ini')
    window = app_module.MainWindow(S.AppSettings.for_file(path))
    for _ in range(3):
        _settle()
        if window._worker is not None:
            window._worker.wait(30000)
        _settle()
        window.run_trace()          # must not raise
    _settle()


#: Containers built by _plot_tabs, kept alive for the run. A PlotTabs with
#: no parent is collected as soon as the expression that made it ends,
#: which destroys its C++ children -- so `_plot_tabs().tab('spot')` hands
#: back an already-deleted widget.
_KEEP_ALIVE = []


def _plot_tabs():
    from PyXFocus.gui.tabs import PlotTabs
    tabs = PlotTabs(lambda: WolterParams())
    _KEEP_ALIVE.append(tabs)
    return tabs


def test_every_tab_in_the_registry_satisfies_the_contract():
    """Keys and titles unique, order preserved, every tab a QWidget."""
    from PyXFocus.gui.tabs import TABS
    keys = [spec.key for spec in TABS]
    titles = [spec.title for spec in TABS]
    assert len(keys) == len(set(keys)), keys
    assert len(titles) == len(set(titles)), titles

    tabs = _plot_tabs()
    assert tabs.count() == len(TABS)
    for index, spec in enumerate(TABS):
        assert tabs.tabText(index) == spec.title
        assert isinstance(tabs.tab(spec.key), QtWidgets.QWidget)


def test_the_sweep_tab_is_not_fed_traces():
    """
    The documented opt-out, guarded against a well-meaning future stub.

    The sweep tab runs its own computation on its own worker; a no-op
    set_result would be a lie the container cannot see through.
    """
    assert not hasattr(_plot_tabs().tab('sweep'), 'set_result')


def test_registry_order_is_append_only():
    """
    The active tab is persisted as an integer INDEX, in two places --
    AppSettings.TAB and a saved configuration's ui.tab -- so inserting a
    tab in the middle silently reopens everyone's session on a different
    tab. New tabs go on the end until that is stored by key instead.
    """
    from PyXFocus.gui.tabs import TABS
    assert [spec.key for spec in TABS][:4] == ['spot', 'layout', 'energy',
                                               'sweep']


def test_clear_results_does_not_remove_the_tabs():
    """Guards against anyone renaming clear_results back to clear()."""
    from PyXFocus.gui.tabs import TABS
    tabs = _plot_tabs()
    tabs.clear_results()
    assert tabs.count() == len(TABS)


def test_plot_tabs_do_not_paint_until_flushed():
    """A trace must not repaint tabs nobody is looking at."""
    tabs = _plot_tabs()
    from PyXFocus.gui.wolter import trace
    tabs.set_result(trace(WolterParams(num_rays=500)))
    panes = [tabs.tab(k) for k in ('spot', 'layout', 'energy')]
    assert [p.paints for p in panes] == [0, 0, 0]


def test_the_spot_tab_plots_the_arcsecond_spot():
    """Not a second source of truth: the tab plots what wolter computed."""
    from PyXFocus.gui.wolter import trace
    result = trace(WolterParams(offaxis=2., num_rays=500))
    tab = _plot_tabs().tab('spot')
    tab.set_result(result)
    tab.flush()
    drawn = np.asarray(tab.ax.collections[0].get_offsets())
    x, y = result.spot_arcsec
    assert np.allclose(drawn, np.column_stack([x, y]))


def test_the_energy_tab_plots_what_wolter_computed():
    from PyXFocus.gui import wolter as W
    result = W.trace(WolterParams(offaxis=2., num_rays=500))
    tab = _plot_tabs().tab('energy')
    tab.set_result(result)
    tab.flush()
    rad, frac = W.encircled_energy(result)
    drawn = tab.ax.lines[0].get_xydata()
    assert np.allclose(drawn, np.column_stack([rad, frac]))


def test_the_layout_tab_clears_its_inset():
    """
    One drawing entry point, so the inset reference cannot go stale.

    matplotlib 3.3.2 does drop child axes on ax.clear(), so this is not
    fixing a visible bug -- it stops _inset pointing at a removed axes,
    which the next redraw would try to remove a second time.
    """
    from PyXFocus.gui.wolter import trace
    tab = _plot_tabs().tab('layout')
    tab.set_result(trace(WolterParams(num_rays=500)))
    tab.flush()
    assert tab._inset is not None
    tab.set_result(None)
    tab.flush()
    assert tab._inset is None
    assert tab.ax.lines == []


class _Probe(object):
    """A minimal FigurePane subclass that records what it was asked to draw."""

    def __new__(cls, *args, **kwargs):
        from PyXFocus.gui.tabs.pane import FigurePane

        class Probe(FigurePane):
            def __init__(self):
                FigurePane.__init__(self)
                self.drawn = []

            def _draw(self, result):
                self.drawn.append(result)

        return Probe()


def test_a_hidden_pane_does_not_paint():
    """
    The point of the whole class.

    Every trace used to repaint all three plot tabs, including the two
    nobody was looking at, and the spot tab scatters up to 500 000
    alpha-blended points to do it.
    """
    pane = _Probe()
    first, second, third = object(), object(), object()
    for result in (first, second, third):
        pane.set_result(result)
    assert pane.paints == 0, 'a hidden pane painted %d times' % pane.paints
    assert pane.result() is third


def test_first_flush_paints_the_newest_result():
    """A tab shown after several traces shows the latest, not the first."""
    pane = _Probe()
    first, third = object(), object()
    pane.set_result(first)
    pane.set_result(object())
    pane.set_result(third)
    pane.flush()
    assert pane.paints == 1
    assert pane.drawn == [third], pane.drawn


def test_flush_is_idempotent():
    """Re-flushing without a new result repaints nothing."""
    pane = _Probe()
    pane.set_result(object())
    pane.flush()
    pane.flush()
    assert pane.paints == 1


def test_flush_paints_while_hidden():
    """
    flush() must not re-check visibility.

    show() segfaults under the offscreen platform on this machine, so if
    flush gated on visibility every content assertion in this suite would
    silently become a no-op.
    """
    pane = _Probe()
    assert not pane.isVisible()
    pane.set_result(object())
    pane.flush()
    assert pane.paints == 1


def test_clearing_a_pane_is_a_paint():
    """
    set_result(None) blanks, and does so even if it happened while hidden.

    Otherwise a tab hidden during a failed trace would show the previous
    result when next opened.
    """
    pane = _Probe()
    result = object()
    pane.set_result(result)
    pane.flush()
    pane.set_result(None)          # while hidden
    pane.flush()
    assert pane.paints == 2
    assert pane.drawn == [result, None], pane.drawn


def _settle(rounds=80):
    """Run the event loop long enough for queued work to complete."""
    for _ in range(rounds):
        APP.processEvents()
    if APP is not None:
        APP.processEvents()


def main():
    global APP
    APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            check(name, fn)

    # Nothing here may write the developer's real preferences.
    assert os.path.exists(_REAL_STORE) == _REAL_STORE_EXISTED, (
        'a test wrote the real settings file %s' % _REAL_STORE)

    failures = [(n, tb) for n, ok, tb in RESULTS if not ok]
    print('\n%d passed, %d failed' % (len(RESULTS) - len(failures), len(failures)))
    for name, tb in failures:
        print('\n--- %s ---\n%s' % (name, tb))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
