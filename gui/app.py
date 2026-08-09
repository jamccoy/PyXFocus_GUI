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

import matplotlib
matplotlib.use('Qt5Agg')

import matplotlib.patches
import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt5 import QtCore, QtWidgets

from PyXFocus.gui import settings
from PyXFocus.gui import wolter
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


class SweepWorker(QtCore.QThread):
    """Runs a parameter sweep off the UI thread, with cancel support."""

    progressed = QtCore.pyqtSignal(int, int)
    swept = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, params, name, start, stop, steps, parent=None):
        super(SweepWorker, self).__init__(parent)
        self.params = params
        self.name = name
        #: Named start_value/stop_value, not start/stop: `self.start` would
        #: shadow QThread.start() and break the thread launch.
        self.start_value = start
        self.stop_value = stop
        self.steps = steps
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        try:
            result = wolter.sweep(
                self.params, self.name, self.start_value, self.stop_value,
                self.steps,
                progress=lambda done, total: self.progressed.emit(done, total),
                should_stop=lambda: self._stop)
        except Exception:
            self.failed.emit(traceback.format_exc())
        else:
            self.swept.emit(result)


class SweepTab(QtWidgets.QWidget):
    """
    Vary one parameter and plot how image quality responds.

    This is the tolerancing question -- how far can a mirror shift before
    the error budget is spent -- so HPD and throughput are plotted together
    against the swept value.
    """

    def __init__(self, params_provider, parent=None):
        super(SweepTab, self).__init__(parent)
        self._params_provider = params_provider
        self._worker = None
        self._result = None

        self.combo = QtWidgets.QComboBox()
        for name, label, unit in wolter.SWEEPABLE:
            self.combo.addItem('%s [%s]' % (label, unit) if unit else label,
                               name)
        self.combo.setCurrentIndex(
            [n for n, _, _ in wolter.SWEEPABLE].index('sec_dy'))
        self.combo.currentIndexChanged.connect(self._apply_default_range)

        self.start = self._spin()
        self.stop = self._spin()
        self.steps = QtWidgets.QSpinBox()
        self.steps.setRange(2, 500)
        self.steps.setValue(30)

        self.run_button = QtWidgets.QPushButton('Run sweep')
        self.run_button.clicked.connect(self.run_sweep)
        self.cancel_button = QtWidgets.QPushButton('Cancel')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.save_button = QtWidgets.QPushButton('Save CSV…')
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_csv)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel('Vary:'))
        controls.addWidget(self.combo, 1)
        for label, widget in (('from', self.start), ('to', self.stop),
                              ('steps', self.steps)):
            controls.addWidget(QtWidgets.QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(self.run_button)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.save_button)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setVisible(False)

        figure = Figure(figsize=(5, 4), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(figure)
        self.ax = figure.add_subplot(111)
        self.ax2 = self.ax.twinx()

        box = QtWidgets.QVBoxLayout(self)
        box.addLayout(controls)
        box.addWidget(self.progress)
        box.addWidget(NavigationToolbar2QT(self.canvas, self))
        box.addWidget(self.canvas, 1)

        self._apply_default_range()

    @staticmethod
    def _spin():
        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(4)
        spin.setRange(-1e6, 1e6)
        spin.setKeyboardTracking(False)
        return spin

    def _apply_default_range(self):
        """Pick a useful range for the newly selected parameter."""
        low, high = wolter.sweep_range(self.combo.currentData(),
                                       self._params_provider())
        self.start.setValue(low)
        self.stop.setValue(high)

    def run_sweep(self):
        if self._worker is not None and self._worker.isRunning():
            return
        name = self.combo.currentData()
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)

        self._worker = SweepWorker(self._params_provider(), name,
                                   self.start.value(), self.stop.value(),
                                   self.steps.value(), self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.swept.connect(self._on_swept)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def is_running(self):
        """True while a sweep is in flight (drives the Run menu)."""
        return self._worker is not None and self._worker.isRunning()

    def has_result(self):
        """True when there is a sweep worth exporting."""
        return self._result is not None and bool(self._result.valid.any())

    def cancel(self):
        """Ask the running sweep to stop after the current point."""
        if self._worker is not None:
            self._worker.cancel()

    def _on_progress(self, done, total):
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _on_swept(self, result):
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setVisible(False)
        self._result = result
        self.save_button.setEnabled(bool(result.valid.any()))
        self._draw(result)

    def _on_failed(self, message):
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setVisible(False)
        QtWidgets.QMessageBox.critical(self, 'Sweep failed', message)

    def _draw(self, result):
        self.ax.clear()
        self.ax2.clear()
        good = result.valid

        self.ax.plot(result.values[good], result.hpd_arcsec[good],
                     color='#1f77b4', lw=1.8, label='HPD')

        # Split the markers: a point where rays failed to converge is a
        # lower bound, not a measurement, so it must not look like one.
        degraded = good & (result.nonconverged > 0)
        solid = good & ~degraded
        self.ax.plot(result.values[solid], result.hpd_arcsec[solid],
                     ls='none', marker='o', ms=3, color='#1f77b4')
        if degraded.any():
            self.ax.plot(result.values[degraded], result.hpd_arcsec[degraded],
                         ls='none', marker='o', ms=5, mfc='none',
                         mec='#c0392b', mew=1.2,
                         label='non-converged (bound)')
        self.ax2.plot(result.values[good], 100. * result.throughput[good],
                      color='#7f7f7f', lw=1.2, ls='--', label='Throughput')

        # Mark where the baseline configuration sits.
        base = getattr(result.params, result.name)
        lo, hi = float(np.min(result.values)), float(np.max(result.values))
        if lo <= base <= hi:
            self.ax.axvline(base, color='crimson', ls=':', lw=1.2)

        # Call out points that could not be traced at all.
        if (~good).any():
            self.ax.plot(result.values[~good],
                         np.zeros((~good).sum()), 'x', color='crimson',
                         ms=6, label='not traceable')

        self.ax.set_xlabel(result.label)
        self.ax.set_ylabel('HPD [arcsec]')
        self.ax2.set_ylabel('throughput [%]', color='#7f7f7f')
        self.ax2.set_ylim(0, 105)
        self.ax.set_title('Sensitivity to %s' % result.label)
        self.ax.grid(alpha=.3)

        handles = self.ax.get_legend_handles_labels()[0] + \
            self.ax2.get_legend_handles_labels()[0]
        labels = self.ax.get_legend_handles_labels()[1] + \
            self.ax2.get_legend_handles_labels()[1]
        if handles:
            self.ax.legend(handles, labels, loc='best', fontsize=8)

        self.canvas.draw_idle()

    def save_csv(self):
        if self._result is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save sweep as CSV', 'sweep_%s.csv' % self._result.name,
            'CSV files (*.csv)')
        if path:
            self._result.to_csv(path)


class PlotTabs(QtWidgets.QTabWidget):
    """Spot diagram, telescope profile, and encircled energy."""

    def __init__(self, params_provider, parent=None):
        super(PlotTabs, self).__init__(parent)
        self.spot_ax = self._add_tab('Spot Diagram')
        self.layout_ax = self._add_tab('Telescope Layout')
        self.ee_ax = self._add_tab('Encircled Energy')
        #: Zoom inset on the layout tab, rebuilt on every redraw.
        self._layout_inset = None
        #: Sweeps run on demand, so this tab is not touched by draw_all.
        self.sweep = SweepTab(params_provider)
        self.addTab(self.sweep, 'Parameter Sweep')

    def _add_tab(self, title):
        page = QtWidgets.QWidget()
        box = QtWidgets.QVBoxLayout(page)
        figure = Figure(figsize=(5, 4), tight_layout=True)
        canvas = FigureCanvasQTAgg(figure)
        box.addWidget(NavigationToolbar2QT(canvas, page))
        box.addWidget(canvas)
        self.addTab(page, title)
        ax = figure.add_subplot(111)
        ax.canvas = canvas
        return ax

    def draw_all(self, result):
        self._draw_spot(result)
        self._draw_layout(result)
        self._draw_ee(result)

    def _draw_spot(self, result):
        ax = self.spot_ax
        ax.clear()
        x, y = result.spot_arcsec
        ax.scatter(x, y, s=1, alpha=.3, color='#1f77b4', edgecolors='none')

        # Mark the half-power diameter for scale.
        if np.isfinite(result.hpd_arcsec) and result.hpd_arcsec > 0:
            circle = matplotlib.patches.Circle(
                (0, 0), result.hpd_arcsec / 2., fill=False, color='crimson',
                lw=1.5, ls='--', label='HPD = %.4f"' % result.hpd_arcsec)
            ax.add_patch(circle)
            ax.legend(loc='upper right', fontsize=8)

        ax.set_xlabel('x [arcsec]')
        ax.set_ylabel('y [arcsec]')
        ax.set_title('Focal plane spot')
        ax.set_aspect('equal')
        ax.grid(alpha=.3)
        ax.canvas.draw_idle()

    def _draw_layout(self, result):
        """
        Telescope in profile, with a zoom inset on the mirrors.

        A Wolter-I is roughly 8 m long but only ~20 cm in radius, so at a
        scale that shows rays converging on the focus the two mirrors
        collapse into a single invisible speck.  The inset zooms on the
        grazing-incidence region so the primary and secondary are actually
        distinguishable.
        """
        ax = self.layout_ax
        if self._layout_inset is not None:
            # ax.clear() leaves child axes behind, so drop it explicitly.
            self._layout_inset.remove()
            self._layout_inset = None
        ax.clear()

        params = result.params
        profiles = wolter.mirror_profile(params)
        self._plot_layout_into(ax, result, profiles, full=True)

        ax.set_xlabel('z [mm]')
        ax.set_ylabel('radius [mm]')
        ax.set_title('Telescope profile (rays travelling −z)')
        ax.legend(loc='upper left', fontsize=8)
        ax.grid(alpha=.3)

        # Inset zoomed on the mirrors themselves.
        (zp, rp), (zs, rs) = profiles
        # Rays run diagonally from bottom-left to top-right, so the bottom
        # -right corner is free; the legend keeps the top-left.
        inset = ax.inset_axes([0.55, 0.12, 0.42, 0.45])
        self._plot_layout_into(inset, result, profiles, full=False)
        zlo = params.z0 - params.secondary_length
        zhi = params.z0 + params.primary_length
        rlo, rhi = float(np.min(rs)), float(np.max(rp))
        zpad, rpad = .05 * (zhi - zlo), max(.05 * (rhi - rlo), 1e-3)
        inset.set_xlim(zlo - zpad, zhi + zpad)
        inset.set_ylim(rlo - rpad, rhi + rpad)
        inset.tick_params(labelsize=6)
        inset.set_title('mirrors (zoom)', fontsize=7)
        inset.grid(alpha=.3)
        ax.indicate_inset_zoom(inset, edgecolor='gray')
        self._layout_inset = inset

        ax.canvas.draw_idle()

    @staticmethod
    def _plot_layout_into(ax, result, profiles, full):
        """Draw mirrors, rays and focus into ``ax``."""
        (zp, rp), (zs, rs) = profiles
        if result.path_z is not None:
            # Columns are individual rays, rows are successive surfaces.
            ax.plot(result.path_z, result.path_r, color='#1f77b4',
                    lw=.4, alpha=.5)
        ax.plot(zp, rp, color='k', lw=2.5,
                label='Primary' if full else None)
        ax.plot(zs, rs, color='#d62728', lw=2.5,
                label='Secondary' if full else None)
        if full:
            ax.axvline(0., color='crimson', ls='--', lw=1, label='Focus')

    def _draw_ee(self, result):
        ax = self.ee_ax
        ax.clear()
        rad, frac = wolter.encircled_energy(result)
        if len(rad):
            ax.plot(rad, frac, color='#1f77b4', lw=1.5)
            # The x axis is a radius, so the half-power point sits at
            # HPD/2 -- label both so the two can't be confused.
            ax.axhline(.5, color='crimson', ls='--', lw=1)
            ax.axvline(result.hpd_arcsec / 2., color='crimson', ls='--', lw=1,
                       label='half-power radius %.4f"\n(HPD = %.4f")'
                             % (result.hpd_arcsec / 2., result.hpd_arcsec))
            ax.legend(loc='lower right', fontsize=8)
        ax.set_xlabel('radius from centroid [arcsec]')
        ax.set_ylabel('enclosed fraction')
        ax.set_title('Encircled energy')
        ax.set_ylim(0, 1.02)
        ax.grid(alpha=.3)
        ax.canvas.draw_idle()

    def clear_all(self):
        for ax in (self.spot_ax, self.layout_ax, self.ee_ax):
            ax.clear()
            ax.canvas.draw_idle()


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
            self.tabs.clear_all()
            self.statusBar().showMessage(result.message)
            return
        self.metrics.update_metrics(result)
        self.tabs.draw_all(result)
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
