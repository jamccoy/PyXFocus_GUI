"""The focal-plane spot diagram."""

import matplotlib.patches
import numpy as np

from PyXFocus.gui.tabs.pane import FigurePane


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
