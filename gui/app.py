"""
Wolter-I telescope explorer -- a PyQt5 front end for PyXFocus.

Set up a Wolter-I shell with labelled input fields, press Trace, and see the
spot diagram, the telescope in profile, and the encircled-energy curve, with
HPD and RMS reported in arcseconds.

Launch it with::

    python -m PyXFocus.gui.app

The trace itself lives in :mod:`PyXFocus.gui.wolter` and has no Qt
dependency, so anything you can set up here you can also script.
"""

import math
import sys
import traceback

import numpy as np
from PyQt5 import QtCore, QtWidgets

from PyXFocus.gui import settings
from PyXFocus.gui import wolter
from PyXFocus.gui.tabs import PlotTabs
from PyXFocus.gui.wolter import WolterParams


class TraceWorker(QtCore.QThread):
    """Runs a trace off the UI thread so the window stays responsive."""

    #: Named `traced`, not `finished`: a pyqtSignal called `finished` on a
    #: QThread subclass REPLACES QThread's own finished() lifecycle signal,
    #: which costs you deleteLater wiring and any "wait until the thread has
    #: actually exited" logic. SweepWorker uses `swept` for the same reason.
    traced = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, params, parent=None):
        super(TraceWorker, self).__init__(parent)
        self.params = params

    def run(self):
        try:
            self.traced.emit(wolter.trace(self.params))
        except Exception:
            self.failed.emit(traceback.format_exc())


class ParameterPanel(QtWidgets.QWidget):
    """The input fields, grouped by what they describe."""

    changed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super(ParameterPanel, self).__init__(parent)
        self._spins = {}
        #: Parameters a configuration carries but the panel gives no field
        #: for. Without this a saved seed would silently revert to 0 on
        #: reload, which makes a "reproducible" configuration a lie.
        self._carried = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for title, names in wolter.PARAM_GROUPS:
            layout.addWidget(self._group(title, names))
        layout.addStretch(1)

        grouped = set(n for _, names in wolter.PARAM_GROUPS for n in names)
        for spec in wolter.PARAM_SPECS:
            if spec.name not in grouped:
                self._carried[spec.name] = spec.default

    def _group(self, title, names):
        box = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(box)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        for name in names:
            spec = wolter.param_spec(name)
            spin = QtWidgets.QDoubleSpinBox()
            spin.setDecimals(spec.decimals)
            spin.setRange(spec.lo, spec.hi)
            spin.setSingleStep(spec.step)
            spin.setValue(spec.default)
            spin.setKeyboardTracking(False)
            if spec.unit:
                spin.setSuffix(' ' + spec.unit)
            spin.valueChanged.connect(self.changed)
            self._spins[name] = spin
            form.addRow(spec.label + ':', spin)
        return box

    def params(self):
        """Current field values as a :class:`WolterParams`."""
        p = WolterParams()
        for name, spin in self._spins.items():
            value = spin.value()
            setattr(p, name, int(value) if name == 'num_rays' else value)
        for name, value in self._carried.items():
            setattr(p, name, value)
        return p

    def set_params(self, params):
        """
        Write ``params`` into the fields.

        Returns a list of ``(name, requested, applied)`` for every field
        that would not take its value verbatim. Callers must surface it: a
        spin box clamps in silence, so a load that says nothing claims to
        have loaded a configuration the user never wrote.

        ``changed`` is emitted exactly once, after every field has settled,
        so a listener calling :meth:`params` sees one consistent set rather
        than fifteen half-applied ones.
        """
        adjusted = []
        for spin in self._spins.values():
            spin.blockSignals(True)
        try:
            for name, spin in self._spins.items():
                if not hasattr(params, name):
                    continue
                wanted = float(getattr(params, name))

                if not math.isfinite(wanted):
                    # setValue(nan) silently yields the maximum, which then
                    # reads back as a deliberate extreme. Leave the field.
                    adjusted.append((name, wanted, spin.value()))
                    continue

                if name == 'num_rays':
                    # decimals=0 rounds rather than truncates, and params()
                    # takes int() of whatever lands; round here so the value
                    # reported as applied is the value actually used.
                    wanted = float(int(round(wanted)))

                spin.setValue(wanted)
                applied = spin.value()
                # Half a least-significant digit: anything larger is a clamp
                # or a real loss of precision, not display rounding.
                if abs(applied - wanted) > 0.5 * 10 ** -spin.decimals():
                    adjusted.append((name, wanted, applied))
        finally:
            for spin in self._spins.values():
                spin.blockSignals(False)

        for name in self._carried:
            if hasattr(params, name):
                self._carried[name] = getattr(params, name)

        self.changed.emit()
        return adjusted

    def reset(self):
        """Restore every field to its default."""
        return self.set_params(WolterParams())


class MetricsBar(QtWidgets.QWidget):
    """Read-out strip for the numbers that come out of a trace."""

    #: key, caption, tooltip
    FIELDS = [
        ('hpd', 'HPD', 'Half-power diameter: the angular diameter '
                       'enclosing half the rays'),
        ('rms', 'RMS radius', 'RMS ray distance from the centroid'),
        ('rays', 'Rays surviving', 'Rays through both mirrors, of those '
                                   'launched'),
        ('throughput', 'Throughput', 'Fraction of launched rays surviving '
                                     'vignetting'),
        ('area', 'Collecting area', 'GEOMETRIC aperture times vignetting. '
                                    'Excludes mirror reflectivity, which '
                                    'PyXFocus does not model, so this is an '
                                    'upper bound rather than a true '
                                    'effective area.'),
        ('focus', 'Focus z', 'Axial position of best focus'),
        ('nonconv', 'Non-converged',
         'Rays the Fortran surface solver gave up on. They are NOT '
         'geometric misses. Being excluded from every metric, they make '
         'throughput and collecting area lower bounds rather than values.'),
    ]

    #: Captions greyed by default; recoloured when the metrics are bounds.
    GREY = 'color: gray; font-size: 10px;'
    AMBER = 'color: #b8860b; font-size: 10px; font-weight: bold;'
    VALUE = 'font-size: 15px; font-weight: bold;'
    VALUE_BAD = 'font-size: 15px; font-weight: bold; color: #c0392b;'

    def __init__(self, parent=None):
        super(MetricsBar, self).__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._values = {}
        self._captions = {}
        for key, label, tip in self.FIELDS:
            box = QtWidgets.QVBoxLayout()
            caption = QtWidgets.QLabel(label)
            caption.setStyleSheet(self.GREY)
            value = QtWidgets.QLabel('—')
            value.setStyleSheet(self.VALUE)
            caption.setToolTip(tip)
            value.setToolTip(tip)
            box.addWidget(caption)
            box.addWidget(value)
            layout.addLayout(box)
            self._values[key] = value
            self._captions[key] = label
            self._captions[key + '_widget'] = caption
        layout.addStretch(1)

    def update_metrics(self, result):
        v = self._values
        v['hpd'].setText('%.4f"' % result.hpd_arcsec)
        v['rms'].setText('%.4f"' % result.rms_arcsec)
        v['rays'].setText('%d / %d' % (result.num_surviving,
                                       result.num_launched))
        v['focus'].setText('%.4f mm' % result.focus_z)

        # Rays lost to non-convergence are excluded from the counts above,
        # so say so rather than letting a bound read as a measurement.
        bounds = result.metrics_are_bounds
        prefix = '≥ ' if bounds else ''
        v['throughput'].setText('%s%.1f%%' % (prefix, 100. * result.throughput))
        v['area'].setText('%s%.2f cm²' % (prefix, result.collecting_area))

        lost = result.num_nonconverged
        if lost:
            v['nonconv'].setText('%d (%.1f%%)'
                                 % (lost, 100. * lost
                                    / max(result.num_launched, 1)))
            v['nonconv'].setStyleSheet(self.VALUE_BAD)
        else:
            v['nonconv'].setText('0')
            v['nonconv'].setStyleSheet(self.VALUE)

        for key in ('throughput', 'area'):
            self._captions[key + '_widget'].setStyleSheet(
                self.AMBER if bounds else self.GREY)

    def clear(self):
        for key, value in self._values.items():
            value.setText('—')
            value.setStyleSheet(self.VALUE)
        for key in ('throughput', 'area'):
            self._captions[key + '_widget'].setStyleSheet(self.GREY)


SCRIPT_TEMPLATE = '''"""Equivalent PyXFocus script for the current settings."""
import numpy as np
import PyXFocus.sources as sources
import PyXFocus.surfaces as surf
import PyXFocus.transformations as tran
import PyXFocus.analyses as anal
import PyXFocus.conicsolve as conic

r0, z0 = {r0!r}, {z0!r}
primary_length, secondary_length, psi = {pl!r}, {sl!r}, {psi!r}

# Primary entrance aperture.
rin = conic.primrad(z0, r0, z0, psi=psi)
rout = conic.primrad(z0 + primary_length, r0, z0, psi=psi)

np.random.seed({seed!r})
rays = sources.annulus(rin, rout, {n!r})
tran.transform(rays, 0, 0, -(z0 + primary_length + 500.), 0, 0, 0)

# Off-axis source: {off!r} arcmin at azimuth {az!r} deg.
theta = np.radians({off!r} / 60.)
phi = np.radians({az!r})
if theta:
    n_rays = len(rays[1])
    rays[4] = np.repeat(np.sin(theta) * np.cos(phi), n_rays)
    rays[5] = np.repeat(np.sin(theta) * np.sin(phi), n_rays)
    rays[6] = np.repeat(-np.cos(theta), n_rays)

# Primary.
surf.wolterprimary(rays, r0, z0, psi=psi)
tran.reflect(rays)
ind = np.logical_and(rays[3] > z0, rays[3] < z0 + primary_length)
rays = tran.vignette(rays, ind=ind)

# Secondary, in its misaligned frame.
misalign = ({dx!r}, {dy!r}, {dz!r},
            np.radians({rx!r} / 60.), np.radians({ry!r} / 60.),
            np.radians({rz!r} / 60.))
tran.transform(rays, *misalign)
surf.woltersecondary(rays, r0, z0, psi=psi)
tran.reflect(rays)
tran.itransform(rays, *misalign)
ind = np.logical_and(rays[3] > z0 - secondary_length, rays[3] < z0)
rays = tran.vignette(rays, ind=ind)

# Best focus and performance.
focus_z = surf.focusI(rays)
hpd_arcsec = anal.hpd(rays) / z0 * 180. / np.pi * 3600.
print("rays surviving:", len(rays[1]))
print("HPD [arcsec]:", hpd_arcsec)
'''


class MainWindow(QtWidgets.QMainWindow):

    def __init__(self, store=None):
        super(MainWindow, self).__init__()
        self.setWindowTitle('PyXFocus — Wolter-I Explorer')
        # The pre-restore default; restore_geometry leaves it alone when
        # nothing has been saved yet.
        self.resize(*settings.DEFAULT_SIZE)
        self._worker = None
        self._result = None
        self.config_path = None
        #: Stashed rather than shown -- see _note_restore.
        self._restore_note = ''
        self.settings = store if store is not None else settings.AppSettings()

        self.panel = ParameterPanel()
        self.panel.changed.connect(self._on_changed)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self.panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setMinimumWidth(300)

        self.trace_button = QtWidgets.QPushButton('Trace')
        self.trace_button.setDefault(True)
        self.trace_button.clicked.connect(self.run_trace)
        self.auto_box = QtWidgets.QCheckBox('Auto-trace on change')
        self.auto_box.setChecked(True)
        reset_button = QtWidgets.QPushButton('Reset')
        reset_button.clicked.connect(self.panel.reset)
        script_button = QtWidgets.QPushButton('Show script')
        script_button.setToolTip(
            'Show the plain PyXFocus script that reproduces these settings')
        script_button.clicked.connect(self.show_script)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.trace_button)
        buttons.addWidget(reset_button)
        buttons.addWidget(script_button)

        left = QtWidgets.QWidget()
        left_box = QtWidgets.QVBoxLayout(left)
        left_box.setContentsMargins(8, 8, 4, 8)
        left_box.addWidget(scroll, 1)
        left_box.addWidget(self.auto_box)
        left_box.addLayout(buttons)

        self.metrics = MetricsBar()
        self.tabs = PlotTabs(self.panel.params)

        right = QtWidgets.QWidget()
        right_box = QtWidgets.QVBoxLayout(right)
        right_box.setContentsMargins(4, 8, 8, 8)
        right_box.addWidget(self.metrics)
        right_box.addWidget(self.tabs, 1)

        self.splitter = QtWidgets.QSplitter()
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.setCentralWidget(self.splitter)

        self.statusBar().showMessage('Ready')

        # Coalesce rapid edits into one trace.
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.run_trace)

        # Geometry first: splitter state is absolute pixels, so restoring it
        # into a default-sized window and then resizing would give the user
        # proportions they never chose.
        self.settings.restore_geometry(self)        # before show(); main shows
        self.settings.restore_window_state(self)    # no toolbars until Phase 4
        self._restore_session()

        # set_params emits `changed`, which arms the debounce; belt and braces
        # with the blockSignals in _restore_session. An armed timer plus the
        # singleShot below is two traces on every launch.
        self._timer.stop()

        # One trace, deferred. A bare run_trace() here would trace the
        # defaults, because restore has not happened at that point in
        # __init__ -- and once it has, restore schedules a second trace of
        # the real values. singleShot(0) runs after __init__ returns.
        QtCore.QTimer.singleShot(0, self.run_trace)

    def _restore_session(self):
        """Put back the layout and parameters from the last run."""
        store = self.settings
        problems = []
        adjusted = []

        params = store.session_params(problems)
        if params is not None:
            # Block on the panel rather than the timer: this stops `changed`
            # being emitted at all instead of undoing its effect.
            self.panel.blockSignals(True)
            try:
                adjusted = self.panel.set_params(params)
            finally:
                self.panel.blockSignals(False)

        self.auto_box.setChecked(store.auto_trace())

        blob = store.splitter_state()
        if not blob.isEmpty():
            self.splitter.restoreState(blob)

        tab = store.tab()
        # config._clean_ui deliberately does not bound the tab index -- how
        # many tabs exist is a Qt fact it does not know. This is that bound.
        if 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)

        self.config_path = store.config_path() or None
        self._note_restore(adjusted, problems)

    def _note_restore(self, adjusted, problems):
        """
        Say what the restore could not honour -- status bar, never a modal.

        A dialog here would fire on every launch after a range change, before
        the user has touched anything, offering only OK.

        The note is stashed rather than shown because the deferred trace
        overwrites the status bar with 'Tracing…' within milliseconds, so a
        showMessage() here is technically correct and literally invisible.
        _on_finished appends it to the first trace's line instead.
        """
        notes = ['%s %g → %g' % (name, req, app) for name, req, app in adjusted]
        if not notes and not problems:
            return
        if notes:
            self._restore_note = (
                'restored session: %d value%s adjusted to fit — %s%s'
                % (len(notes), '' if len(notes) == 1 else 's',
                   ', '.join(notes[:3]), ', …' if len(notes) > 3 else ''))
        else:
            self._restore_note = 'restored session: %s' % problems[0]
        # Nothing is lost: the full list is one hover away.
        self.statusBar().setToolTip('\n'.join(notes + problems))

    def save_settings(self):
        """
        Persist layout and session. Idempotent, and never raises.

        Runs on quit, so an exception here would crash on exit and -- once
        Phase 6 adds closeEvent -- leave a window that cannot be closed.
        Phase 6 calls this same method, so the two do not duplicate work.
        """
        try:
            store = self.settings
            store.save_geometry(self)
            store.save_window_state(self)
            store.set_splitter_state(self.splitter.saveState())
            store.set_tab(self.tabs.currentIndex())
            store.set_auto_trace(self.auto_box.isChecked())
            store.set_session_params(self.panel.params())
            store.set_config_path(self.config_path)
            store.sync()
        except Exception:
            traceback.print_exc()

    def _on_changed(self):
        if self.auto_box.isChecked():
            self._timer.start()

    def run_trace(self):
        if self._worker is not None and self._worker.isRunning():
            self._timer.start()
            return
        self.trace_button.setEnabled(False)
        self.statusBar().showMessage('Tracing…')
        worker = TraceWorker(self.panel.params(), self)
        self._worker = worker
        worker.traced.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        # Without deleteLater every trace leaves a QThread QObject parented
        # to the window forever; a session with auto-trace on accumulates
        # hundreds. But dropping the C++ object leaves self._worker dangling,
        # and the isRunning() guard above then raises RuntimeError on the
        # next trace -- so forget the reference at the same time.
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self._forget_worker(worker))
        worker.start()

    def _forget_worker(self, worker):
        """Drop our reference once a worker has finished, if it is still ours."""
        if self._worker is worker:
            self._worker = None

    def _on_finished(self, result):
        self.trace_button.setEnabled(True)
        self._result = result
        if result.message:
            self.metrics.clear()
            self.tabs.clear_results()
            self.statusBar().showMessage(result.message)
            return
        self.metrics.update_metrics(result)
        self.tabs.set_result(result)
        status = ('Traced %d rays — HPD %.4f arcsec'
                  % (result.num_launched, result.hpd_arcsec))
        if result.warnings:
            status += '  ⚠ ' + ' '.join(result.warnings)
        # Anything the session restore could not honour rides along with the
        # first trace, since a message shown during __init__ is overwritten
        # by 'Tracing…' before it can be read.
        if self._restore_note:
            status += '  ⚠ ' + self._restore_note
            self._restore_note = ''
        self.statusBar().showMessage(status)

    def _on_failed(self, message):
        self.trace_button.setEnabled(True)
        self.metrics.clear()
        self.statusBar().showMessage('Trace failed')
        QtWidgets.QMessageBox.critical(self, 'Trace failed', message)

    def show_script(self):
        p = self.panel.params()
        script = SCRIPT_TEMPLATE.format(
            r0=p.r0, z0=p.z0, pl=p.primary_length, sl=p.secondary_length,
            psi=p.psi, seed=p.seed, n=int(p.num_rays), off=p.offaxis,
            az=p.azimuth, dx=p.sec_dx, dy=p.sec_dy, dz=p.sec_dz,
            rx=p.sec_rx, ry=p.sec_ry, rz=p.sec_rz)

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle('Equivalent PyXFocus script')
        dialog.resize(760, 620)
        box = QtWidgets.QVBoxLayout(dialog)
        text = QtWidgets.QPlainTextEdit(script)
        text.setReadOnly(True)
        text.setStyleSheet('font-family: Menlo, Consolas, monospace;')
        box.addWidget(text)

        row = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton('Copy to clipboard')
        copy.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(script))
        close = QtWidgets.QPushButton('Close')
        close.clicked.connect(dialog.accept)
        row.addStretch(1)
        row.addWidget(copy)
        row.addWidget(close)
        box.addLayout(row)
        dialog.exec_()


def main():
    argv, wants_reset = settings.take_reset_flag(sys.argv)
    app = QtWidgets.QApplication(argv)
    settings.apply_identity()

    store = settings.AppSettings()
    if wants_reset:
        store.reset()
        print('Settings reset (%s)' % store.file_name())

    window = MainWindow(store)
    # There is no closeEvent yet -- Phase 6 adds one. aboutToQuit fires on
    # Quit and on the last window closing, and is worth keeping even after
    # Phase 6: closeEvent does not run when a session manager terminates the
    # app. Both call the same idempotent method.
    app.aboutToQuit.connect(window.save_settings)

    window.show()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
