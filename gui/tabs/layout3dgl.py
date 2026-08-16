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


def camera_basis(elevation, azimuth):
    """
    The camera's ``(right, up)`` axes in world space, from its two angles.

    One definition, used by everything that needs to know where the screen's
    x and y point: framing a box, zooming toward the cursor, and mapping a
    dragged rectangle back to the scene.  Written once because the sign
    conventions here are easy to get subtly wrong and impossible to notice
    from a still picture -- the error shows up only as a view that drifts in
    the wrong direction under the mouse.
    """
    elev, azim = np.radians(elevation), np.radians(azimuth)
    right = np.array([-np.sin(azim), np.cos(azim), 0.])
    up = np.array([-np.sin(elev) * np.cos(azim),
                   -np.sin(elev) * np.sin(azim), np.cos(elev)])
    return right, up


def half_extents(distance, width, height, fov=FOV):
    """
    Half the width and height the viewport covers, in world units.

    ``fov`` is pyqtgraph's, and pyqtgraph's is *horizontal*: the vertical
    field is narrower by the aspect ratio.  Framing on the horizontal is
    what once left the default view running off the top and bottom of the
    window, so every caller goes through here.
    """
    if width <= 0 or height <= 0:
        width, height = _NOMINAL_ASPECT, 1.
    half_w = distance * np.tan(np.radians(fov / 2.))
    return half_w, half_w * height / float(width)


def world_at(center, elevation, azimuth, distance, width, height, x, y,
             fov=FOV):
    """
    Where a pixel points, on the plane through ``center`` facing the camera.

    That plane, and not the geometry itself, because nothing here is picked:
    a depth-buffer read would give the surface under the cursor, which is
    both slower and wrong when the cursor is over empty space.  Every 3D
    application that zooms toward the pointer without a selection does this.
    """
    right, up = camera_basis(elevation, azimuth)
    half_w, half_h = half_extents(distance, width, height, fov)
    ndc_x = 2. * x / float(width) - 1.
    ndc_y = 1. - 2. * y / float(height)
    return np.asarray(center, dtype=float) + right * ndc_x * half_w \
        + up * ndc_y * half_h


def rect_fraction(rect_width, rect_height, view_width, view_height):
    """
    How much of the viewport a dragged rectangle occupies.

    The *larger* of the two fractions, because the box has to fit both ways
    round: fitting the width alone spills a tall box off the top and bottom,
    which is the same horizontal-field trap that pyqtgraph's fov sets
    everywhere else in this file.
    """
    if view_width <= 0 or view_height <= 0:
        return 1.
    return max(rect_width / float(view_width),
               rect_height / float(view_height))


def zoom_about(center, cursor_point, old_distance, new_distance):
    """
    The new view centre that keeps ``cursor_point`` where it is on screen.

    Screen position scales linearly with distance about the centre, so
    holding a point fixed is just moving the centre toward it in the same
    ratio.  This is the whole of zoom-toward-cursor.
    """
    if old_distance <= 0.:
        return np.asarray(center, dtype=float)
    ratio = new_distance / old_distance
    cursor_point = np.asarray(cursor_point, dtype=float)
    return cursor_point + (np.asarray(center, dtype=float) - cursor_point) \
        * ratio


def fit_distance(width, height, elevation=22., azimuth=-60., fov=FOV,
                 box=None, margin=1.12):
    """
    How far back to sit so the scene box fills this viewport, from here.

    The box is taller than it is wide -- 2.2 times so under the default
    compression, and far more than that at true scale -- so how much of the
    viewport it needs depends entirely on where it is seen from.  Projecting
    the corners onto the camera's own axes is what keeps "Down axis" from
    being framed as though it were looking at the long side.

    ``box`` defaults to the compressed scene box, but a caller that has a
    :class:`~PyXFocus.gui.scene3d.ViewTransform` should pass
    :func:`scene_box` for it: under true scale the z half-extent is not
    ``Z_BOX * VIEW_SPAN`` at all, and framing against the constant would put
    the camera an order of magnitude too close.
    """
    box = SCENE_BOX if box is None else box
    right, up = camera_basis(elevation, azimuth)
    corners = _corners(box)
    across = np.abs(corners.dot(right)).max()
    tall = np.abs(corners.dot(up)).max()

    half_w, half_h = half_extents(1., width, height, fov)
    return margin * max(across / half_w, tall / half_h)


def scene_box(view):
    """
    Half-extents of what a :class:`ViewTransform` actually produces.

    Not the SCENE_BOX constant: x and y are always normalised to VIEW_SPAN,
    but z is whatever the z scale makes of the system's real length -- 220
    view units compressed, some 3600 at true scale for an 8 m telescope.
    """
    half_xy, zlo, zhi = view.span_mm
    return (scene3d.VIEW_SPAN, scene3d.VIEW_SPAN,
            0.5 * (zhi - zlo) * view.scale[2])


#: Camera distance for a nominally shaped viewport at the default view.
DEFAULT_DISTANCE = fit_distance(_NOMINAL_ASPECT, 1.)

#: The closest the camera may sit, and it is a guard against degenerate
#: arithmetic rather than a usability limit.
#:
#: There used to be a limit here of 0.05 * DEFAULT_DISTANCE, on the stated
#: grounds that a fast scroll would otherwise "walk the distance through zero
#: and turn the scene inside out".  That was simply wrong: pyqtgraph zooms
#: multiplicatively -- ``opts['distance'] *= 0.999**delta`` -- so the distance
#: approaches zero asymptotically and can never reach it, let alone cross it.
#: What the limit did do was stop the view 51 mm across on the default design.
#: That is barely wider than the 30 mm diffraction fan itself, and some four
#: thousand times too coarse to look *into* one order: each order's spot is
#: about 4 microns across (HPD 0.09 arcsec over an 8.4 m focal length).
#: Inspecting a spot is the whole reason to zoom.
#:
#: Nor is precision a reason to stop early.  Vertices are uploaded as float32
#: at magnitudes up to a few hundred view units, which resolves to roughly
#: 61 nm in x and y -- four orders of magnitude finer than anything worth
#: looking at here.
MIN_DISTANCE = 1e-6 * DEFAULT_DISTANCE


class GLViewWidget(gl.GLViewWidget):
    """
    A GLViewWidget that zooms toward the pointer and says when it moved.

    pyqtgraph scales ``distance`` about ``opts['center']``, so zooming in on
    anything off-centre pushes it out of frame and you chase it across the
    window.  Zooming about the point under the cursor is the single change
    that most makes a 3D view feel like a CAD one.
    """

    #: Emitted whenever the camera moves, so a readout can follow it.
    #: GLViewWidget itself is silent about this.
    cameraChanged = QtCore.pyqtSignal()

    def __init__(self, *args, **kwargs):
        super(GLViewWidget, self).__init__(*args, **kwargs)
        #: A QRubberBand parented to this widget. Qt composites child widgets
        #: over a QOpenGLWidget, so the box needs no GL drawing of its own.
        self._band = QtWidgets.QRubberBand(
            QtWidgets.QRubberBand.Rectangle, self)
        self._box_origin = None
        #: Set by the toolbar toggle, so the box is reachable without
        #: knowing about the shift chord.
        self.zoom_box_armed = False

    def wheelEvent(self, event):
        before = float(self.opts['distance'])
        position = event.position() if hasattr(event, 'position') \
            else event.posF()
        cursor = world_at(self.opts['center'], self.opts['elevation'],
                          self.opts['azimuth'], before,
                          max(1, self.width()), max(1, self.height()),
                          position.x(), position.y(), self.opts['fov'])

        super(GLViewWidget, self).wheelEvent(event)

        after = max(MIN_DISTANCE, float(self.opts['distance']))
        self.opts['distance'] = after
        self.opts['center'] = pg.Vector(
            *zoom_about(self.opts['center'], cursor, before, after))
        self.update()
        self.cameraChanged.emit()

    # -- zoom box ----------------------------------------------------------

    #: Shorter than this, in pixels, and a drag was a click. Without it a
    #: stray shift-click zooms to a point, which is unrecoverable except by
    #: reaching for a preset.
    MIN_BOX_PIXELS = 8

    def _zooming(self, event):
        """True when this event asks for a zoom box rather than an orbit."""
        shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        return (self.zoom_box_armed or shift) \
            and event.buttons() & QtCore.Qt.LeftButton

    def mousePressEvent(self, event):
        if self._zooming(event):
            self._box_origin = event.pos()
            self._band.setGeometry(QtCore.QRect(self._box_origin,
                                                QtCore.QSize()))
            self._band.show()
            event.accept()
            return
        super(GLViewWidget, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._box_origin is not None:
            self._band.setGeometry(
                QtCore.QRect(self._box_origin, event.pos()).normalized())
            event.accept()
            return
        super(GLViewWidget, self).mouseMoveEvent(event)
        self.cameraChanged.emit()

    def mouseReleaseEvent(self, event):
        if self._box_origin is None:
            super(GLViewWidget, self).mouseReleaseEvent(event)
            return
        rect = QtCore.QRect(self._box_origin, event.pos()).normalized()
        self._box_origin = None
        self._band.hide()
        if min(rect.width(), rect.height()) >= self.MIN_BOX_PIXELS:
            self.zoom_to_rect(rect)
        event.accept()

    def zoom_to_rect(self, rect):
        """Frame a screen rectangle: put its centre in view and fill to it."""
        width, height = max(1, self.width()), max(1, self.height())
        distance = float(self.opts['distance'])
        center = world_at(self.opts['center'], self.opts['elevation'],
                          self.opts['azimuth'], distance, width, height,
                          rect.center().x(), rect.center().y(),
                          self.opts['fov'])

        fraction = rect_fraction(rect.width(), rect.height(), width, height)
        self.opts['center'] = pg.Vector(*center)
        self.opts['distance'] = max(MIN_DISTANCE, distance * fraction)
        self.update()
        self.cameraChanged.emit()


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
        self.true_scale = QtWidgets.QCheckBox('True scale (1:1)')
        self.true_scale.setToolTip(
            'Give z the same scale as x and y, so nothing is distorted and a '
            'convergence angle on screen is the real one. The whole system '
            'then reads as a thread, so this is for looking closely at one '
            'part of it.')
        self._boxes = (self.mirrors_only, self.solid, self.grooves,
                       self.by_order, self.true_scale)
        for box in self._boxes:
            box.toggled.connect(self.force_repaint)

        #: Arms the zoom box without needing the shift chord, which nothing
        #: on screen would otherwise advertise.
        self.zoom_box = QtWidgets.QToolButton()
        self.zoom_box.setText('Zoom box')
        self.zoom_box.setCheckable(True)
        self.zoom_box.setToolTip(
            'Drag a rectangle to zoom to it. Shift and drag does the same '
            'without arming this.')
        self.zoom_box.toggled.connect(self._arm_zoom_box)

        controls = QtWidgets.QHBoxLayout()
        for box in self._boxes:
            controls.addWidget(box)
        controls.addWidget(self.zoom_box)
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

        #: The ViewTransform the items on screen were built with. Needed
        #: outside _draw, because framing and the scale readout both have to
        #: turn view units back into millimetres.
        self._view = None
        self._notes = []
        self.view.cameraChanged.connect(self._refresh_status)

    # -- camera ------------------------------------------------------------

    def _arm_zoom_box(self, on):
        self.view.zoom_box_armed = bool(on)
        self.view.setCursor(QtCore.Qt.CrossCursor if on
                            else QtCore.Qt.ArrowCursor)

    def set_camera(self, name):
        """
        Point the camera at a named viewpoint, without redrawing.

        Deliberately not a repaint: the scene has not changed, only where it
        is looked at from, and re-uploading every buffer to turn the camera
        is the cost this tab exists to avoid.
        """
        angles = CAMERA_PRESETS[name]
        # The *drawn* box, not the SCENE_BOX constant. Under true scale the
        # z half-extent is the system's real length times the x scale --
        # thousands of view units, not 220 -- and framing against the
        # constant would leave the camera an order of magnitude too close.
        box = None if self._view is None else scene_box(self._view)
        self.view.setCameraPosition(
            distance=fit_distance(self.view.width(), self.view.height(),
                                  box=box, **angles),
            **angles)
        self.view.opts['center'] = pg.Vector(0., 0., 0.)
        self._framed = True
        self.view.update()
        self._refresh_status()

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
            color_by_order=self.by_order.isChecked(),
            true_scale=self.true_scale.isChecked())

    def _draw(self, result):
        # removeItem rather than clear(): clear() has changed shape between
        # pyqtgraph releases, and this is the operation both agree on.
        for item in list(self.view.items):
            self.view.removeItem(item)
        if result is None:
            self._view = None
            self.status.setText('')
            return

        system = wolter.build_system(result.params)
        scene = scene3d.build_scene(system, result, self.options())
        for item in scene.items:
            self._upload(item, scene.view)
        self._add_axes(scene.view)

        rescaled = (self._view is None
                    or self._view.scale != scene.view.scale)
        self._view, self._notes = scene.view, list(scene.notes)
        self._legend = scene.legend
        # Toggling true scale changes the shape of the box by a factor of
        # ten or more, so the old camera distance frames nothing at all.
        if rescaled and self._framed:
            self.set_camera('Iso')
        self._refresh_status()

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

    def view_width_mm(self):
        """
        How wide the viewport is, in millimetres of telescope.

        The one number that makes deep zoom trustworthy.  Past the axis
        triad there is otherwise nothing on screen to say whether you are
        looking at fifty millimetres or fifty microns, and the answer
        changes by five orders of magnitude across the wheel's range.
        """
        if self._view is None:
            return None
        half_w, _ = half_extents(float(self.view.opts['distance']),
                                 max(1, self.view.width()),
                                 max(1, self.view.height()),
                                 self.view.opts['fov'])
        return 2. * half_w / self._view.scale[0]

    @staticmethod
    def format_mm(mm):
        """
        A width in the unit a reader would actually use for it.

        ``(limit, per_mm, unit)``, and ``per_mm`` is how many of that unit
        make a millimetre -- 1000 microns, a thousandth of a metre.  Worth
        spelling out because the first version of this table multiplied a
        sub-millimetre width by 1e3 and then labelled it "mm", so a view
        1.77 microns across reported itself as 1.77 mm.  A scale readout
        that is wrong by a factor of a thousand is worse than none.
        """
        if mm is None or not np.isfinite(mm) or mm <= 0.:
            return ''
        for limit, per_mm, unit in ((1e-3, 1e6, 'nm'),
                                    (1., 1e3, '&micro;m'),
                                    (1e3, 1., 'mm')):
            if mm < limit:
                return '%.3g %s' % (mm * per_mm, unit)
        return '%.3g m' % (mm * 1e-3)

    def _refresh_status(self):
        self.status.setText(self._status_text())

    def _status_text(self):
        """
        What the picture cannot say for itself.

        The compression factor above all: an unlabelled 38:1 squash makes a
        Wolter-I look like a Cassegrain.
        """
        if self._view is None:
            return ''
        scale = ('x : y : z is 1 : 1 : 1' if self._view.compression == 1.
                 else 'x : y is 1 : 1')
        parts = [scale, 'axis arms %.0f mm' % self._view.unit_mm]
        width = self.view_width_mm()
        if width:
            parts.append('view is %s across' % self.format_mm(width))
        parts.extend(self._notes)
        text = ' &middot; '.join(parts)
        swatches = []
        for label, rgba in getattr(self, '_legend', ()):
            color = QtGui.QColor.fromRgbF(*rgba[:3]).name()
            swatches.append('<span style="color:%s">&#9632;</span> %s'
                            % (color, label))
        if swatches:
            text += '<br>' + ' &nbsp; '.join(swatches)
        return text
