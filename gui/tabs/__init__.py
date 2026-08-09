"""
The plot-area tabs.

Everything under this package imports Qt by definition. ``gui/wolter.py``
and ``gui/config.py`` must not, and ``test_qt_free_modules_stay_qt_free``
is what enforces that.

The matplotlib backend is selected here rather than in ``app.py`` because
a tab module can now be imported on its own, so ``app.py`` is no longer
guaranteed to run first. A package's ``__init__`` running before any of its
submodules is the one ordering Python does guarantee.
"""

import collections

import matplotlib
matplotlib.use('Qt5Agg')

from PyQt5 import QtWidgets

from PyXFocus.gui.tabs import energy, layout, spot, sweep


TabSpec = collections.namedtuple('TabSpec', 'key title widget needs_params')

# What a tab must be, and what it may be.
#
# REQUIRED
#   A QWidget. That is the whole requirement.
#
# OPTIONAL, probed with getattr -- a tab opts out by not having it
#   set_result(result)  Show this trace. ``result`` is None for "there is
#                       nothing to show"; a tab must blank itself rather
#                       than keep the previous picture. A tab that runs its
#                       own computation (the sweep tab) must NOT define
#                       this: a no-op stub is a lie the container cannot
#                       see through, and a missing name is one it can.
#   is_running()        True while this tab has work in flight.
#   has_result()        True when it has something worth exporting.
#   cancel()            Ask that work to stop.
#   save_csv()          Export it.
#
# There is deliberately no base class and no ABC. Four of the five tabs are
# near-identical and the fifth is not -- the sweep tab owns a worker and
# needs params_provider -- so any interface spanning all five would either
# exclude it or admit it with stub methods. (An ABC is not even available
# cheaply: `class Base(QWidget, metaclass=ABCMeta)` raises a metaclass
# conflict under PyQt5.) The mechanics the drawing tabs share live in
# FigurePane, which is inheritance for reuse: nothing here checks for it.

#: Every tab in the plot area, in the order they appear.
#:
#: APPEND ONLY. The active tab is persisted as an integer INDEX, in two
#: independent places -- QSettings under AppSettings.TAB, and a saved
#: configuration under config.UI_TAB -- so inserting a tab in the middle
#: silently reopens everyone's session on a different tab than they left.
#: A new tab goes on the end. Reordering is a config format change needing
#: a config._MIGRATIONS entry, not a tidy-up.
TABS = (
    TabSpec('spot', 'Spot Diagram', spot.SpotTab, False),
    TabSpec('layout', 'Telescope Layout', layout.LayoutTab, False),
    TabSpec('energy', 'Encircled Energy', energy.EnergyTab, False),
    TabSpec('sweep', 'Parameter Sweep', sweep.SweepTab, True),
)


class PlotTabs(QtWidgets.QTabWidget):
    """
    The plot area: one tab per entry in :data:`TABS`.

    Nothing here knows what any tab draws. Adding a tab is one module in
    this package plus one line in TABS.
    """

    def __init__(self, params_provider, parent=None):
        super(PlotTabs, self).__init__(parent)
        self._by_key = collections.OrderedDict()
        for spec in TABS:
            widget = (spec.widget(params_provider) if spec.needs_params
                      else spec.widget())
            self.addTab(widget, spec.title)
            self._by_key[spec.key] = widget

    def tab(self, key):
        """The tab registered under ``key``."""
        return self._by_key[key]

    @property
    def sweep(self):
        """The sweep tab, which the Run menu and CSV export reach for."""
        return self._by_key['sweep']

    def set_result(self, result):
        """
        Hand a trace to every tab that draws one; None means "nothing".

        getattr rather than a base-class call, so a tab that runs its own
        computation opts out simply by not having the method.
        """
        for widget in self._by_key.values():
            setter = getattr(widget, 'set_result', None)
            if setter is not None:
                setter(result)

    def clear_results(self):
        """
        Blank every tab.

        Not named ``clear()``: QTabWidget.clear() already means "remove
        every tab from this widget", and overriding it with different
        semantics is a trap for whoever next writes ``tabs.clear()``.
        """
        self.set_result(None)
