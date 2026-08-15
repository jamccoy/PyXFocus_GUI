"""
The focal-plane spot diagram.

With a grating tracing several orders this becomes the spectrum: each order
lands somewhere else along the dispersion direction, and they are drawn about
the *reference* order's centroid so that the separation is what you see.  The
HPD circle stays on the reference order, because that is the only order the
metrics are measured on.
"""

import matplotlib.patches
import numpy as np

from PyXFocus.gui import scene3d
from PyXFocus.gui.tabs.pane import FigurePane

_SPOT_COLOR = '#1f77b4'


class SpotTab(FigurePane):
    """Where the surviving rays land, in arcseconds."""

    def __init__(self, parent=None):
        super(SpotTab, self).__init__(parent)
        self.ax = self.figure.add_subplot(111)

    def _draw(self, result):
        ax = self.ax
        ax.clear()
        if result is None:
            return

        self._draw_spot(ax, result)

        # Mark the half-power diameter for scale.
        if np.isfinite(result.hpd_arcsec) and result.hpd_arcsec > 0:
            circle = matplotlib.patches.Circle(
                (0, 0), result.hpd_arcsec / 2., fill=False, color='crimson',
                lw=1.5, ls='--', label='HPD = %.4f"' % result.hpd_arcsec)
            ax.add_patch(circle)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(handles, labels, loc='upper right', fontsize=8)

        ax.set_xlabel('x [arcsec]')
        ax.set_ylabel('y [arcsec]')
        ax.set_title('Focal plane spot')
        ax.set_aspect('equal')
        ax.grid(alpha=.3)

    @staticmethod
    def _draw_spot(ax, result):
        by_order = (result.spot_arcsec_by_order()
                    if hasattr(result, 'spot_arcsec_by_order') else None)
        if by_order is None:
            ax.scatter(*result.spot_arcsec, s=1, alpha=.3, color=_SPOT_COLOR,
                       edgecolors='none')
            return
        # The same colours the 3D view gives the rays, so an order can be
        # followed from where it leaves the grating to where it lands. That
        # is why the table lives in scene3d, which imports no Qt, rather
        # than in either tab.
        for m, (x, y) in by_order.items():
            if not len(x):
                continue
            ax.scatter(x, y, s=1, alpha=.4, edgecolors='none',
                       color=scene3d.color_for_order(m, alpha=1.)[:3],
                       label='m = %+d' % m if m else 'm = 0')
