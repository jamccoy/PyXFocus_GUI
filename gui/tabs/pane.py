"""
A matplotlib canvas that paints only when it can be seen.

This is inheritance for *reuse*, not an interface: nothing in the registry
checks for this class, and a tab drawing with something other than
matplotlib is expected not to use it.

What it exists to hold is the one piece of state every drawing tab gets
subtly wrong on its own -- "there is a newer result than the pixels on
screen". Before it, every trace repainted all three plot tabs including the
ones nobody was looking at, and the spot tab scatters up to 500 000
alpha-blended points to do that.

It is also where the two invariants that caused the zoom-inset bug now live
exactly once. That bug was a cleared plot with a fully drawn inset floating
over it, because ``_draw_layout`` removed the inset and ``clear_all``
forgot: the same rule written in two places, which is what a shared state
machine prevents and four copies of eight lines would not.
"""

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt5 import QtWidgets


class FigurePane(QtWidgets.QWidget):
    """A toolbar, a canvas, and a dirty flag."""

    def __init__(self, parent=None):
        super(FigurePane, self).__init__(parent)
        self.figure = Figure(figsize=(5, 4), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        box = QtWidgets.QVBoxLayout(self)
        box.addWidget(NavigationToolbar2QT(self.canvas, self))
        box.addWidget(self.canvas, 1)

        self._result = None
        #: The result the pixels currently correspond to. Compared by
        #: identity, not equality: two traces are always two objects, and
        #: re-handing the same one is a no-op worth skipping.
        self._painted = None
        #: How many times this pane has actually repainted. Read by the
        #: lazy-paint tests; costs one int.
        self.paints = 0

    # -- the tab contract --------------------------------------------------

    def set_result(self, result):
        """
        Show ``result``; None means "there is nothing to show".

        Stored unconditionally, painted conditionally. A test with no
        window on screen can read it back, and a tab shown for the first
        time after five traces paints the fifth rather than the first.
        """
        self._result = result
        if self.isVisible():
            self.flush()

    def result(self):
        """The newest result handed to this pane, painted or not."""
        return self._result

    # -- painting ----------------------------------------------------------

    def showEvent(self, event):
        super(FigurePane, self).showEvent(event)
        self.flush()

    def flush(self):
        """
        Paint what is pending, whether on screen or not.

        Deliberately does *not* re-check visibility. The gate in
        ``set_result`` is an optimisation; this is the one path that
        actually draws, and a test cannot call ``show()`` here --  it
        segfaults under QT_QPA_PLATFORM=offscreen -- so a visibility check
        in this method would silently turn every parity test into a no-op.
        """
        if self._painted is self._result and self.paints:
            return
        self._draw(self._result)
        self.canvas.draw_idle()
        self._painted = self._result
        self.paints += 1

    def _draw(self, result):
        """
        Subclass hook. ``result`` is None for "clear yourself".

        Implementations must return immediately once the axes are cleared
        when ``result`` is None -- before setting labels, title or grid.
        A cleared tab that keeps its labels is a visible change from the
        blank axes the old ``clear_all`` produced.
        """
        raise NotImplementedError
