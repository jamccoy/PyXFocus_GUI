"""
The 3D layout, drawn by the GPU.

The matplotlib version of this tab is in :mod:`PyXFocus.gui.tabs.layout3d`,
and it is the fallback rather than the peer: mplot3d re-projects every vertex
in Python on every mouse move, which measured at about 25 frames a second for
a five-surface system before it had drawn anything, and it has no scroll-wheel
zoom, no working toolbar zoom or pan on a 3D axes, and no clamp on elevation
-- so dragging past the pole turns the scene inside out.  None of that is
matplotlib being careless; a 3D axes is simply not what it is for.

What the GPU buys, beyond the frame rate:

*Closed shells.*  matplotlib 3.3 depth-sorts each surface as a single unit,
so an opaque shell swallows the rays inside it and the fallback has to draw
half a shell to leave an open side.  Here the depth test runs per fragment
while depth *writes* are disabled on surfaces (:data:`SEE_THROUGH`), so a
shell is transparent to what is inside it and still occludes correctly among
the rays.  A whole shell, from any angle.

*A camera that survives a repaint.*  :meth:`_draw` replaces the items and
never touches ``view.opts``, so re-tracing a design leaves the camera exactly
where the user put it.  In the fallback the camera is matplotlib's own state
and a right-drag zoom is silently undone by the next repaint.

Geometry arrives from :mod:`PyXFocus.gui.scene3d` already decided, in global
millimetres, and is squashed into the view box on upload.  That normalisation
is what lets one camera distance be right for a 200 mm test optic and an 8 m
telescope alike, and keeps float32 away from holding 8400.0.
"""

import collections

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from OpenGL.GL import (GL_ALPHA_TEST, GL_BLEND, GL_CULL_FACE, GL_DEPTH_TEST,
                       GL_FALSE, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA)
from PyQt5 import QtCore, QtGui, QtWidgets

from PyXFocus.gui import scene3d, wolter
from PyXFocus.gui.tabs.pane import PaintGate

#: Depth test on, depth writes off.  The test is what makes rays occlude each
#: other properly; disabling the write is what stops a shell from hiding what
#: is inside it.  Together they retire the half-shell workaround the
#: matplotlib tab still needs.
SEE_THROUGH = {
    GL_DEPTH_TEST: True,
    GL_BLEND: True,
    GL_ALPHA_TEST: False,
    GL_CULL_FACE: False,
    'glBlendFunc': (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA),
    'glDepthMask': (GL_FALSE,),
}

#: Named viewpoints.  "Down axis" is the one the fallback effectively cannot
#: reach and the one a nested design most wants: it shows the shells as
#: concentric annuli and a grating's dispersion as a displacement across
#: them.  89.9 rather than 90 because the up vector degenerates exactly at
#: the pole and the azimuth becomes undefined.
CAMERA_PRESETS = collections.OrderedDict([
    ('Iso', dict(elevation=22., azimuth=-60.)),
    ('Down axis', dict(elevation=89.9, azimuth=-90.)),
    ('Side x-z', dict(elevation=0., azimuth=-90.)),
    ('Side y-z', dict(elevation=0., azimuth=0.)),
])

#: Very slightly off white, so that a white grating or detector edge still
#: has something to be seen against.
BACKGROUND = '#f4f4f4'

#: A narrow field of view, because this is a measuring instrument and not a
#: room: at 60 degrees the near end of an 8 m telescope is visibly larger
#: than the far end, which reads as a taper that is not there.  Narrow is as
#: close to the fallback's orthographic projection as GLViewWidget gets.
FOV = 30.

#: Half-extents of the normalised scene box, in view units.
SCENE_BOX = (scene3d.VIEW_SPAN, scene3d.VIEW_SPAN,
             scene3d.Z_BOX * scene3d.VIEW_SPAN)

#: Fallback aspect, for a camera framed before the widget has been laid out.
_NOMINAL_ASPECT = 4. / 3.


def _corners(box):
    a, b, c = box
    return np.array([(sx * a, sy * b, sz * c)
                     for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])


def fit_distance(width, height, elevation=22., azimuth=-60., fov=FOV,
                 box=SCENE_BOX, margin=1.12):
    """
    How far back to sit so the scene box fills this viewport, from here.

    Two things this has to get right, both of which show up immediately as a
    scene running off the edge of the window:

    ``fov`` is pyqtgraph's, and pyqtgraph's is *horizontal* -- the vertical
    field is narrower by the aspect ratio, so framing on the horizontal
    leaves a tall scene overflowing top and bottom.

    The box is 2.2 times taller than it is wide, so how much of the viewport
    it needs depends entirely on where it is seen from.  Projecting the
    corners onto the camera's own axes is what keeps "Down axis" from being
    framed as though it were looking at the long side.
    """
    if width <= 0 or height <= 0:
        width, height = _NOMINAL_ASPECT, 1.
    elev, azim = np.radians(elevation), np.radians(azimuth)
    right = np.array([-np.sin(azim), np.cos(azim), 0.])
    up = np.array([-np.sin(elev) * np.cos(azim),
                   -np.sin(elev) * np.sin(azim), np.cos(elev)])

    corners = _corners(box)
    across = np.abs(corners.dot(right)).max()
    tall = np.abs(corners.dot(up)).max()

    half_width = np.tan(np.radians(fov / 2.))
    half_height = half_width * height / float(width)
    return margin * max(across / half_width, tall / half_height)


#: Camera distance for a nominally shaped viewport at the default view.
DEFAULT_DISTANCE = fit_distance(_NOMINAL_ASPECT, 1.)

#: How far in and out the wheel may go.  A fast scroll on an unclamped
#: GLViewWidget walks the distance through zero and turns the scene inside
#: out, which is unrecoverable without reaching for a preset.
DISTANCE_LIMITS = (0.05 * DEFAULT_DISTANCE, 5. * DEFAULT_DISTANCE)


class GLViewWidget(gl.GLViewWidget):
    """A GLViewWidget whose wheel cannot push the camera through itself."""

    def wheelEvent(self, event):
        super(GLViewWidget, self).wheelEvent(event)
        low, high = DISTANCE_LIMITS
        self.opts['distance'] = float(np.clip(self.opts['distance'],
                                              low, high))
        self.update()


class GLLayout3DTab(PaintGate, QtWidgets.QWidget):
    """A rotatable view of the optics and the rays through them."""

    #: Which scene budget and shell treatment this tab asks for.
    backend = 'opengl'

    def __init__(self, parent=None):
        super(GLLayout3DTab, self).__init__(parent)
        self._init_paint_gate()

        self.view = GLViewWidget()
        # Light, and not GLViewWidget's black. STYLES draws the primary
        # mirrors in #000000 -- chosen for a white matplotlib figure, and
        # simply invisible on a black one. Sharing the palette between the
        # two renderers is worth more than a dark viewport, and it keeps
        # this tab looking like the four beside it.
        self.view.setBackgroundColor(BACKGROUND)
        self.view.opts['fov'] = FOV
        self.view.setCameraPosition(distance=DEFAULT_DISTANCE,
                                    **CAMERA_PRESETS['Iso'])
        #: False until the camera has been framed against a real viewport.
        self._framed = False

        self.mirrors_only = QtWidgets.QCheckBox('Mirrors only')
        self.mirrors_only.setToolTip(
            'Zoom on the optics instead of the whole system, where the z '
            'axis needs little or no compression.')
        self.solid = QtWidgets.QCheckBox('Solid shells')
        self.solid.setToolTip(
            'Draw shaded surfaces rather than wireframes. Whole shells: the '
            'depth test keeps the rays inside them visible.')
        self.grooves = QtWidgets.QCheckBox('Grooves')
        self.grooves.setToolTip(
            'Draw the grating grooves and the direction its orders disperse '
            'in. Schematic: a real grating has millions of grooves.')
        self.grooves.setChecked(True)
        self.by_order = QtWidgets.QCheckBox('Colour by order')
        self.by_order.setToolTip(
            'Colour each ray by the diffraction order it left the grating '
            'in. Warm for positive orders, cool for negative.')
        self.by_order.setChecked(True)
        self._boxes = (self.mirrors_only, self.solid, self.grooves,
                       self.by_order)
        for box in self._boxes:
            box.toggled.connect(self.force_repaint)

        controls = QtWidgets.QHBoxLayout()
        for box in self._boxes:
            controls.addWidget(box)
        controls.addSpacing(12)
        for name in CAMERA_PRESETS:
            button = QtWidgets.QPushButton(name)
            button.setToolTip('Point the camera down %s.' % name.lower())
            # default=False, or Return in a spin box fires whichever of
            # these Qt last decided was the default button.
            button.setAutoDefault(False)
            button.clicked.connect(lambda _, key=name: self.set_camera(key))
            controls.addWidget(button)
        controls.addStretch(1)

        #: Carries the compression factor and the groove disclaimer.  A
        #: QLabel and not a GLTextItem: text in the scene depends on the
        #: pyqtgraph version, and a statement the picture is misleading
        #: without is not something to make version-dependent.
        self.status = QtWidgets.QLabel('')
        self.status.setTextFormat(QtCore.Qt.RichText)
        self.status.setWordWrap(True)

        box = QtWidgets.QVBoxLayout(self)
        box.addLayout(controls)
        box.addWidget(self.view, 1)
        box.addWidget(self.status)

    # -- camera ------------------------------------------------------------

    def set_camera(self, name):
        """
        Point the camera at a named viewpoint, without redrawing.

        Deliberately not a repaint: the scene has not changed, only where it
        is looked at from, and re-uploading every buffer to turn the camera
        is the cost this tab exists to avoid.
        """
        angles = CAMERA_PRESETS[name]
        self.view.setCameraPosition(
            distance=fit_distance(self.view.width(), self.view.height(),
                                  **angles),
            **angles)
        self.view.opts['center'] = pg.Vector(0., 0., 0.)
        self._framed = True
        self.view.update()

    def showEvent(self, event):
        # The construction-time framing was computed against a widget with
        # no size yet.  The first time this has a real viewport, frame it
        # again -- once, so that a user who has since zoomed and switched
        # tabs does not find their view reset for them.
        super(GLLayout3DTab, self).showEvent(event)
        if not self._framed and self.view.width() > 0:
            self.set_camera('Iso')

    # -- drawing -----------------------------------------------------------

    def options(self):
        return scene3d.SceneOptions.for_backend(
            self.backend, solid=self.solid.isChecked(),
            mirrors_only=self.mirrors_only.isChecked(),
            show_grooves=self.grooves.isChecked(),
            color_by_order=self.by_order.isChecked())

    def _draw(self, result):
        # removeItem rather than clear(): clear() has changed shape between
        # pyqtgraph releases, and this is the operation both agree on.
        for item in list(self.view.items):
            self.view.removeItem(item)
        if result is None:
            self.status.setText('')
            return

        system = wolter.build_system(result.params)
        scene = scene3d.build_scene(system, result, self.options())
        for item in scene.items:
            self._upload(item, scene.view)
        self._add_axes(scene.view)
        self.status.setText(self._status_text(scene))

    def _upload(self, item, view):
        """One :class:`~PyXFocus.gui.scene3d.DrawItem` as a GL item."""
        verts = scene3d.apply_view(item.verts, view).astype(np.float32)

        # A tuple for one colour, an array for one per vertex -- and the
        # distinction is load-bearing, not stylistic.  GLLinePlotItem
        # branches on isinstance(color, ndarray): hand it a (4,) *array* for
        # a uniform colour and it takes the per-vertex path, walks four
        # floats across every vertex in the item, and draws a rainbow.
        if np.ndim(item.colors) == 1:
            colors = tuple(float(c) for c in item.colors)
        else:
            colors = np.asarray(item.colors, dtype=np.float32)

        if item.mode == 'mesh':
            mesh = gl.GLMeshItem(
                meshdata=gl.MeshData(vertexes=verts, faces=item.faces),
                smooth=False, shader=None, color=colors)
            mesh.setGLOptions(SEE_THROUGH)
            self.view.addItem(mesh)
            return

        if item.mode == 'grid':
            verts = scene3d.apply_view(
                scene3d.grid_segments(item.verts, item.shape),
                view).astype(np.float32)
            mode = 'lines'
        elif item.mode == 'lines':
            mode = 'lines'
        else:
            mode = 'line_strip'

        lines = gl.GLLinePlotItem(pos=verts, color=colors, mode=mode,
                                  width=max(1., item.width * 0.6),
                                  antialias=True)
        lines.setGLOptions('translucent')
        if item.kind == 'ray':
            # Rays after the optics, so a translucent shell blends over them
            # rather than the other way round.
            lines.setDepthValue(10)
        self.view.addItem(lines)

    def _add_axes(self, view):
        """
        An origin triad whose three arms are the same length in millimetres.

        Not the same length on screen: z is compressed, so an equal-sided
        triad would be a ruler that reads differently depending on which way
        it is held.  Scaling the z arm by the z scale is what makes it a
        ruler at all -- and makes the compression visible as well as stated.
        """
        arm = view.unit_mm
        sx, _, sz = view.scale
        axes = gl.GLAxisItem(size=QtGui.QVector3D(arm * sx, arm * sx,
                                                  arm * sz))
        axes.setGLOptions('translucent')
        self.view.addItem(axes)

    @staticmethod
    def _status_text(scene):
        """
        What the picture cannot say for itself.

        The compression factor above all: an unlabelled 38:1 squash makes a
        Wolter-I look like a Cassegrain.
        """
        parts = ['x : y is 1 : 1',
                 'axis arms %.0f mm' % scene.view.unit_mm]
        parts.extend(scene.notes)
        text = ' &middot; '.join(parts)
        swatches = []
        for label, rgba in scene.legend:
            color = QtGui.QColor.fromRgbF(*rgba[:3]).name()
            swatches.append('<span style="color:%s">&#9632;</span> %s'
                            % (color, label))
        if swatches:
            text += '<br>' + ' &nbsp; '.join(swatches)
        return text
