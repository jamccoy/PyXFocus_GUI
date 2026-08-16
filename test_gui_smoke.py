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

#: Force the matplotlib 3D renderer for the whole suite, before anything
#: imports the tab package.  A GLViewWidget under QT_QPA_PLATFORM=offscreen
#: reports "Failed to create context" and then draws nothing, so every
#: content assertion against the 3D tab would pass vacuously on a machine
#: with pyqtgraph installed and fail loudly on one without.  The choice
#: itself is tested by test_backend_selection_is_a_pure_function, which
#: needs no context because it is a function of two arguments.
os.environ['PYXFOCUS_3D_BACKEND'] = 'matplotlib'

from PyQt5 import QtCore, QtWidgets

from PyXFocus.gui import config
from PyXFocus.gui import scene3d
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


def test_show_script_actually_runs():
    """
    The "Show script" text really does reproduce the trace it claims to.

    Nothing used to check this, and the script was a second hand-maintained
    transcription of the pipeline -- the obvious thing to quietly hand a user
    a script that is not their design. Covered across every optional part,
    because that is exactly where a generated script can go wrong.
    """
    from PyXFocus.gui.wolter import script_for, trace

    designs = [
        ('plain', WolterParams(offaxis=2., azimuth=30., num_rays=2000,
                               seed=17)),
        ('misaligned', WolterParams(num_rays=2000, seed=3, sec_dy=.2,
                                    sec_rx=.7)),
        ('nested', WolterParams(num_rays=3000, seed=5, num_shells=4,
                                shell_gap=2.)),
        ('grating', WolterParams(num_rays=2000, seed=7, use_grating=1,
                                 grating_order=1, wavelength=2.5)),
        ('detector', WolterParams(num_rays=2000, seed=11, use_detector=1,
                                  det_z=6., det_tilt=2.)),
        ('multi-order', WolterParams(num_rays=2000, seed=7, use_grating=1,
                                     grating_order=1, grating_order_span=2)),
        ('radial', WolterParams(num_rays=2000, seed=7, use_grating=1,
                                grating_type=1)),
        ('radial multi-order', WolterParams(num_rays=2000, seed=7,
                                            use_grating=1, grating_type=1,
                                            grating_order_span=2)),
        ('radial off-hub', WolterParams(num_rays=2000, seed=7, use_grating=1,
                                        grating_type=1, grating_hub=2000.)),
        ('everything', WolterParams(num_rays=3000, seed=13, num_shells=3,
                                    offaxis=1., use_grating=1,
                                    use_detector=1, det_z=3.)),
    ]

    env = dict(os.environ)
    env['PYTHONPATH'] = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))

    for name, p in designs:
        out = subprocess.check_output(
            [sys.executable, '-c', script_for(p)], env=env)
        printed = {}
        for line in out.decode('utf-8').strip().split('\n'):
            key, _, value = line.partition(':')
            printed[key.strip()] = value.strip()

        result = trace(p)
        assert int(printed['rays surviving']) == result.num_surviving, (
            '%s: the script vignettes differently, %s vs %d'
            % (name, printed['rays surviving'], result.num_surviving))
        assert np.isclose(float(printed['HPD [arcsec]']), result.hpd_arcsec,
                          rtol=1e-9), (
            '%s: the script no longer reproduces the trace, %s vs %r'
            % (name, printed['HPD [arcsec]'], result.hpd_arcsec))


def test_registry_order_is_append_only():
    """
    The active tab is persisted as an integer INDEX, in two places --
    AppSettings.TAB and a saved configuration's ui.tab -- so inserting a
    tab in the middle silently reopens everyone's session on a different
    tab. New tabs go on the end until that is stored by key instead.
    """
    from PyXFocus.gui.tabs import TABS
    assert [spec.key for spec in TABS][:5] == ['spot', 'layout', 'energy',
                                               'sweep', 'layout3d']


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
    panes = [tabs.tab(k) for k in ('spot', 'layout', 'energy', 'layout3d')]
    assert [p.paints for p in panes] == [0, 0, 0, 0]


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


def _drawn_3d(**kwargs):
    """A flushed 3D tab and the result it drew."""
    from PyXFocus.gui.wolter import trace
    result = trace(WolterParams(num_rays=kwargs.pop('num_rays', 500),
                                **kwargs))
    tab = _plot_tabs().tab('layout3d')
    tab.set_result(result)
    tab.flush()
    return tab, result


def test_the_3d_tab_draws_rays_as_one_collection():
    """
    Every ray, one artist.

    mplot3d re-projects the whole scene per artist on every mouse move, so
    this is a frame-rate contract rather than tidiness.

    The scene hands over segments as vertex *pairs* -- which is what GL's
    'lines' mode consumes, and what lets a ray be coloured by the order it
    was diffracted into -- so the count here is one per (stage gap, ray),
    not one per ray.
    """
    from PyXFocus.gui.tabs.layout3d import RAYS_GID
    tab, result = _drawn_3d(offaxis=2.)
    rays = [c for c in tab.ax.collections if c.get_gid() == RAYS_GID]
    assert len(rays) == 1, 'expected exactly one ray collection, got %d' % len(rays)
    stages, drawn = result.path_x.shape
    assert len(rays[0]._segments3d) == (stages - 1) * drawn, (
        'drew %d segments for %d paths of %d stages'
        % (len(rays[0]._segments3d), drawn, stages))


def test_backend_selection_is_a_pure_function():
    """
    Which 3D renderer gets built, decided without building one.

    A GL context is the one thing this environment cannot provide, so the
    choice is separated from the construction and only the choice is tested
    here.  The garbage case matters: a typo in an environment variable must
    cost you the GPU, not the tab.
    """
    from PyXFocus.gui.tabs import select_backend
    cases = {
        ('auto', True): 'opengl',
        ('auto', False): 'matplotlib',
        ('opengl', True): 'opengl',
        ('opengl', False): 'matplotlib',      # asked for, not available
        ('matplotlib', True): 'matplotlib',   # available, not wanted
        ('matplotlib', False): 'matplotlib',
        ('openGL', True): 'opengl',           # typo falls back to auto
        ('', False): 'matplotlib',
    }
    for (requested, available), expected in cases.items():
        got = select_backend(requested, available)
        assert got == expected, (
            'select_backend(%r, %r) gave %r, expected %r'
            % (requested, available, got, expected))


def test_the_3d_tab_is_built_for_the_selected_backend():
    """
    The registry builds whichever renderer was chosen, under one key.

    TABS is append only -- the active tab is persisted as an integer index
    -- so the 3D renderer swap has to happen behind the existing 'layout3d'
    entry rather than as a sixth tab.
    """
    from PyXFocus.gui import tabs as T

    assert [spec.key for spec in T.TABS].index('layout3d') == 4, (
        'the 3D tab moved; a persisted tab index now reopens elsewhere')

    saved = os.environ.get(T.BACKEND_VAR)
    try:
        os.environ[T.BACKEND_VAR] = 'matplotlib'
        assert T.layout3d_class() is T.layout3d.Layout3DTab

        os.environ[T.BACKEND_VAR] = 'opengl'
        chosen = T.layout3d_class()
        if T.opengl_available():
            from PyXFocus.gui.tabs import layout3dgl
            assert chosen is layout3dgl.GLLayout3DTab
        else:
            # Not a skip: without pyqtgraph the promise is that the tab is
            # still there, drawn by matplotlib.
            assert chosen is T.layout3d.Layout3DTab
    finally:
        if saved is None:
            del os.environ[T.BACKEND_VAR]
        else:
            os.environ[T.BACKEND_VAR] = saved


def test_the_gl_camera_frames_the_whole_scene():
    """
    The default view fits the box, from every preset and any window shape.

    Both halves of this have been wrong: pyqtgraph's fov is the *horizontal*
    one, so framing on it leaves a tall scene running off the top; and the
    box is 2.2 times taller than wide, so a distance that suits the side
    view is far too close looking down the axis.  A default view that has to
    be rescued with the scroll wheel is the complaint this renderer exists
    to answer, so it is asserted rather than eyeballed.

    Needs pyqtgraph importable, but no GL context: fit_distance is
    arithmetic.
    """
    from PyXFocus.gui import tabs as T
    if not T.opengl_available():
        return
    import numpy as np
    from PyXFocus.gui.tabs import layout3dgl as G

    corners = G._corners(G.SCENE_BOX)
    for width, height in ((1200, 700), (700, 1200), (900, 900)):
        for name, angles in G.CAMERA_PRESETS.items():
            distance = G.fit_distance(width, height, **angles)
            elev = np.radians(angles['elevation'])
            azim = np.radians(angles['azimuth'])
            right = np.array([-np.sin(azim), np.cos(azim), 0.])
            up = np.array([-np.sin(elev) * np.cos(azim),
                           -np.sin(elev) * np.sin(azim), np.cos(elev)])
            half_w = np.tan(np.radians(G.FOV / 2.)) * distance
            half_h = half_w * height / float(width)
            assert np.abs(corners.dot(right)).max() <= half_w, (
                '%s overflows a %dx%d window sideways' % (name, width, height))
            assert np.abs(corners.dot(up)).max() <= half_h, (
                '%s overflows a %dx%d window vertically'
                % (name, width, height))

    # A degenerate viewport must still give a usable number, because the
    # camera is framed once at construction, before any layout has happened.
    assert G.fit_distance(0, 0) > 0.


def test_zooming_holds_the_point_under_the_cursor():
    """
    The defining CAD behaviour, asserted as arithmetic.

    pyqtgraph scales distance about the view centre, so zooming in on
    anything off-centre pushes it out of frame and you chase it. Holding the
    cursor point fixed is what makes deep zoom usable at all -- and it is
    pure geometry, so it needs no GL context to check.
    """
    from PyXFocus.gui import tabs as T
    if not T.opengl_available():
        return
    import numpy as np
    from PyXFocus.gui.tabs import layout3dgl as G

    width, height = 1200, 700
    center = np.array([3., -2., 40.])
    for elev, azim in ((22., -60.), (89.9, -90.), (0., 0.)):
        right, up = G.camera_basis(elev, azim)
        for at in ((900., 200.), (100., 650.), (600., 350.)):
            for before, after in ((1000., 500.), (500., 1000.), (900., 1.)):
                cursor = G.world_at(center, elev, azim, before, width, height,
                                    at[0], at[1])
                moved = G.zoom_about(center, cursor, before, after)
                half_w, half_h = G.half_extents(after, width, height)
                offset = cursor - moved
                screen_x = (offset.dot(right) / half_w + 1.) * width / 2.
                screen_y = (1. - offset.dot(up) / half_h) * height / 2.
                assert abs(screen_x - at[0]) < 1e-6, (
                    'cursor drifted in x: %r -> %r' % (at[0], screen_x))
                assert abs(screen_y - at[1]) < 1e-6, (
                    'cursor drifted in y: %r -> %r' % (at[1], screen_y))


def test_the_zoom_is_not_clamped_short_of_the_detail():
    """
    The floor is a guard against degenerate arithmetic, nothing more.

    It was once 0.05 * DEFAULT_DISTANCE, on the mistaken grounds that a fast
    scroll could drive the distance through zero -- pyqtgraph zooms
    multiplicatively, so it cannot. What it did do was stop the view 51 mm
    across: barely wider than the 30 mm diffraction fan, and thousands of
    times too coarse to look into a single order, whose spot is about four
    microns across.
    """
    from PyXFocus.gui import tabs as T
    if not T.opengl_available():
        return
    from PyXFocus.gui.tabs import layout3dgl as G
    assert G.MIN_DISTANCE < 1e-4 * G.DEFAULT_DISTANCE, (
        'the zoom floor is back to being a usability limit: %r' % G.MIN_DISTANCE)
    assert G.MIN_DISTANCE > 0., 'a zero distance makes the projection singular'


def test_the_scale_readout_names_the_right_unit():
    """
    A readout wrong by a factor of a thousand is worse than no readout.

    The first version of this table scaled a sub-millimetre width by 1e3 and
    still labelled it "mm", so a view 1.77 microns across called itself
    1.77 mm -- in the one control whose entire job is to say how big what
    you are looking at is.
    """
    from PyXFocus.gui import tabs as T
    if not T.opengl_available():
        return
    from PyXFocus.gui.tabs.layout3dgl import GLLayout3DTab as Tab
    cases = [(8400., '8.4 m'), (235., '235 mm'), (1., '1 mm'),
             (0.74, '740 &micro;m'), (0.00177, '1.77 &micro;m'),
             (6.1e-5, '61 nm')]
    for mm, expected in cases:
        got = Tab.format_mm(mm)
        assert got == expected, '%g mm formatted as %r, wanted %r' % (
            mm, got, expected)
    assert Tab.format_mm(None) == ''
    assert Tab.format_mm(float('nan')) == ''


def test_a_zoom_box_frames_what_was_dragged():
    """
    A box a fifth of the window across zooms in five times, not four or six.

    Both dimensions matter: fitting the width alone would spill a tall box
    off the top and bottom, which is the same horizontal-fov trap that once
    made the default view overflow.
    """
    from PyXFocus.gui import tabs as T
    if not T.opengl_available():
        return
    from PyXFocus.gui.tabs import layout3dgl as G

    width, height = 1200, 700
    cases = [
        ((240, 70), 0.2),      # a fifth wide, a tenth tall: width decides
        ((120, 175), 0.25),    # a tenth wide, a quarter tall: height decides
        ((1200, 700), 1.0),    # the whole window changes nothing
        ((600, 350), 0.5),     # half of each
    ]
    for (rect_w, rect_h), expected in cases:
        got = G.rect_fraction(rect_w, rect_h, width, height)
        assert abs(got - expected) < 1e-9, (
            'a %dx%d box in a %dx%d view gave %r, wanted %r'
            % (rect_w, rect_h, width, height, got, expected))

    # A viewport with no size must not divide by zero on the way to a crash.
    assert G.rect_fraction(10, 10, 0, 0) == 1.


def test_the_3d_tab_blanks_on_none():
    """A cleared tab keeps nothing, labels included."""
    tab, _ = _drawn_3d()
    assert tab.ax.collections or tab.ax.lines
    tab.set_result(None)
    tab.flush()
    # len(), not == [] : matplotlib 3.7 made these immutable ArtistList
    # views rather than plain lists, and an ArtistList never compares equal
    # to a list. len() reads the same on 3.3 and on 3.8.
    assert len(tab.ax.collections) == 0
    assert len(tab.ax.lines) == 0
    assert tab.ax.get_title() == ''


def test_the_3d_tab_keeps_x_and_y_to_the_same_scale():
    """
    Azimuth, tilt and decentre all live in the x-y plane.

    Only z may be compressed; squashing x against y would destroy the very
    thing this view exists to show.
    """
    tab, _ = _drawn_3d(offaxis=2.)
    assert np.allclose(tab.ax.get_xlim(), tab.ax.get_ylim()), (
        'x and y are not to the same scale: %r vs %r'
        % (tab.ax.get_xlim(), tab.ax.get_ylim()))


def test_mirrors_only_zooms_and_repaints():
    """
    A view control repaints, though the result has not changed.

    FigurePane's identity gate is right about results and knows nothing
    about views, which is what force_repaint exists to say.
    """
    tab, result = _drawn_3d()
    before = tab.ax.get_zlim()
    painted = tab.paints

    tab.mirrors_only.setChecked(True)
    assert tab.paints == painted + 1, 'toggling a view control did not repaint'

    after = tab.ax.get_zlim()
    assert (after[1] - after[0]) < (before[1] - before[0]), (
        'mirrors-only did not narrow the z range: %r -> %r' % (before, after))
    from PyXFocus.gui.wolter import mirror_z_range
    zlo, zhi = mirror_z_range(result.params)
    assert after[0] <= zlo and after[1] >= zhi, (
        'mirrors-only clipped the optics: %r excludes %r' % (after, (zlo, zhi)))


def test_true_scale_repaints_and_undoes_the_compression():
    """
    The toggle reaches the picture, not just the options object.

    Checked on the matplotlib tab because that one draws without a GL
    context; both tabs build the same scene, so the scene-level behaviour is
    covered once in test_smoke and the wiring is covered here.
    """
    tab, _ = _drawn_3d()
    painted = tab.paints
    before = tab.ax.get_box_aspect() if hasattr(tab.ax, 'get_box_aspect') \
        else None

    tab.true_scale.setChecked(True)
    assert tab.paints == painted + 1, 'the 1:1 toggle did not repaint'
    assert tab.options().true_scale is True

    if before is not None:
        after = tab.ax.get_box_aspect()
        # The *ratio*, not the components: matplotlib normalises the aspect
        # tuple, so the absolute numbers shrink as the box deepens and only
        # z-against-x carries meaning.
        assert abs(before[2] / before[0] - scene3d.Z_BOX) < 1e-6, (
            'the compressed aspect is no longer Z_BOX: %r' % (before,))
        assert after[2] / after[0] > 5 * before[2] / before[0], (
            'the drawn z aspect did not follow the scale: %r -> %r'
            % (before, after))
        assert after[0] == after[1], 'x and y stopped being equal'


def test_the_3d_tab_survives_the_solid_toggle():
    """Solid half-shells draw, and leave the rays visible."""
    from PyXFocus.gui.tabs.layout3d import RAYS_GID
    tab, _ = _drawn_3d()
    tab.solid.setChecked(True)
    rays = [c for c in tab.ax.collections if c.get_gid() == RAYS_GID]
    assert len(rays) == 1, 'rays vanished when surfaces went solid'
    assert len(tab.ax.collections) > 1, 'no surfaces were drawn'


def test_the_3d_tab_draws_a_misaligned_secondary_where_it_is():
    """
    The payoff: a tilt that the 2D profile cannot show at all.

    mirror_profile returns the nominal prescription regardless of
    misalignment, so the profile view is identical either way. The 3D view
    goes through Element.patches, which honours the placement.
    """
    from PyXFocus.gui.wolter import WolterParams as P, mirror_profile
    from PyXFocus.gui.wolter import WolterSecondary

    aligned, tilted = P(), P(sec_rx=5.)
    (_, ra), _ = mirror_profile(aligned)
    (_, rt), _ = mirror_profile(tilted)
    assert np.array_equal(ra, rt), 'premise changed: profiles now differ'

    flat, = WolterSecondary(aligned).patches(n_azimuth=24, num=8)
    bent, = WolterSecondary(tilted).patches(n_azimuth=24, num=8)
    assert not np.allclose(flat.z, bent.z), (
        'the 3D geometry ignored the tilt too')


def test_optional_groups_drive_their_flag():
    """
    A checkable group box is the on/off switch for that part of the system.

    The flag has no spin box of its own, so if the checkbox does not write
    it, an unticked Grating group would still trace with a grating.
    """
    from PyXFocus.gui.app import ParameterPanel
    panel = ParameterPanel()
    _KEEP_ALIVE.append(panel)

    assert panel.params().use_grating == 0, 'a grating is fitted by default'
    panel._enables['use_grating'].setChecked(True)
    assert panel.params().use_grating == 1, 'ticking the group did nothing'
    panel._enables['use_grating'].setChecked(False)
    assert panel.params().use_grating == 0, 'unticking the group did nothing'


def test_optional_groups_follow_a_loaded_design():
    """
    Loading a design ticks its groups, and emits `changed` exactly once.

    A listener that reads params() mid-load would otherwise see a grating
    whose parameters have not landed yet.
    """
    from PyXFocus.gui.app import ParameterPanel
    panel = ParameterPanel()
    _KEEP_ALIVE.append(panel)

    seen = []
    panel.changed.connect(lambda: seen.append(panel.params().use_detector))
    panel.set_params(WolterParams(use_detector=1, det_z=4.))

    assert panel._enables['use_detector'].isChecked(), (
        'a loaded detector left its group unticked')
    assert panel.params().det_z == 4.
    assert seen == [1], 'expected exactly one settled change, got %r' % seen


def test_the_3d_tab_draws_every_shell():
    """
    A nest draws without the tab learning anything about nesting.

    The whole point of the geometry contract: the tab asks the scene for
    items and never counts shells.  The vertex budget that keeps a
    twenty-shell design rotatable is asserted in test_smoke, against the
    scene rather than against the artists.
    """
    from PyXFocus.gui.tabs.layout3d import RAYS_GID

    tab_one, _ = _drawn_3d(num_shells=1)
    surfaces_one = len([c for c in tab_one.ax.collections
                        if c.get_gid() != RAYS_GID])

    tab_many, _ = _drawn_3d(num_shells=6)
    surfaces_many = len([c for c in tab_many.ax.collections
                         if c.get_gid() != RAYS_GID])
    assert surfaces_many > surfaces_one, (
        'six shells drew %d surfaces, one shell drew %d'
        % (surfaces_many, surfaces_one))


def test_settings_session_migrates_an_older_version():
    """
    A remembered session gets the same migrations a file does.

    The version was already written and range-checked on read, but nothing
    ever acted on it -- so a session from before a field existed degraded
    with notes where a migration had the real answer.
    """
    store = _store()
    fields = dict(WolterParams().to_dict())
    del fields['num_shells']
    del fields['shell_gap']
    store._settings.setValue(store.PARAMETERS, json.dumps(fields))
    store._settings.setValue(store.VERSION, 1)

    problems = []
    params = store.session_params(problems)
    assert params is not None, 'an older session must still load'
    assert params.num_shells == 1, params.num_shells
    assert params.shell_gap == 1., params.shell_gap
    assert any('predates nested shells' in p for p in problems), problems


def test_the_layout_tab_draws_a_pair_of_mirrors_per_shell():
    """The 2D view keeps up with the nest it is given."""
    from PyXFocus.gui.wolter import trace
    tab = _plot_tabs().tab('layout')
    tab.set_result(trace(WolterParams(num_shells=4, num_rays=800)))
    tab.flush()
    # One ray artist plus two mirror lines per shell, plus the focus line.
    labels = [line.get_label() for line in tab.ax.lines]
    assert labels.count('Primary') == 1, 'a nest should not repeat its legend'
    assert labels.count('Secondary') == 1


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
    assert len(tab.ax.lines) == 0        # ArtistList on matplotlib >= 3.7


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


def test_the_paint_gate_works_without_a_canvas():
    """
    The gate is about results, not about matplotlib.

    The 3D tab renders with OpenGL and has no figure, canvas or toolbar, so
    a gate that reached for one would leave that tab either duplicating the
    dirty flag or doing without it. This is what catches a future edit
    quietly re-coupling the two.
    """
    from PyQt5 import QtWidgets
    from PyXFocus.gui.tabs.pane import PaintGate

    class NoCanvas(PaintGate, QtWidgets.QWidget):
        def __init__(self):
            super(NoCanvas, self).__init__()
            self._init_paint_gate()
            self.drawn = []
            self.presented = 0

        def _draw(self, result):
            self.drawn.append(result)

        def _present(self):
            self.presented += 1

    pane = NoCanvas()
    _KEEP_ALIVE.append(pane)
    first, second = object(), object()
    pane.set_result(first)
    assert pane.paints == 0, 'a hidden pane painted %d times' % pane.paints
    pane.set_result(second)
    pane.flush()
    assert pane.drawn == [second], pane.drawn
    assert pane.presented == 1, 'flush did not present what it drew'

    pane.force_repaint()
    assert pane.drawn == [second, second], pane.drawn
    assert pane.paints == 2


def _settle(rounds=80):
    """Run the event loop long enough for queued work to complete."""
    for _ in range(rounds):
        APP.processEvents()
    if APP is not None:
        APP.processEvents()


# --------------------------------------------------------------------------
# The documentation viewer
# --------------------------------------------------------------------------

class _LinkRecorder(object):
    """Stands in for QDesktopServices.openUrl, so no browser opens."""

    def __init__(self):
        self.opened = []

    def __call__(self, url):
        self.opened.append(url.toString())
        return True


def _docs_window():
    """A viewer that can never reach a real browser. Never shown."""
    from PyXFocus.gui import docview
    recorder = _LinkRecorder()
    return docview.DocsWindow(open_external=recorder), recorder


def test_docs_window_lists_every_page():
    """The contents list is PAGES, and the first page actually loaded."""
    from PyXFocus.gui import docs_index
    window, _ = _docs_window()
    assert window.contents.count() == len(docs_index.PAGES)
    assert window.current_key() == docs_index.PAGES[0].key
    assert window.browser.toPlainText().strip(), 'the first page is blank'


def test_docs_render_as_utf8():
    """
    Non-ASCII survives the trip through QTextBrowser.

    The bug this caught: a generated page with no charset declaration is
    decoded as Latin-1, so every em dash renders as 'â€”'. It is silent --
    nothing raises, the page loads, the text is just wrong -- and these
    pages are full of em dashes, so it disfigured most of a paragraph.
    """
    window, _ = _docs_window()
    text = window.browser.toPlainText()
    assert '—' in text, 'em dashes did not survive: encoding regressed'
    assert 'â' not in text, 'mojibake in the rendered page'


def test_docs_internal_link_navigates_in_place():
    """An internal link changes page and moves the contents selection."""
    window, recorder = _docs_window()
    window._on_anchor(QtCore.QUrl('Installation.html'))
    assert window.current_key() == 'Installation'
    selected = window.contents.currentItem().data(QtCore.Qt.UserRole)
    assert selected == 'Installation', (
        'the contents list did not follow the link, it says %r' % selected)
    assert not recorder.opened, 'an internal link escaped to a browser'


def test_docs_external_link_leaves_the_page_alone():
    """
    An http link opens outside and does NOT navigate the viewer.

    The failure this guards against is setOpenLinks(True): Qt would try to
    load the remote page into the QTextBrowser, which renders as a blank
    document rather than an error.
    """
    window, recorder = _docs_window()
    before = window.current_key()
    window._on_anchor(QtCore.QUrl('https://example.com/thing'))
    assert recorder.opened == ['https://example.com/thing']
    assert window.current_key() == before, 'an external link changed the page'


def test_docs_back_button_tracks_history():
    """Back is dead until there is somewhere to go back to."""
    window, _ = _docs_window()
    assert not window.back_button.isEnabled()
    window.show_page('Installation')
    APP.processEvents()
    assert window.back_button.isEnabled(), (
        'setSource did not record history -- Back can never work')


def test_help_menu_offers_documentation():
    """The menu bar exists, and the help key is wired to it."""
    from PyQt5 import QtGui
    from PyXFocus.gui import app as app_module

    window = app_module.MainWindow(store=_store())
    titles = [a.text() for a in window.menuBar().actions()]
    assert any('Help' in title for title in titles), (
        'no Help menu, found %r' % titles)

    help_menu = window.menuBar().actions()[0].menu()
    actions = [a for a in help_menu.actions() if a.text() == 'Documentation']
    assert actions, 'no Documentation action under Help'
    assert actions[0].shortcut() == QtGui.QKeySequence(
        QtGui.QKeySequence.HelpContents), 'Documentation is not on the help key'


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
