"""
The telescope in three dimensions, drawn from whatever elements it contains.

The 2D layout tab plots radius against z, which is the right picture for a
system that is a surface of revolution and says nothing at all about one that
is not.  Azimuth, decentre and tilt are exactly the quantities it cannot show:
a secondary tilted by 5 arcmin looks, in profile, like a secondary.

This tab asks the system for its geometry -- ``System.patches`` -- rather than
asking a Wolter telescope for its two mirror profiles, so a nested shell, a
grating or a tilted detector will draw here as soon as it exists as an
element, with no change to this file.

Two things about mplot3d drive nearly every decision below.

*Occlusion.*  matplotlib 3.3 has no ``computed_zorder``: ``plot_surface``
depth-sorts each surface as a single unit, so an opaque mirror shell swallows
the rays inside it, non-deterministically, as the camera turns.  The default
here is therefore a wireframe, which is see-through by construction.  The
solid alternative draws half a shell -- ``dphi = pi`` -- so that there is an
open side no matter where the camera is.  Note that is the *element's*
argument doing the work, not a trick played here.

*Aspect.*  A Wolter-I is roughly 8 m long and 20 cm in radius.  Drawn to
scale it is an invisible thread.  The z axis is compressed and the label says
by how much, because an unlabelled 38:1 squash makes a Wolter-I look like a
Cassegrain.  What is never compressed is x against y: azimuth, tilt and
decentre all live in that plane and are the whole reason for this view.
"""

import numpy as np
from matplotlib.colors import to_rgba
from mpl_toolkits.mplot3d import Axes3D             # noqa: F401  (projection)
from mpl_toolkits.mplot3d.art3d import Line3DCollection
from PyQt5 import QtWidgets

from PyXFocus.gui import wolter
from PyXFocus.gui.tabs.pane import FigurePane

#: Vertices we are willing to re-project on every mouse move. mplot3d
#: transforms the whole scene per frame, so this is a frame-rate budget.
MAX_VERTICES = 20000

#: Points along each surface's axis. A Wolter conic sags well under a
#: millimetre over 100 mm against a 220 mm radius, so a handful is plenty and
#: the azimuthal direction is where the vertices are worth spending.
N_AXIAL = 8

#: How much taller than wide the plot box is. Enough to read the system as
#: long without squeezing the mirrors into the axis.
Z_BOX = 2.2

_RAY_COLOR = '#1f77b4'

#: Identifies the ray collection among the axes' artists.
RAYS_GID = 'rays'


def azimuth_for(n_surfaces):
    """Azimuthal samples per surface, coarsening as a design grows."""
    if n_surfaces <= 0:
        return 8
    return int(np.clip(MAX_VERTICES // (n_surfaces * N_AXIAL), 8, 48))


class Layout3DTab(FigurePane):
    """A rotatable view of the optics and the rays through them."""

    def __init__(self, parent=None):
        super(Layout3DTab, self).__init__(parent)
        self.ax = self.figure.add_subplot(111, projection='3d')

        self.mirrors_only = QtWidgets.QCheckBox('Mirrors only')
        self.mirrors_only.setToolTip(
            'Zoom on the optics instead of the whole system, where the z '
            'axis needs little or no compression.')
        self.solid = QtWidgets.QCheckBox('Solid half-shells')
        self.solid.setToolTip(
            'Draw shaded half surfaces rather than wireframes. Half, so that '
            'the rays inside stay visible from any angle.')
        for box in (self.mirrors_only, self.solid):
            box.toggled.connect(self.force_repaint)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.mirrors_only)
        row.addWidget(self.solid)
        row.addStretch(1)
        self.layout().insertLayout(0, row)

    # -- drawing -----------------------------------------------------------

    def _draw(self, result):
        ax = self.ax
        ax.clear()
        if result is None:
            # Before labels and title: a cleared tab keeps nothing.
            return

        system = wolter.build_system(result.params)
        solid = self.solid.isChecked()
        patches = self._patches(system, solid)

        self._draw_surfaces(ax, patches, solid)
        self._draw_rays(ax, result)
        # Zoomed on the optics, the focal plane is metres out of frame; a
        # legend entry for something invisible is worse than no marker.
        if not self.mirrors_only.isChecked():
            self._draw_focus(ax, result, patches)

        self._set_limits(ax, result, patches)
        ax.set_xlabel('x [mm]')
        ax.set_ylabel('y [mm]')
        ax.set_title('%s in 3D' % system.label)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(handles, labels, loc='upper left', fontsize=8)

    def _patches(self, system, solid):
        """Meshes for every element, at a resolution that stays interactive."""
        n_surfaces = max(1, len(system.elements))
        kwargs = dict(n_azimuth=azimuth_for(n_surfaces), num=N_AXIAL)
        if solid:
            # Half a revolution, so there is always an open side facing the
            # camera and the rays inside a shell stay visible.
            kwargs.update(phi0=-np.pi / 2., dphi=np.pi)
        return system.patches(**kwargs)

    @staticmethod
    def _draw_surfaces(ax, patches, solid):
        seen = set()
        for patch in patches:
            style = patch.style
            label = patch.name if patch.name not in seen else None
            seen.add(patch.name)
            if solid:
                # No label on the surface itself: a labelled Poly3DCollection
                # raises inside legend() on matplotlib 3.3, which has no
                # _edgecolors2d for it to read back. The meridian line below
                # carries the legend entry instead, and marks the open edge.
                ax.plot_surface(patch.x, patch.y, patch.z,
                                color=style['color'], alpha=style['alpha'],
                                linewidth=0, antialiased=False, shade=False)
                ax.plot(patch.x[:, 0], patch.y[:, 0], patch.z[:, 0],
                        color=style['color'], lw=style['lw'], label=label)
            else:
                ax.plot_wireframe(patch.x, patch.y, patch.z,
                                  color=style['color'], alpha=style['alpha'],
                                  linewidth=0.5, label=label)

    @staticmethod
    def _draw_rays(ax, result):
        """
        Every drawn ray as one collection, not one artist each.

        mplot3d re-projects per *artist*, so forty ``ax.plot`` calls cost
        forty times the per-frame overhead of a single collection.
        """
        if result.path_x is None:
            return
        # (stage, ray) -> (ray, stage, 3), which is what a segment list is.
        segments = np.stack([result.path_x, result.path_y, result.path_z],
                            axis=-1).transpose(1, 0, 2)
        rays = Line3DCollection(segments, colors=to_rgba(_RAY_COLOR, 0.5),
                                linewidths=0.4)
        # Wireframe surfaces are Line3DCollections too, so tag this one --
        # otherwise nothing downstream can tell the rays from the mirrors.
        rays.set_gid(RAYS_GID)
        ax.add_collection3d(rays)

    @staticmethod
    def _draw_focus(ax, result, patches):
        """A disc at best focus, sized to the optics rather than the spot."""
        if not np.isfinite(result.focus_z) or not patches:
            return
        radius = max(np.hypot(p.x, p.y).max() for p in patches) * 0.25
        phi = np.linspace(0., 2 * np.pi, 64)
        ax.plot(radius * np.cos(phi), radius * np.sin(phi),
                np.repeat(result.focus_z, len(phi)),
                color='crimson', ls='--', lw=1., label='Focus')

    def _set_limits(self, ax, result, patches):
        """
        Equal x and y, compressed z, and a label that admits it.

        ``Axes3D.clear()`` drops both the box aspect and the projection type,
        so they are set here on every draw rather than once at construction.
        """
        xs = [p.x for p in patches]
        ys = [p.y for p in patches]
        if result.path_x is not None:
            xs.append(result.path_x)
            ys.append(result.path_y)
        half = max(np.abs(np.concatenate([a.ravel() for a in xs])).max(),
                   np.abs(np.concatenate([a.ravel() for a in ys])).max())
        half = max(half, 1e-6) * 1.05

        zlo, zhi = self._z_limits(result, patches)
        ax.set_xlim(-half, half)
        ax.set_ylim(-half, half)
        ax.set_zlim(zlo, zhi)

        # Guard rather than require: nothing in this repo pins a matplotlib
        # version, and a missing box aspect is a cosmetic loss, not a crash.
        if hasattr(ax, 'set_box_aspect'):
            ax.set_box_aspect((1., 1., Z_BOX))
        ax.set_proj_type('ortho')

        squash = (zhi - zlo) / (2. * half) / Z_BOX
        # Saying so is not optional: an unlabelled 38:1 squash makes a
        # Wolter-I look like a Cassegrain.
        text = 'z [mm]' if squash <= 1.5 else 'z [mm]  (compressed ×%.0f)' % squash
        ax.set_zlabel(text, labelpad=18)

    def _z_limits(self, result, patches):
        if self.mirrors_only.isChecked():
            span = wolter.mirror_z_range(result.params)
            pad = 0.05 * (span[1] - span[0])
            return span[0] - pad, span[1] + pad
        zs = [p.z.ravel() for p in patches]
        if result.path_z is not None:
            zs.append(result.path_z.ravel())
        allz = np.concatenate(zs)
        lo, hi = float(allz.min()), float(allz.max())
        pad = 0.02 * (hi - lo) or 1.
        return lo - pad, hi + pad
