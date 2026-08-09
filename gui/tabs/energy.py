"""The encircled-energy curve."""

from PyXFocus.gui import wolter
from PyXFocus.gui.tabs.pane import FigurePane


class EnergyTab(FigurePane):
    """Enclosed fraction against radius from the centroid."""

    def __init__(self, parent=None):
        super(EnergyTab, self).__init__(parent)
        self.ax = self.figure.add_subplot(111)

    def _draw(self, result):
        ax = self.ax
        ax.clear()
        if result is None:
            return

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
