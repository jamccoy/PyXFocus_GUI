"""
The 3D picture of a system, described without naming a renderer.

Two things draw this scene: an OpenGL tab and a matplotlib one.  They have
almost nothing in common at the API level -- one uploads vertex buffers, the
other hands 2D arrays to ``plot_surface`` -- but the *decisions* are the same
in both, and those decisions are what is worth getting right once:

* which surfaces to mesh and at what resolution,
* that x and y are strictly to the same scale while z is squashed,
* by how much z is squashed, so that a view can admit it,
* which colour a diffraction order is drawn in.

So this module turns a :class:`~PyXFocus.gui.optics.System` and a
:class:`~PyXFocus.gui.optics.TraceResult` into a :class:`Scene`: a flat list
of :class:`DrawItem` records holding nothing but numpy arrays and rgba
tuples.  Neither backend gets to make a geometric decision on its own, which
is the only way two views of the same telescope stay honest about it.

It lives beside ``optics.py`` rather than under ``gui/tabs/`` on purpose:
importing ``gui.tabs`` runs ``matplotlib.use('Qt5Agg')`` and pulls in PyQt5,
and every decision above is meant to be assertable without a GUI anywhere
near it.  (``PyXFocus.surfaces`` imports matplotlib transitively, so what is
promised and tested here is the narrower thing: nothing in this module is
reached for from a renderer.)

Vertices are in **global millimetres**, throughout.  The :class:`ViewTransform`
that squashes them into a view box rides alongside as numbers, applied by the
GL backend, ignored by matplotlib -- which keeps its millimetre tick labels.
"""

import collections

import numpy as np

from PyXFocus.gui import optics


# ---------------------------------------------------------------------------
# What a backend is handed
# ---------------------------------------------------------------------------

#: One thing to draw.
#:
#: ``mode`` is one of
#:
#: ``'lines'``  ``verts`` are consumed as independent *pairs*;
#: ``'strip'``  a single connected polyline;
#: ``'grid'``   a quad grid drawn as a wireframe, flattened C-order;
#: ``'mesh'``   the same grid as filled triangles, indexed by ``faces``.
#:
#: ``colors`` is one ``(4,)`` rgba for the whole item, or an ``(N, 4)``
#: array with one per vertex -- which is how colouring rays by diffraction
#: order gets in without an item per order.
#:
#: ``shape`` is the ``(rows, cols)`` a grid or mesh was flattened from, and
#: None for everything else.  A grid stays a grid rather than being expanded
#: to line pairs here because the two backends want opposite things from it:
#: matplotlib hands the 2D arrays to ``plot_wireframe`` and must re-project
#: every vertex *per frame*, so tripling the count to pre-expand would cost
#: it directly; GL expands once per result and then rotates with a matrix.
DrawItem = collections.namedtuple(
    'DrawItem', 'name kind mode verts faces colors width shape')

#: How to squash global mm into the view box, and the honest report of it.
#:
#: ``scale`` is ``(sx, sy, sz)`` and ``sx == sy`` always -- azimuth, tilt and
#: decentre all live in the x-y plane and are the whole reason for this view,
#: so squashing one against the other would destroy the thing it exists to
#: show.  ``compression`` is how many times harder z was squeezed than x and
#: y; ``unit_mm`` is what one ``VIEW_SPAN`` of view box is worth in mm, for a
#: scale bar or an axis triad.
ViewTransform = collections.namedtuple(
    'ViewTransform', 'center scale compression span_mm unit_mm')

#: Everything a backend needs: items to draw, how to frame them, and the
#: prose that has to appear next to the picture for it not to mislead.
Scene = collections.namedtuple('Scene', 'items view title notes legend')


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

#: Vertices matplotlib is willing to re-project on every mouse move.  mplot3d
#: transforms the whole scene per frame in Python, so this is a frame-rate
#: budget: measured at ~25 fps for a five-surface system, and it degrades
#: linearly.
MAX_VERTICES_MPL = 20000

#: The GL budget is an order of magnitude larger because the cost is a buffer
#: upload once per *result*, not a projection once per frame -- the rotation
#: itself is a matrix the GPU applies.
MAX_VERTICES_GL = 200000

#: Points along each surface's axis.  A Wolter conic sags well under a
#: millimetre over 100 mm against a 220 mm radius, so a handful is plenty and
#: the azimuthal direction is where the vertices are worth spending.
N_AXIAL = 8

#: How much taller than wide the view box is.  Enough to read the system as
#: long without squeezing the mirrors into the axis.
Z_BOX = 2.2

#: Half-width of the view box in view units.  Geometry is normalised into it
#: before it reaches the GPU, which is what makes one camera distance right
#: for every design: a 200 mm test optic and an 8 m telescope arrive at the
#: same size, and nothing is asked to hold 8400.0 in a float32.
VIEW_SPAN = 100.

_RAY_RGBA = (0.12, 0.47, 0.71, 0.5)
_FOCUS_RGBA = (0.86, 0.08, 0.24, 1.0)

#: ``STYLES`` is plain strings so that ``optics`` need not import matplotlib;
#: everything in it is a hex triple except these, so a two-entry table beats
#: taking a matplotlib dependency here to parse them.
_NAMED_COLORS = {'crimson': (0.863, 0.078, 0.235)}


def _rgba(style, alpha=None):
    """``(r, g, b, a)`` floats from one of ``optics.STYLES``' plain dicts."""
    color = style['color']
    if color in _NAMED_COLORS:
        rgb = _NAMED_COLORS[color]
    else:
        text = color.lstrip('#')
        rgb = tuple(int(text[i:i + 2], 16) / 255. for i in (0, 2, 4))
    return rgb + (float(style['alpha'] if alpha is None else alpha),)


def azimuth_for(n_surfaces, max_vertices=MAX_VERTICES_MPL):
    """Azimuthal samples per surface, coarsening as a design grows."""
    if n_surfaces <= 0:
        return 8
    return int(np.clip(max_vertices // (n_surfaces * N_AXIAL), 8, 48))


class SceneOptions(collections.namedtuple(
        'SceneOptions',
        'solid mirrors_only max_vertices phi0 dphi show_grooves '
        'color_by_order true_scale')):
    """
    What a backend wants, expressed as choices rather than as capabilities.

    :meth:`for_backend` is where "GL closes the shells and spends ten times
    the vertices, matplotlib halves them and does not" is written down --
    once, and visibly, instead of drifting apart in two tabs.
    """

    __slots__ = ()

    @classmethod
    def for_backend(cls, backend, solid=False, mirrors_only=False,
                    show_grooves=True, color_by_order=True,
                    true_scale=False):
        if backend == 'opengl':
            # A closed shell, because GL depth-tests per fragment and the
            # tab disables depth *writes* on surfaces: the rays inside stay
            # visible from every angle without cutting the optic in half.
            return cls(solid=solid, mirrors_only=mirrors_only,
                       max_vertices=MAX_VERTICES_GL, phi0=0., dphi=2 * np.pi,
                       show_grooves=show_grooves,
                       color_by_order=color_by_order, true_scale=true_scale)
        # matplotlib 3.3 has no computed_zorder: plot_surface depth-sorts
        # each surface as a single unit, so an opaque shell swallows the rays
        # inside it as the camera turns.  Half a revolution leaves an open
        # side facing the camera wherever it is.
        return cls(solid=solid, mirrors_only=mirrors_only,
                   max_vertices=MAX_VERTICES_MPL,
                   phi0=-np.pi / 2. if solid else 0.,
                   dphi=np.pi if solid else 2 * np.pi,
                   show_grooves=show_grooves, color_by_order=color_by_order,
                   true_scale=true_scale)


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------

#: Diverging, and deliberately not a colormap lookup: the orders are a small
#: signed set, the sign is the physically meaningful half (which way the
#: order disperses), and zero is the undiffracted beam.  Warm for positive,
#: cool for negative, pale for zero.
_ORDER_COLORS = {
    0: (0.60, 0.60, 0.60),
    1: (0.84, 0.19, 0.15), -1: (0.13, 0.44, 0.71),
    2: (0.99, 0.55, 0.24), -2: (0.42, 0.68, 0.84),
    3: (0.96, 0.76, 0.28), -3: (0.65, 0.81, 0.89),
}
_ORDER_FALLBACK = ((0.55, 0.11, 0.09), (0.03, 0.19, 0.42))


def color_for_order(m, alpha=0.55):
    """rgba for diffraction order ``m``; warm positive, cool negative."""
    m = int(m)
    if m in _ORDER_COLORS:
        return _ORDER_COLORS[m] + (float(alpha),)
    return _ORDER_FALLBACK[m < 0] + (float(alpha),)


# ---------------------------------------------------------------------------
# Meshes
# ---------------------------------------------------------------------------

def grid_faces(n_rows, n_cols):
    """
    Triangles covering an ``n_rows`` x ``n_cols`` quad grid, C-ordered.

    Two per quad, indexing into ``verts.ravel()`` order -- the flattening
    a :class:`~PyXFocus.gui.optics.Patch` gets when its three 2D arrays are
    stacked into one vertex list.
    """
    if n_rows < 2 or n_cols < 2:
        return np.zeros((0, 3), dtype=np.int32)
    corner = np.arange((n_rows - 1) * (n_cols - 1)).reshape(n_rows - 1,
                                                           n_cols - 1)
    # From "index among the quads" to "index among the vertices": every row
    # of quads is one vertex short of a row of vertices.
    corner = corner + corner // (n_cols - 1)
    lower = np.stack([corner, corner + 1, corner + n_cols], axis=-1)
    upper = np.stack([corner + 1, corner + n_cols + 1, corner + n_cols],
                     axis=-1)
    return np.concatenate([lower.reshape(-1, 3),
                           upper.reshape(-1, 3)]).astype(np.int32)


def _mesh_item(patch):
    verts = np.stack([patch.x.ravel(), patch.y.ravel(), patch.z.ravel()],
                     axis=-1)
    return DrawItem(patch.name, patch.kind, 'mesh', verts,
                    grid_faces(*patch.x.shape), _rgba(patch.style),
                    float(patch.style['lw']), patch.x.shape)


def _wireframe_item(patch):
    """A patch as a quad grid, left unexpanded -- see :class:`DrawItem`."""
    verts = np.stack([patch.x.ravel(), patch.y.ravel(), patch.z.ravel()],
                     axis=-1)
    return DrawItem(patch.name, patch.kind, 'grid', verts, None,
                    _rgba(patch.style), 1.0, patch.x.shape)


def grid_segments(verts, shape):
    """
    A ``'grid'`` item's vertices as independent pairs, both directions.

    For a backend that draws line pairs rather than quad grids.  Done once
    per result there, which is why it is not done for everyone here.
    """
    grid = np.asarray(verts, float).reshape(shape + (3,))
    pairs = []
    if grid.shape[1] > 1:                                    # along each row
        pairs.append(np.stack([grid[:, :-1], grid[:, 1:]], axis=-2))
    if grid.shape[0] > 1:                                    # down each column
        pairs.append(np.stack([grid[:-1, :], grid[1:, :]], axis=-2))
    if not pairs:
        return np.zeros((0, 3))
    return np.concatenate([p.reshape(-1, 3) for p in pairs])


def _polyline_item(line):
    verts = np.stack([np.asarray(line.x, float).ravel(),
                      np.asarray(line.y, float).ravel(),
                      np.asarray(line.z, float).ravel()], axis=-1)
    return DrawItem(line.name, line.kind, 'strip', verts, None,
                    _rgba(line.style, alpha=0.9), float(line.style['lw']),
                    None)


def _clip_to_z(head, tail, z_span):
    """
    Trim segments to ``z_span``, moving the cut ends rather than dropping.

    Dropping whole segments is not enough: the run from the source to the
    primary is metres long and overlaps a 200 mm mirror span at one end, so
    keeping it whole draws eight metres of ray across a view scaled to the
    optics.  Returns ``(head, tail, keep)``, the mask being what any
    parallel per-segment array has to be filtered by.
    """
    lo, hi = z_span
    keep = ((np.maximum(head[:, 2], tail[:, 2]) >= lo)
            & (np.minimum(head[:, 2], tail[:, 2]) <= hi))
    head, tail = head[keep].copy(), tail[keep].copy()

    for point, other in ((head, tail), (tail, head)):
        dz = other[:, 2] - point[:, 2]
        # A segment lying in the plane has nothing to interpolate along and
        # is already inside the span, both of its ends having passed above.
        flat = dz == 0.
        for bound, outside in ((lo, point[:, 2] < lo), (hi, point[:, 2] > hi)):
            cut = outside & ~flat
            if not cut.any():
                continue
            t = ((bound - point[cut, 2]) / dz[cut])[:, None]
            point[cut] = point[cut] + t * (other[cut] - point[cut])
    return head, tail, keep


def _ray_item(result, options, z_span=None):
    """
    Every drawn ray as one item, with a colour per vertex.

    ``'lines'`` consumes vertices in *pairs*, so a path of S stages becomes
    S-1 pairs.  The interleave below and the colour tiling must agree about
    ordering: both run stage-major, ray-minor.

    ``z_span`` drops segments outside it.  matplotlib gets this for free
    from its axis limits; GL has no limits to clip against, so zooming on
    the optics would otherwise leave eight metres of ray drawn across a view
    scaled to two hundred millimetres of mirror.
    """
    if result.path_x is None:
        return None
    paths = np.stack([result.path_x, result.path_y, result.path_z],
                     axis=-1)                                 # (S, R, 3)
    if paths.shape[0] < 2:
        return None
    head, tail = paths[:-1], paths[1:]                        # (S-1, R, 3)

    orders = getattr(result, 'path_orders', None)
    if orders is None or not options.color_by_order:
        per_ray = None
        colors = _RAY_RGBA
    else:
        per_ray = np.array([color_for_order(m) for m in orders])  # (R, 4)
        # Stage-major to match the vertices below.
        colors = np.tile(per_ray, (head.shape[0], 1))

    head, tail = head.reshape(-1, 3), tail.reshape(-1, 3)
    if z_span is not None:
        head, tail, keep = _clip_to_z(head, tail, z_span)
        if per_ray is not None:
            colors = colors[keep]
    if not len(head):
        return None

    verts = np.empty((2 * len(head), 3))
    verts[0::2] = head
    verts[1::2] = tail
    if per_ray is not None:
        colors = np.repeat(colors, 2, axis=0)      # two vertices per segment
    return DrawItem('Rays', 'ray', 'lines', verts, None, colors, 1.0, None)


def _focus_item(result, patches):
    """A disc at best focus, sized to the optics rather than to the spot."""
    if not np.isfinite(result.focus_z) or not patches:
        return None
    radius = max(np.hypot(p.x, p.y).max() for p in patches) * 0.25
    phi = np.linspace(0., 2 * np.pi, 64)
    verts = np.stack([radius * np.cos(phi), radius * np.sin(phi),
                      np.repeat(float(result.focus_z), len(phi))], axis=-1)
    return DrawItem('Focus', 'focus', 'strip', verts, None, _FOCUS_RGBA, 1.0,
                    None)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def view_transform(patches, paths, z_span, z_box=Z_BOX, span=VIEW_SPAN,
                   isotropic=False):
    """
    How to squash this system into the view box, and by how much.

    x and y share one scale factor -- not approximately, exactly -- because
    azimuth, tilt and decentre all live in that plane and are the whole
    reason for a 3D view of a system of revolution.  z gets its own, because
    a Wolter-I is 8 m long and 20 cm across and drawn to scale it is an
    invisible thread.

    ``compression`` is the ratio between them, which is the number a view has
    to print next to its z axis: an unlabelled 38:1 squash makes a Wolter-I
    look like a Cassegrain.

    ``isotropic`` gives z the same scale as x and y, so nothing is distorted
    and a convergence angle measured on screen is the real one.  The whole
    system is then an invisible thread, which is exactly why it is not the
    default -- it is for looking closely at one part, not at everything.
    """
    values_x = [np.abs(p.x).max() for p in patches]
    values_y = [np.abs(p.y).max() for p in patches]
    if paths is not None:
        values_x.append(np.abs(paths[0]).max())
        values_y.append(np.abs(paths[1]).max())
    half_xy = max(max(values_x or [0.]), max(values_y or [0.]), 1e-6) * 1.05

    zlo, zhi = z_span
    if not (zhi > zlo):
        zhi = zlo + 1.
    zmid = 0.5 * (zlo + zhi)
    zhalf = 0.5 * (zhi - zlo)

    sxy = span / half_xy
    # `is` the same object, not merely equal: the compression below divides
    # them, and a view claiming "1 : 1" must be exactly that.
    sz = sxy if isotropic else (z_box * span) / zhalf
    return ViewTransform(center=(0., 0., zmid),
                         scale=(sxy, sxy, sz),
                         compression=sxy / sz,
                         span_mm=(half_xy, zlo, zhi),
                         unit_mm=half_xy)


def apply_view(verts, view):
    """Global mm to view units. The GL backend's one geometric step."""
    return (np.asarray(verts, float) - np.asarray(view.center)) \
        * np.asarray(view.scale)


# ---------------------------------------------------------------------------
# The scene
# ---------------------------------------------------------------------------

def _z_span(system, result, patches, options):
    if options.mirrors_only:
        span = system.mirror_z_range()
        if span is not None:
            pad = 0.05 * (span[1] - span[0]) or 1.
            return span[0] - pad, span[1] + pad
    values = [p.z.ravel() for p in patches]
    if result is not None and result.path_z is not None:
        values.append(result.path_z.ravel())
    if not values:
        return 0., 1.
    allz = np.concatenate(values)
    lo, hi = float(allz.min()), float(allz.max())
    return lo - (0.02 * (hi - lo) or 1.), hi + (0.02 * (hi - lo) or 1.)


def build_scene(system, result, options):
    """
    Everything to draw for ``system`` and ``result``, in draw order.

    Surfaces first, then decorations, then rays, then the focus marker, so a
    backend that respects list order gets the rays over the optics without
    having to know which is which.
    """
    n_surfaces = max(1, len(system.elements))
    patches = system.patches(
        n_azimuth=azimuth_for(n_surfaces, options.max_vertices),
        num=N_AXIAL, phi0=options.phi0, dphi=options.dphi)

    items = [(_mesh_item if options.solid else _wireframe_item)(p)
             for p in patches]

    notes = []
    if options.show_grooves:
        for line in system.polylines():
            items.append(_polyline_item(line))
        notes.extend(_groove_notes(system))

    z_span = _z_span(system, result, patches, options)

    ray = None
    if result is not None:
        # Clipped when zoomed on the optics, so the framing below measures
        # the rays that are actually drawn rather than eight metres of ray
        # that is not.
        ray = _ray_item(result, options,
                        z_span if options.mirrors_only else None)
        if ray is not None:
            items.append(ray)

    # Zoomed on the optics the focal plane is metres out of frame, and a
    # legend entry for something invisible is worse than no marker.
    if result is not None and not options.mirrors_only:
        focus = _focus_item(result, patches)
        if focus is not None:
            items.append(focus)

    paths = None if ray is None else (ray.verts[:, 0], ray.verts[:, 1])
    view = view_transform(patches, paths, z_span,
                          isotropic=options.true_scale)
    if view.compression > 1.5:
        notes.append('z compressed x%.0f' % view.compression)

    return Scene(items=items, view=view,
                 title='%s in 3D' % system.label,
                 notes=notes,
                 legend=_legend(items, result, options))


def _groove_notes(system):
    """
    Say that the grooves are a direction cue, not a depiction.

    At a 200 nm period a 240 mm grating carries over a million grooves.
    Drawing nine of them is the only option; letting the picture imply a
    coarse grating is not.
    """
    notes = []
    for element in system.elements:
        period = getattr(element, 'period', None)
        half = getattr(element, 'half_width', None)
        if period and half and np.isfinite(period):
            notes.append('grooves are schematic: %d lines for ~%.0e real '
                         'ones (period %g nm)'
                         % (optics.GROOVE_LINES, 2e6 * half / period, period))
        elif getattr(element, 'dpermm', None) and half:
            notes.append('grooves are schematic: %d lines, converging on the '
                         'hub' % optics.GROOVE_LINES)
    return notes


def _legend(items, result, options):
    """``[(label, rgba), ...]``, one entry per distinct thing on screen."""
    out, seen = [], set()
    orders = getattr(result, 'order_values', None) if result is not None \
        else None
    for item in items:
        if item.kind == 'ray':
            if orders is not None and options.color_by_order:
                for m in orders:
                    label = 'm = %+d' % m if m else 'm = 0'
                    if label not in seen:
                        seen.add(label)
                        out.append((label, color_for_order(m)))
            elif 'Rays' not in seen:
                seen.add('Rays')
                out.append(('Rays', _RAY_RGBA))
            continue
        if item.name not in seen:
            seen.add(item.name)
            out.append((item.name, item.colors if np.ndim(item.colors) == 1
                        else _RAY_RGBA))
    return out
