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
from PyQt5 import QtCore, QtGui, QtWidgets

from PyXFocus.gui import docs_index
from PyXFocus.gui import docview
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
        #: Fields drawn as a combo box rather than a spin box, keyed by
        #: parameter name. They store the selected index.
        self._choices = {}
        #: Parameters a configuration carries but the panel gives no field
        #: for. Without this a saved seed would silently revert to 0 on
        #: reload, which makes a "reproducible" configuration a lie.
        self._carried = {}
        #: Checkable groups, keyed by the 0/1 parameter each one drives.
        self._enables = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for title, names, enable in wolter.PARAM_GROUPS:
            layout.addWidget(self._group(title, names, enable))
        layout.addStretch(1)

        grouped = set(n for _, names, _ in wolter.PARAM_GROUPS for n in names)
        for spec in wolter.PARAM_SPECS:
            if spec.name not in grouped:
                self._carried[spec.name] = spec.default

    def _group(self, title, names, enable=None):
        box = QtWidgets.QGroupBox(title)
        if enable is not None:
            # A checkable group box greys out its own contents, so an
            # optional part of the instrument needs no extra plumbing to
            # look optional. The flag itself rides in _carried with the
            # other fieldless parameters.
            box.setCheckable(True)
            box.setChecked(bool(wolter.param_spec(enable).default))
            box.toggled.connect(
                lambda on, key=enable: self._set_enabled(key, on))
            self._enables[enable] = box
        form = QtWidgets.QFormLayout(box)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        for name in names:
            spec = wolter.param_spec(name)
            if spec.choices:
                # A choice, not a quantity: a spin box reading "1" for
                # "Radial" would be a field nobody can use without the
                # source. The selected index is what params() stores, which
                # is why every such field is also an INT_FIELD.
                combo = QtWidgets.QComboBox()
                combo.addItems(list(spec.choices))
                combo.setCurrentIndex(int(spec.default))
                combo.currentIndexChanged.connect(self.changed)
                self._choices[name] = combo
                form.addRow(spec.label + ':', combo)
                continue
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

    def _set_enabled(self, key, on):
        """Record a group checkbox and re-trace, as a spin box would."""
        self._carried[key] = int(bool(on))
        self.changed.emit()

    def params(self):
        """Current field values as a :class:`WolterParams`."""
        p = WolterParams()
        for name, spin in self._spins.items():
            value = spin.value()
            setattr(p, name, int(value) if name in wolter.INT_FIELDS else value)
        for name, combo in self._choices.items():
            setattr(p, name, int(combo.currentIndex()))
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
        # Combo boxes are blocked in the same window as the spin boxes, so
        # that the single `changed` promised above stays single.
        for widget in list(self._spins.values()) + list(self._choices.values()):
            widget.blockSignals(True)
        try:
            for name, combo in self._choices.items():
                if not hasattr(params, name):
                    continue
                wanted = int(getattr(params, name))
                if 0 <= wanted < combo.count():
                    combo.setCurrentIndex(wanted)
                else:
                    adjusted.append((name, wanted, combo.currentIndex()))

            for name, spin in self._spins.items():
                if not hasattr(params, name):
                    continue
                wanted = float(getattr(params, name))

                if not math.isfinite(wanted):
                    # setValue(nan) silently yields the maximum, which then
                    # reads back as a deliberate extreme. Leave the field.
                    adjusted.append((name, wanted, spin.value()))
                    continue

                if name in wolter.INT_FIELDS:
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
            for widget in (list(self._spins.values())
                           + list(self._choices.values())):
                widget.blockSignals(False)

        for name in self._carried:
            if hasattr(params, name):
                self._carried[name] = getattr(params, name)

        # Checkboxes after _carried, and with signals blocked, so that the
        # one changed emitted below is still the only one -- a listener must
        # see a whole parameter set, never a half-applied one.
        for name, box in self._enables.items():
            box.blockSignals(True)
            try:
                box.setChecked(bool(self._carried.get(name, 0)))
            finally:
                box.blockSignals(False)

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
        # With several orders in flight, "surviving" would otherwise quietly
        # change meaning: it counts the reference order, which is what every
        # metric beside it is measured on, so the total across the fan is
        # named separately rather than folded in.
        rays = '%d / %d' % (result.num_surviving, result.num_launched)
        total = getattr(result, 'num_surviving_all_orders', 0)
        if total and total != result.num_surviving:
            rays += '  (%d all orders)' % total
        v['rays'].setText(rays)
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


# The equivalent-script text is generated from the system itself, in
# wolter.script_for -- see its docstring for why it is not a template here.


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

        #: Held rather than local: DocsWindow is non-modal, and a non-modal
        #: dialog with no surviving reference is collected and disappears.
        self._docs_window = None
        self._build_menus()

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

    # -- menus -------------------------------------------------------------

    def _build_menus(self):
        """
        The menu bar. Help only, for now.

        The trace controls stay the QPushButtons they have always been; this
        adds a home for the things that have nowhere else to live, not a
        second way to do what the buttons already do. (Two docstrings in
        gui/tabs still refer to a "Run menu" that does not exist -- that is
        a separate change, not this one.)

        WINDOW_STATE_VERSION is deliberately not bumped: saveState() covers
        toolbars and docks, and a menu bar is neither, so no saved blob is
        invalidated by this.
        """
        help_menu = self.menuBar().addMenu('&Help')

        docs_action = QtWidgets.QAction('Documentation', self)
        docs_action.setShortcut(QtGui.QKeySequence.HelpContents)
        docs_action.triggered.connect(self.show_docs)
        help_menu.addAction(docs_action)

        wiki_action = QtWidgets.QAction('View Wiki Online', self)
        wiki_action.setToolTip(docs_index.WIKI_URL)
        wiki_action.triggered.connect(self.open_wiki)
        help_menu.addAction(wiki_action)

        help_menu.addSeparator()

        about_action = QtWidgets.QAction('About Wolter-I Explorer', self)
        # Qt spots an action whose text starts with "About" and relocates it
        # into the macOS application menu. That is the right home for it, so
        # this is left alone deliberately rather than pinned with NoRole --
        # but it does mean it will not appear under Help on a Mac.
        about_action.setMenuRole(QtWidgets.QAction.AboutRole)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def show_docs(self):
        """Open (or re-raise) the bundled documentation."""
        if self._docs_window is None:
            self._docs_window = docview.DocsWindow(self)
            self.settings.restore_geometry(
                self._docs_window, key=settings.AppSettings.DOCS_GEOMETRY)
            self._docs_window.finished.connect(self._on_docs_closed)
        self._docs_window.show()
        self._docs_window.raise_()
        self._docs_window.activateWindow()

    def _on_docs_closed(self, result):
        """Remember where the docs window was before letting go of it."""
        if self._docs_window is None:
            return
        try:
            self.settings.save_geometry(
                self._docs_window, key=settings.AppSettings.DOCS_GEOMETRY)
        except Exception:
            pass
        self._docs_window = None

    def open_wiki(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(docs_index.WIKI_URL))

    def show_about(self):
        QtWidgets.QMessageBox.about(
            self, 'About Wolter-I Explorer',
            '<b>PyXFocus — Wolter-I Explorer</b>'
            '<p>A PyQt5 front end for PyXFocus, the raytracing package for '
            'X-ray telescope design.</p>'
            '<p>The raytracing engine is the work of Ryan Allured and '
            'contributors, under the MIT licence.</p>'
            '<p><a href="%s">%s</a></p>' % (docs_index.WIKI_URL,
                                            docs_index.WIKI_URL))

    def show_script(self):
        script = wolter.script_for(self.panel.params())

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
