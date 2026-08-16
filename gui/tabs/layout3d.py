"""
The telescope in three dimensions, drawn with matplotlib.

The 2D layout tab plots radius against z, which is the right picture for a
system that is a surface of revolution and says nothing at all about one that
is not.  Azimuth, decentre and tilt are exactly the quantities it cannot show:
a secondary tilted by 5 arcmin looks, in profile, like a secondary.

This is the *fallback* renderer.  Where pyqtgraph and PyOpenGL are installed
the same scene is drawn by :mod:`PyXFocus.gui.tabs.layout3dgl`, which orbits
smoothly and can close its shells; this one exists so that the tab is never
simply missing.  Both consume a :class:`~PyXFocus.gui.scene3d.Scene`, so the
geometry, the framing and the z compression are decided in one place and
neither tab is in a position to disagree with the other about the telescope.

Two things about mplot3d drive what is left here.

*Occlusion.*  matplotlib 3.3 has no ``computed_zorder``: ``plot_surface``
depth-sorts each surface as a single unit, so an opaque mirror shell swallows
the rays inside it, non-deterministically, as the camera turns.  The default
here is therefore a wireframe, which is see-through by construction.  The
solid alternative draws half a shell, so that there is an open side no matter
where the camera is.  Note that is ``SceneOptions.for_backend`` doing the
work, not a trick played here.

*Aspect.*  A Wolter-I is roughly 8 m long and 20 cm in radius.  Drawn to
scale it is an invisible thread.  The z axis is compressed and the label says
by how much, because an unlabelled 38:1 squash makes a Wolter-I look like a
Cassegrain.  What is never compressed is x against y: azimuth, tilt and
decentre all live in that plane and are the whole reason for this view.
"""

import numpy as np
from mpl_toolkits.mplot3d import Axes3D             # noqa: F401  (projection)
from mpl_toolkits.mplot3d.art3d import Line3DCollection
from PyQt5 import QtWidgets

from PyXFocus.gui import scene3d, wolter
from PyXFocus.gui.tabs.pane import FigurePane

#: How much taller than wide the plot box is.
Z_BOX = scene3d.Z_BOX

#: Identifies the ray collection among the axes' artists.
RAYS_GID = 'rays'


class Layout3DTab(FigurePane):
    """A rotatable view of the optics and the rays through them."""

    #: Which scene budget and shell treatment this tab asks for.
    backend = 'matplotlib'

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
        self.grooves = QtWidgets.QCheckBox('Grooves')
        self.grooves.setToolTip(
            'Draw the grating grooves and the direction its orders disperse '
            'in. Schematic: a real grating has millions of grooves.')
        self.grooves.setChecked(True)
        self.true_scale = QtWidgets.QCheckBox('True scale (1:1)')
        self.true_scale.setToolTip(
            'Give z the same scale as x and y, so nothing is distorted. The '
            'whole system then reads as a thread, so this is for looking '
            'closely at one part of it.')
        self._boxes = (self.mirrors_only, self.solid, self.grooves,
                       self.true_scale)
        for box in self._boxes:
            box.toggled.connect(self.force_repaint)

        row = QtWidgets.QHBoxLayout()
        for box in self._boxes:
            row.addWidget(box)
        row.addStretch(1)
        self.layout().insertLayout(0, row)

    # -- drawing -----------------------------------------------------------

    def options(self):
        return scene3d.SceneOptions.for_backend(
            self.backend, solid=self.solid.isChecked(),
            mirrors_only=self.mirrors_only.isChecked(),
            show_grooves=self.grooves.isChecked(),
            true_scale=self.true_scale.isChecked())

    def _draw(self, result):
        ax = self.ax
        ax.clear()
        if result is None:
            # Before labels and title: a cleared tab keeps nothing.
            return

        system = wolter.build_system(result.params)
        scene = scene3d.build_scene(system, result, self.options())

        for item in scene.items:
            self._draw_item(ax, item)
        self._set_limits(ax, scene)

        ax.set_xlabel('x [mm]')
        ax.set_ylabel('y [mm]')
        ax.set_title(scene.title)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(handles, labels, loc='upper left', fontsize=8)

    @staticmethod
    def _draw_item(ax, item):
        """
        One :class:`~PyXFocus.gui.scene3d.DrawItem`, as matplotlib artists.

        Rays and wireframes are both collections rather than one artist per
        line: mplot3d re-projects per *artist* on every mouse move, so forty
        ``ax.plot`` calls cost forty times the per-frame overhead of one
        collection.
        """
        color = item.colors if np.ndim(item.colors) == 1 else None
        if item.mode in ('mesh', 'grid'):
            grid = item.verts.reshape(item.shape + (3,))
            if item.mode == 'grid':
                ax.plot_wireframe(grid[..., 0], grid[..., 1], grid[..., 2],
                                  color=color[:3], alpha=color[3],
                                  linewidth=0.5, label=item.name)
                return
            # No label on the surface itself: a labelled Poly3DCollection
            # raises inside legend() on matplotlib 3.3, which has no
            # _edgecolors2d for it to read back. The rim line below carries
            # the legend entry instead, and marks the open edge.
            ax.plot_surface(grid[..., 0], grid[..., 1], grid[..., 2],
                            color=color[:3], alpha=color[3], linewidth=0,
                            antialiased=False, shade=False)
            rim = grid[:, 0]
            ax.plot(rim[:, 0], rim[:, 1], rim[:, 2], color=color[:3],
                    lw=item.width, label=item.name)
            return

        if item.mode == 'strip':
            ax.plot(item.verts[:, 0], item.verts[:, 1], item.verts[:, 2],
                    color=color[:3], alpha=color[3], lw=item.width,
                    ls='--' if item.kind == 'focus' else '-',
                    label=item.name)
            return

        segments = item.verts.reshape(-1, 2, 3)
        if color is None:
            colors = item.colors[::2]        # one per segment, from its head
        else:
            colors = [color]
        lines = Line3DCollection(segments, colors=colors,
                                 linewidths=0.4 if item.kind == 'ray'
                                 else 0.5)
        if item.kind == 'ray':
            # Wireframe surfaces are Line3DCollections too, so tag this one
            # -- otherwise nothing downstream can tell the rays from the
            # mirrors.
            lines.set_gid(RAYS_GID)
        else:
            lines.set_label(item.name)
        ax.add_collection3d(lines)

    @staticmethod
    def _set_limits(ax, scene):
        """
        Equal x and y, compressed z, and a label that admits it.

        ``Axes3D.clear()`` drops both the box aspect and the projection type,
        so they are set here on every draw rather than once at construction.
        """
        half, zlo, zhi = scene.view.span_mm
        ax.set_xlim(-half, half)
        ax.set_ylim(-half, half)
        ax.set_zlim(zlo, zhi)

        # Derived, not the Z_BOX constant: the drawn z-to-x ratio has to be
        # whatever undoes the scene's compression, or the picture and the
        # label it carries disagree. Reduces to Z_BOX at the default
        # compression and to the true aspect at 1:1 -- where an 8 m
        # telescope really is drawn twenty times taller than it is wide.
        squash = scene.view.compression
        depth = (zhi - zlo) / (2. * half) / squash

        # Guard rather than require: nothing in this repo pins a matplotlib
        # version, and a missing box aspect is a cosmetic loss, not a crash.
        if hasattr(ax, 'set_box_aspect'):
            ax.set_box_aspect((1., 1., depth))
        ax.set_proj_type('ortho')

        # Saying so is not optional: an unlabelled 38:1 squash makes a
        # Wolter-I look like a Cassegrain.
        text = 'z [mm]' if squash <= 1.5 else \
            'z [mm]  (compressed x%.0f)' % squash
        ax.set_zlabel(text, labelpad=18)
