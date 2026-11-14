"""
The parameter-sweep tab: vary one parameter, plot how performance responds.

Kept out of the drawing-tab family on purpose. The other tabs are handed a
TraceResult and draw it; this one runs its own computation on its own
JobRunner, so it has no ``set_result`` and ``PlotTabs`` never feeds it.
See the contract documented above ``TABS`` in this package's __init__.
"""

import threading

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib.figure import Figure
from endstation.qt import recent
from endstation.qt import theming
from endstation.qt.jobs import JobRunner
from endstation.qt.progress_bar import DelayedProgressBar
from PySide6 import QtWidgets

from PyXFocus.gui import wolter
from PyXFocus.gui.tabs.pane import style_figure


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
        #: Its own runner, so a sweep and the main window's trace can run
        #: at once, as they always could.
        self._runner = JobRunner(self, gc_guard=True)
        #: Set by Cancel, read by wolter.sweep before each point. Not the
        #: runner's cancel, which discards the result: a cancelled sweep
        #: keeps and plots the points it has already traced.
        self._stop = threading.Event()
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

        self.progress = DelayedProgressBar()
        self.progress.follow(self._runner)

        figure = Figure(figsize=(5, 4), tight_layout=True)
        self.figure = figure
        self.canvas = FigureCanvasQTAgg(figure)
        self.ax = figure.add_subplot(111)
        self.ax2 = self.ax.twinx()

        box = QtWidgets.QVBoxLayout(self)
        box.addLayout(controls)
        box.addWidget(self.progress)
        box.addWidget(NavigationToolbar2QT(self.canvas, self))
        box.addWidget(self.canvas, 1)

        self._apply_default_range()
        theming.subscribe(self._on_theme)

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
        if self._runner.busy():
            return
        name = self.combo.currentData()
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._stop = threading.Event()
        self._runner.start(wolter.sweep, self._params_provider(), name,
                           self.start.value(), self.stop.value(),
                           self.steps.value(), should_stop=self._stop.is_set,
                           on_done=self._on_swept, on_fail=self._on_failed)

    def is_running(self):
        """True while a sweep is in flight (drives the Run menu)."""
        return self._runner.busy()

    def has_result(self):
        """True when there is a sweep worth exporting."""
        return self._result is not None and bool(self._result.valid.any())

    def cancel(self):
        """Ask the running sweep to stop after the current point."""
        self._stop.set()

    def _on_swept(self, result):
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._result = result
        self.save_button.setEnabled(bool(result.valid.any()))
        self._draw(result)

    def _on_failed(self, message):
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        QtWidgets.QMessageBox.critical(self, 'Sweep failed', message)

    def _on_theme(self, theme):
        """Redraw the sweep in the new theme, or just recolour the blank axes."""
        if self._result is not None:
            self._draw(self._result)
        else:
            style_figure(self.figure, theme)
            self.canvas.draw_idle()

    def _draw(self, result):
        self.ax.clear()
        self.ax2.clear()
        # clear() puts a twin's axis label back on the left while its
        # ticks stay right, so the label would sit on top of the HPD one.
        self.ax2.yaxis.set_label_position('right')
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

        style_figure(self.figure, theming.current())
        self.canvas.draw_idle()

    def save_csv(self):
        if self._result is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save sweep as CSV',
            recent.start_in('exports', 'sweep_%s.csv' % self._result.name),
            'CSV files (*.csv)')
        if path:
            recent.remember('exports', path)
            self._result.to_csv(path)
