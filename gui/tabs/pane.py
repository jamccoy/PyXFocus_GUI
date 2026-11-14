"""
A pane that paints only when it can be seen.

This is inheritance for *reuse*, not an interface: nothing in the registry
checks for these classes, and a tab is free to draw with whatever it likes.

What they exist to hold is the one piece of state every drawing tab gets
subtly wrong on its own -- "there is a newer result than the pixels on
screen". Before it, every trace repainted all three plot tabs including the
ones nobody was looking at, and the spot tab scatters up to 500 000
alpha-blended points to do that.

It also collapses drawing and clearing into a single entry point per tab.
The layout tab previously had two -- ``_draw_layout`` removed its zoom
inset and ``clear_all`` did not -- which is the shape a bug grows in even
where, as it happens, matplotlib 3.3.2 cleaned up after it anyway.

The gate itself is :class:`PaintGate` and knows nothing about matplotlib;
:class:`FigurePane` adds the figure, canvas and toolbar.  They are separate
because the 3D tab renders with OpenGL and has no canvas to speak of, and
duplicating a dirty flag is exactly how two tabs drift into disagreeing
about when a repaint is owed.

:class:`FigurePane` also follows the theme chosen under View > Theme. It
draws under that theme's matplotlib settings and restyles its figure
afterwards, because the settings govern only what is created after them
and ``ax.clear()`` keeps the colours an axes was born with.
"""

import matplotlib
from endstation.qt import theming
from endstation.theme import pane as pane_color
from endstation.theme import rc_params
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6 import QtWidgets


def style_figure(figure, theme):
    """Restyle an existing figure and every axes on it, 2D or 3D."""
    figure.set_facecolor(theme.figure)
    for axes in figure.axes:
        axes.set_facecolor(theme.axes)
        axes.tick_params(colors=theme.ink, labelcolor=theme.ink, which='both')
        for spine in getattr(axes, 'spines', {}).values():
            spine.set_edgecolor(theme.ink)
        zaxis = getattr(axes, 'zaxis', None)
        if zaxis is not None:
            for axis in (axes.xaxis, axes.yaxis, zaxis):
                axis.set_pane_color(pane_color(theme))
                axis.line.set_color(theme.ink)


class PaintGate(object):
    """
    "There is a newer result than the pixels", without a canvas.

    A mixin, and ``QtWidgets.QWidget`` must come *last* in the bases, because
    :meth:`showEvent` relies on plain cooperative ``super()`` to reach it.

    There is deliberately no ``__init__`` here.  A cooperative one across a
    plain object and a QWidget buys nothing under PySide6's metaclass rules,
    so subclasses call :meth:`_init_paint_gate` explicitly and it is
    obvious in each of them that they did.
    """

    def _init_paint_gate(self):
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
        super(PaintGate, self).showEvent(event)
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
        self._present()
        self._painted = self._result
        self.paints += 1

    def force_repaint(self):
        """
        Repaint the current result even though it has not changed.

        For a tab that has view controls of its own: the identity gate in
        :meth:`flush` is right about results and knows nothing about views,
        so a changed camera, zoom or visibility toggle would otherwise be
        treated as a no-op.
        """
        self._painted = None
        self.flush()

    def _present(self):
        """
        Put what :meth:`_draw` produced on screen.

        Nothing for a renderer that paints during ``_draw``; a
        ``draw_idle`` for one that fills a canvas and schedules it.
        """

    def _draw(self, result):
        """
        Subclass hook. ``result`` is None for "clear yourself".

        Implementations must return immediately once the axes are cleared
        when ``result`` is None -- before setting labels, title or grid.
        A cleared tab that keeps its labels is a visible change from the
        blank axes the old ``clear_all`` produced.
        """
        raise NotImplementedError


class FigurePane(PaintGate, QtWidgets.QWidget):
    """A toolbar, a canvas, a dirty flag, and the theme."""

    #: The theme this pane always draws in, or None to follow the one
    #: chosen under View > Theme.
    THEME = None

    def __init__(self, parent=None):
        super(FigurePane, self).__init__(parent)
        with matplotlib.rc_context(rc_params(self.theme())):
            self.figure = Figure(figsize=(5, 4), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        box = QtWidgets.QVBoxLayout(self)
        box.addWidget(NavigationToolbar2QT(self.canvas, self))
        box.addWidget(self.canvas, 1)
        self._init_paint_gate()
        theming.subscribe(self._on_theme)

    def theme(self):
        """The theme this pane draws in now."""
        return self.THEME or theming.current()

    def flush(self):
        # Drawn under the theme's settings, so labels, legends and new
        # ticks take its colours, then restyled, for what clear() kept.
        theme = self.theme()
        with matplotlib.rc_context(rc_params(theme)):
            super(FigurePane, self).flush()
        style_figure(self.figure, theme)

    def _on_theme(self, _theme):
        """Repaint in the new theme: now if on screen, else when shown."""
        self._painted = None
        if self.isVisible():
            self.flush()

    def _present(self):
        self.canvas.draw_idle()
