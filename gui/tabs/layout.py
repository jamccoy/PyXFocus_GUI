"""The telescope in profile, with a zoom inset on the mirrors."""

import numpy as np

from PyXFocus.gui import wolter
from PyXFocus.gui.tabs.pane import FigurePane


class LayoutTab(FigurePane):
    """
    Radius against z, plus an inset on the grazing-incidence region.

    A Wolter-I is roughly 8 m long but only ~20 cm in radius, so at a
    scale that shows rays converging on the focus the two mirrors collapse
    into a single invisible speck. The inset zooms on them.
    """

    def __init__(self, parent=None):
        super(LayoutTab, self).__init__(parent)
        self.ax = self.figure.add_subplot(111)
        #: The zoom inset, rebuilt on every redraw.
        self._inset = None

    def _draw(self, result):
        ax = self.ax
        # Drop the inset explicitly and keep the reference honest. On
        # matplotlib 3.3.2 ax.clear() does remove child axes, so this is
        # belt and braces rather than a fix -- but _inset would otherwise
        # be left pointing at a removed axes, and the next redraw would
        # call .remove() on it a second time. Doing it here, in the single
        # drawing entry point, is also what keeps the clearing path and
        # the drawing path from drifting apart.
        if self._inset is not None:
            self._inset.remove()
            self._inset = None
        ax.clear()
        if result is None:
            # Return before labels and title: the old clearing path left
            # bare axes, and keeping them furnished would be a visible
            # change rather than a refactor.
            return

        params = result.params
        profiles = wolter.mirror_profile(params)
        self._plot_into(ax, result, profiles, full=True)

        ax.set_xlabel('z [mm]')
        ax.set_ylabel('radius [mm]')
        ax.set_title('Telescope profile (rays travelling −z)')
        ax.legend(loc='upper left', fontsize=8)
        ax.grid(alpha=.3)

        (zp, rp), (zs, rs) = profiles
        # Rays run diagonally from bottom-left to top-right, so the bottom
        # -right corner is free; the legend keeps the top-left.
        inset = ax.inset_axes([0.55, 0.12, 0.42, 0.45])
        self._plot_into(inset, result, profiles, full=False)
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
        self._inset = inset

    @staticmethod
    def _plot_into(ax, result, profiles, full):
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
