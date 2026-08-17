"""
An optical system as a list of elements, rather than a hard-coded procedure.

The explorer began as one function that traced one Wolter-I shell: source,
primary, secondary, focus, in a straight line with the surface calls written
out by hand.  That is a fine shape for one telescope and a dead end for two.
This module is the general form of it.

An *element* is one optic.  It answers to two audiences and nothing else:

* :meth:`Element.apply` puts it in the beam's way -- push its frame, hit the
  surface, cut what missed, pop the frame, record where the rays landed.
* :meth:`Element.profile` and :meth:`Element.patches` hand back its geometry
  so a viewer can draw it without being told what kind of optic it is.

That second audience is the reason this module exists in the shape it does.
The 2D layout tab used to call ``wolter.mirror_profile``, which knew there
were exactly two mirrors and that both were Wolter conics.  A drawing routine
built on that has to be rewritten for every new optic.  A drawing routine
built on :meth:`Element.patches` does not.

A :class:`System` is an ordered list of elements plus a terminator.  It may be
split into parallel *channels* -- independent bundles that share a focal
plane.  Nested mirror shells are channels: each shell gets its own source and
its own pair of surfaces, and the survivors merge just before the terminator.
Modelling a ray that leaves one shell and strikes another would need a
branching tracer; that is deliberately not what this is.

This module is Qt-free and matplotlib-free, and must stay that way -- see
``test_qt_free_modules_stay_qt_free``.  Styling is carried as plain dicts of
strings and floats so that geometry can be generated, and tested, with no GUI
anywhere in sight.

Conventions are inherited from PyXFocus and unchanged: lengths in mm, angles
in radians once they reach a :class:`Placement`, and ``rays`` is a list of ten
float64 arrays ``[opd, x, y, z, l, m, n, ux, uy, uz]``.
"""

import collections

import numpy as np

import PyXFocus.analyses as anal
import PyXFocus.surfaces as surf
import PyXFocus.transformations as tran

_ARCSEC_PER_RAD = 180. / np.pi * 3600.


# ---------------------------------------------------------------------------
# Rays and their identities
# ---------------------------------------------------------------------------

class Beam(object):
    """
    Rays, and the launch id of each, which are only ever cut together.

    This class exists to make one specific bug unwriteable.  ``tran.vignette``
    does not vignette in place -- it returns a *new* ten-array list -- while
    the Fortran surface routines mutate the list they are given.  So any code
    holding a reference to ``rays`` across a cut is reading a stale array, and
    any code tracking ray identities alongside ``rays`` has to remember to
    re-index its own array at every single cut site.  The flat pipeline this
    replaced had four such sites and got one of them wrong: see the
    :func:`stack_paths` docstring for what that cost.

    Here there is exactly one way for rays to leave a beam -- :meth:`cut` --
    and it re-indexes the ids itself.  The rule for anything outside this
    class is short: never rebind ``.rays``.
    """

    __slots__ = ('rays', 'ids', 'orders', 'waves', 'stride', 'reference')

    def __init__(self, rays, ids=None, orders=None, waves=None, stride=1,
                 reference=None):
        self.rays = rays
        self.ids = np.arange(len(rays[1])) if ids is None else ids
        #: Diffraction order and wavelength per ray, or None.
        #:
        #: None, rather than an array of ones, until a grating actually fans
        #: this beam.  That is what keeps a system with no grating -- and one
        #: tracing a single order -- allocating nothing and producing the
        #: same numbers it always has.
        self.orders = orders
        self.waves = waves
        #: Ids are handed out in blocks of ``stride``, one slot per order in
        #: flight, so that a fan fills slots inside a block and leaves the
        #: ordering every ``searchsorted`` downstream depends on intact.
        self.stride = stride
        #: The order every metric is measured on.
        self.reference = reference

    def __len__(self):
        return len(self.rays[1])

    @property
    def fanned(self):
        """True once a grating has split this beam into several orders."""
        return self.orders is not None

    def cut(self, keep):
        """Drop rays where ``keep`` is False, ids included."""
        self.rays = tran.vignette(self.rays, ind=keep)
        self.ids = self.ids[keep]
        if self.orders is not None:
            self.orders = self.orders[keep]
            self.waves = self.waves[keep]

    def fan(self, orders, wavelength, substride):
        """
        One ray in, ``len(orders)`` out: same point, one copy per order.

        Ids stay strictly increasing because a source hands them out in
        blocks of :attr:`stride` and this only ever fills slots inside a
        block.  :func:`merge`, :func:`stack_paths` and :func:`choose_paths`
        all reach for ``searchsorted``, so that is a contract and not an
        implementation detail.
        """
        count, width = len(self), len(orders)
        self.rays = [np.repeat(component, width) for component in self.rays]
        self.ids = (np.repeat(self.ids, width)
                    + np.tile(np.arange(width) * substride, count))
        self.orders = np.tile(np.asarray(orders, dtype=float), count)
        self.waves = np.full(len(self.ids), float(wavelength))

    def reference_mask(self):
        """Which rays are in the order the metrics are measured on."""
        if not self.fanned or self.reference is None:
            return None
        return self.orders == self.reference

    def focus_weights(self):
        """
        0/1 weights so best focus is found from the reference order alone.

        None when the beam was never fanned, and deliberately not an array
        of ones: ``np.average(a, weights=None)`` and ``np.average(a,
        weights=ones)`` are not obliged to agree in the last bit, and the
        parity table asserts equality rather than closeness.

        Without this, adding an order purely to look at it would drag best
        focus towards the dispersed spots and quietly move every metric.
        """
        mask = self.reference_mask()
        return None if mask is None else mask.astype(float)

    def drop_dead(self):
        """
        Remove rays the Fortran solver gave up on, and return how many.

        The solver marks a non-converged ray by zeroing its direction cosines
        (see the iteration caps in ``woltsurf.f95``).  They must be removed
        immediately after the surface call, before anything else, for two
        reasons:

        * Left in, they are swallowed by the aperture test further down and
          silently counted as rays that *missed the mirror*.  That is a
          different physical statement and it quietly corrupts throughput.
        * ``analyses.analyticImagePlane`` computes ``x*l/n``.  A dead ray has
          ``n = 0``, so one survivor is enough to turn the focus -- and every
          metric derived from it -- into NaN.

        :meth:`Element.apply` pins this ordering.  Nothing should override it.
        """
        rays = self.rays
        alive = rays[4] ** 2 + rays[5] ** 2 + rays[6] ** 2 >= 0.1
        ndead = int((~alive).sum())
        if ndead:
            self.cut(alive)
        return ndead

    def drop_nonfinite(self):
        """Remove rays carrying a non-finite position, and return how many."""
        rays = self.rays
        good = (np.isfinite(rays[1]) & np.isfinite(rays[2])
                & np.isfinite(rays[3]))
        nbad = int((~good).sum())
        if nbad:
            self.cut(good)
        return nbad


def merge(beams):
    """
    Concatenate parallel channels into one beam.

    A single channel is returned untouched rather than copied, so a
    one-channel system is bit-for-bit what the flat pipeline produced.

    Ids stay globally sorted because each channel is allocated a contiguous,
    increasing id block (see :meth:`Source.launch`) and a cut preserves order
    within a channel.  Everything downstream -- :func:`stack_paths` above all
    -- reaches for ``searchsorted``, so that ordering is load bearing.
    """
    if len(beams) == 1:
        return beams[0]
    # Fanning happens in a system's ``common`` elements, downstream of this,
    # so no channel should arrive already carrying orders. Asserted rather
    # than handled: concatenating a fanned channel with an unfanned one would
    # need a rule for what order the unfanned rays are in, and there is no
    # honest answer to invent here.
    assert not any(b.fanned for b in beams), (
        'a channel was fanned into orders before the merge')
    rays = [np.concatenate([b.rays[i] for b in beams]) for i in range(10)]
    merged = Beam(rays, np.concatenate([b.ids for b in beams]))
    merged.stride = beams[0].stride
    return merged


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

#: Where an element sits: translations in mm, rotations in RADIANS.
#:
#: Radians, not the arcminutes the parameter panel shows, because this is what
#: ``tran.transform`` takes.  Conversion happens once, in the code that builds
#: the system, and never again.
Placement = collections.namedtuple('Placement', 'dx dy dz rx ry rz')

IDENTITY = Placement(0., 0., 0., 0., 0., 0.)


def coords_for(placement):
    """
    Transformation matrices mapping ``placement``'s local frame to global.

    Built by running a throwaway one-ray list through ``tran.transform`` with
    a coords list, rather than re-deriving the matrix products here.  The
    composition rules -- translate first, then Rx, Ry, Rz, with every argument
    negated on the way into Fortran -- live in ``transformations.transform``
    and must have exactly one home.  ``surfaces.oapCollimate`` already
    establishes this idiom.

    NOTE ``tran.newCoords()`` returns ``[identity_matrix()] * 4``: the *same*
    array object in all four slots.  That is safe only because ``transform``
    rebinds ``coords[i]`` with ``np.dot`` rather than writing into it.  Never
    write ``coords[i][:] = ...`` into one of these -- it would corrupt all
    four silently.
    """
    dummy = [np.zeros(1) for _ in range(10)]
    coords = tran.newCoords()
    tran.transform(dummy, *placement, coords=coords)
    return coords


def to_global(x, y, z, placement):
    """
    Map points from an element's local frame back to global coordinates.

    ``tran.transform`` moves the coordinate *frame*, so a point at local
    ``(x, y, z)`` sits at global ``(x, y, z)`` plus the frame offset, rotated.

    Pure translations take a fast path.  Not only for speed: it keeps the
    result exactly ``z + dz`` rather than a 4x4 dot product that happens to
    equal it, which is what lets the focal-plane stage stay bit-identical to
    the ``zs[-1] += focus_z`` line it replaced.
    """
    if placement == IDENTITY:
        return x, y, z
    if placement.rx == 0. and placement.ry == 0. and placement.rz == 0.:
        return x + placement.dx, y + placement.dy, z + placement.dz

    # applyTPos builds its homogeneous row as np.ones(np.size(x)), so it only
    # accepts flat arrays -- pass it a mesh and the coordinate list is ragged.
    # Flatten here and restore the shape, because meshes are exactly what a 3D
    # view asks for.
    shape = np.shape(x)
    flat = tran.applyTPos(np.ravel(x), np.ravel(y), np.ravel(z),
                          coords_for(placement), inverse=True)
    return tuple(np.reshape(component, shape) for component in flat)


def sample_beam(beam, placement=IDENTITY):
    """
    Copy a beam's positions and the launch ids they belong to, in global mm.

    Copies rather than views: the Fortran surface routines and
    ``tran.transform`` mutate ``rays`` in place, so a view would be rewritten
    by the next stage.
    """
    x, y, z = to_global(beam.rays[1], beam.rays[2], beam.rays[3], placement)
    return (beam.ids.copy(), np.array(x, dtype=float),
            np.array(y, dtype=float), np.array(z, dtype=float))


# ---------------------------------------------------------------------------
# Apertures
# ---------------------------------------------------------------------------

class Aperture(object):
    """
    What part of a surface actually exists.

    Returns a boolean keep-mask and touches nothing.  An aperture never cuts
    the beam itself -- :meth:`Element.apply` decides when to cut, because the
    ordering relative to ``drop_dead`` and to popping the frame is the part
    that matters.
    """

    def keep(self, rays):
        raise NotImplementedError


class NoAperture(Aperture):
    """A surface of unlimited extent."""

    def keep(self, rays):
        return np.ones(len(rays[1]), dtype=bool)


class AxialExtent(Aperture):
    """A grazing-incidence mirror, bounded in z."""

    def __init__(self, zlo, zhi):
        self.zlo = zlo
        self.zhi = zhi

    def keep(self, rays):
        return np.logical_and(rays[3] > self.zlo, rays[3] < self.zhi)


class Annulus(Aperture):
    """An annular stop, bounded in radius."""

    def __init__(self, rin, rout):
        self.rin = rin
        self.rout = rout

    def keep(self, rays):
        r = np.hypot(rays[1], rays[2])
        return np.logical_and(r >= self.rin, r <= self.rout)


class Disc(Aperture):
    """A circular detector or stop."""

    def __init__(self, radius):
        self.radius = radius

    def keep(self, rays):
        return np.hypot(rays[1], rays[2]) <= self.radius


class Rect(Aperture):
    """A rectangular grating or detector, half-widths in x and y."""

    def __init__(self, hx, hy):
        self.hx = hx
        self.hy = hy

    def keep(self, rays):
        return np.logical_and(np.abs(rays[1]) <= self.hx,
                              np.abs(rays[2]) <= self.hy)


# ---------------------------------------------------------------------------
# Geometry, for drawing
# ---------------------------------------------------------------------------

#: A quadrilateral mesh in global mm.  ``x``, ``y`` and ``z`` are 2D and the
#: same shape, ready for ``plot_surface`` or ``plot_wireframe``.
Patch = collections.namedtuple('Patch', 'name kind x y z style')

#: A 1D curve in global mm -- a groove hint, an axis, a focus marker.
Polyline = collections.namedtuple('Polyline', 'name kind x y z style')


#: Groove hint lines drawn across a grating.  Odd, so that one of them runs
#: through the centre.  This is a direction cue and nothing more: a 240 mm
#: grating at a 200 nm period carries over a million grooves, and a viewer is
#: expected to say so rather than let the picture imply a coarse one.
GROOVE_LINES = 9


#: How each kind of element is drawn.  Plain strings and floats, because this
#: module must not import matplotlib; the tab turns them into artist kwargs.
STYLES = {
    'mirror': {'color': '#000000', 'alpha': 0.55, 'lw': 2.5},
    'secondary': {'color': '#d62728', 'alpha': 0.55, 'lw': 2.5},
    'grating': {'color': '#9467bd', 'alpha': 0.35, 'lw': 1.0},
    'detector': {'color': 'crimson', 'alpha': 0.30, 'lw': 1.0},
    'aperture': {'color': '#7f7f7f', 'alpha': 0.25, 'lw': 1.0},
}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class Source(object):
    """
    Where a channel's rays come from.

    A source owns its own id block, so that ids stay unique and globally
    sorted once channels are concatenated -- see :func:`merge`.  Subclasses
    set :attr:`count` and implement :meth:`emit`.
    """

    #: How many rays this source launches, before any vignetting.
    count = 0
    #: First launch id. Channels are handed disjoint, increasing blocks.
    id_offset = 0
    #: Ids per launched ray: one slot for every order the system will put in
    #: flight, so that a grating downstream can fan a ray into slots inside
    #: its own block without colliding with the next ray's.  One when nothing
    #: fans, which reproduces the plain ``arange`` this used to be.
    id_stride = 1

    def emit(self):
        """Return a fresh ten-array ray list, positioned and pointed."""
        raise NotImplementedError

    def launch(self):
        """A :class:`Beam` of ``count`` rays with this channel's ids."""
        rays = self.emit()
        ids = (np.arange(len(rays[1])) + self.id_offset) * self.id_stride
        return Beam(rays, ids, stride=self.id_stride)

    def sample(self, beam):
        """The launch stage, in global mm."""
        return sample_beam(beam)


# ---------------------------------------------------------------------------
# Elements
# ---------------------------------------------------------------------------

class Element(object):
    """
    One optic: something the beam hits, and something a viewer can draw.

    Subclasses supply the physics (:meth:`trace_to`, or a whole
    :meth:`interact`) and the geometry (:meth:`profile`, :meth:`patches`),
    both expressed in the element's *own* frame.  Everything about placement,
    cutting, id bookkeeping and sampling lives in :meth:`apply`, so that no
    subclass is in a position to get the vignette/ray-id pairing wrong.

    ``kind`` is what lets a viewer choose a colour and a draw style without
    asking what the element actually is.
    """

    #: Identifier used to key per-element counters. Stable across releases.
    key = 'element'
    #: How this element is named in a message about the surface solver.
    #: Grammatical, and lower case: "failed to converge on the primary".
    label = 'this element'
    #: How it is named in a message about rays missing it.
    miss_label = 'this element'
    #: How it is named in a legend. Separate from :attr:`label` because the
    #: two are different parts of speech, and one string serving both ends up
    #: reading badly in at least one of them.
    title = 'Element'
    #: 'mirror' | 'secondary' | 'grating' | 'detector' | 'aperture'
    kind = 'mirror'

    placement = IDENTITY
    aperture = None
    #: Frame the aperture test runs in: 'parent' or 'local'.
    #:
    #: 'parent' pops the placement first and tests global coordinates.  That
    #: is what the flat Wolter pipeline did, and it is very slightly wrong for
    #: a tilted mirror, whose ends tilt with it -- 'local' is the physical
    #: test.  'parent' is preserved here deliberately, so that generalising
    #: the pipeline changed no numbers.  It is a known inaccuracy awaiting a
    #: deliberate, tested fix, not a considered choice.
    extent_frame = 'parent'
    #: True for a surface the rays end on. Its frame is not popped, so the
    #: survivors are left in image coordinates -- which is what the spot,
    #: HPD and RMS all want.
    terminal = False

    def check(self):
        """Raise ValueError if this element would hang or diverge."""

    # -- trace side --------------------------------------------------------

    def trace_to(self, rays):
        """Advance rays to this surface. Mutates ``rays`` in place."""
        raise NotImplementedError

    def interact(self, beam):
        """
        Hit the surface and act on it. Returns rays lost to the solver.

        The default is the grazing-incidence mirror case: solve, drop the
        rays the solver abandoned, reflect what is left.  A grating overrides
        this because ``tran.radgrat`` zeroes the direction cosines of
        evanescent orders, so its ``drop_dead`` belongs *after* diffraction; a
        detector overrides it because rays stop there and are not reflected.
        """
        self.trace_to(beam.rays)
        ndead = beam.drop_dead()
        if len(beam):
            tran.reflect(beam.rays)
        return ndead

    def apply(self, beam, record=True):
        """
        Push the frame, interact, cut what missed, pop the frame, sample.

        Returns ``(sample_or_None, reason_or_None, ndead)``, where ``reason``
        is ``'solver'`` or ``'aperture'`` when the element emptied the beam --
        the two cases stay distinguishable because they mean different things
        and produce different messages.

        The ordering here is load bearing and must not be reshuffled by a
        subclass: solve, then ``drop_dead``, then reflect, then pop, then the
        aperture test.  :meth:`Beam.drop_dead` explains what running the
        aperture test first would silently do to throughput.
        """
        moved = self.placement != IDENTITY
        if moved:
            tran.transform(beam.rays, *self.placement)

        ndead = self.interact(beam)

        if not len(beam):
            if moved and not self.terminal:
                tran.itransform(beam.rays, *self.placement)
            return None, 'solver', ndead

        if self.aperture is not None and self.extent_frame == 'local':
            keep = self.aperture.keep(beam.rays)
            if not keep.any():
                if moved and not self.terminal:
                    tran.itransform(beam.rays, *self.placement)
                return None, 'aperture', ndead
            beam.cut(keep)

        if moved and not self.terminal:
            tran.itransform(beam.rays, *self.placement)

        if self.aperture is not None and self.extent_frame == 'parent':
            keep = self.aperture.keep(beam.rays)
            if not keep.any():
                return None, 'aperture', ndead
            beam.cut(keep)

        return (self.sample(beam) if record else None), None, ndead

    def sample(self, beam):
        """Positions and ids at this element, in global mm."""
        return sample_beam(beam,
                           self.placement if self.terminal else IDENTITY)

    def empty_message(self, reason):
        """Why the beam is empty, in words."""
        if reason == 'solver':
            return ('The surface solver failed to converge on %s for every '
                    'ray.' % self.label)
        return 'All rays missed %s.' % self.miss_label

    # -- drawing side ------------------------------------------------------

    @property
    def style(self):
        return STYLES.get(self.kind, STYLES['mirror'])

    def profile(self, num=200):
        """``[(z, r), ...]`` polylines in the local frame, for a 2D view."""
        return []

    def patches(self, n_azimuth=32, phi0=0., dphi=2 * np.pi, num=None):
        """:class:`Patch` meshes in global mm, for a 3D view."""
        return []

    def polylines(self):
        """:class:`Polyline` decorations in global mm -- grooves, axes."""
        return []

    def z_range(self):
        """``(zlo, zhi)`` this element occupies, or None if it has no extent."""
        return None


class SurfaceOfRevolution(Element):
    """
    An element whose shape is one radius law swept about the z axis.

    Both the 2D profile and the 3D mesh come from :meth:`radius_at`, so there
    is exactly one radius law per element and a 2D and a 3D view cannot drift
    into disagreeing about where a mirror is.
    """

    #: Axial extent in the local frame; set by the subclass.
    zlo = zhi = 0.

    def radius_at(self, z):
        raise NotImplementedError

    def profile(self, num=200):
        z = np.linspace(self.zlo, self.zhi, num)
        return [(z, self.radius_at(z))]

    def patches(self, n_azimuth=32, phi0=0., dphi=2 * np.pi, num=None):
        phi = np.linspace(phi0, phi0 + dphi, n_azimuth)
        cos, sin = np.cos(phi), np.sin(phi)
        out = []
        for z, r in self.profile(24 if num is None else num):
            x = np.outer(r, cos)
            y = np.outer(r, sin)
            zz = np.repeat(z[:, None], n_azimuth, axis=1)
            gx, gy, gz = to_global(x, y, zz, self.placement)
            out.append(Patch(self.title, self.kind, gx, gy, gz, self.style))
        return out

    def z_range(self):
        return self.zlo, self.zhi


class Flat(Element):
    """
    A plane: a detector, a grating, a stop.

    Drawn as a rectangle in its own frame, which is where its aperture is
    tested too -- a tilted detector's edges tilt with it, and unlike the
    grazing mirrors there is no historical behaviour to preserve here.

    :meth:`z_range` stays None deliberately.  It feeds the "zoom on the
    optics" span, and a detector eight metres from the mirrors would stretch
    that back to the whole system, which is the view it exists to escape.
    """

    kind = 'detector'
    extent_frame = 'local'
    #: Half-width in x and y, in mm. None means unbounded.
    half_width = None

    def trace_to(self, rays):
        surf.flat(rays)

    def patches(self, n_azimuth=32, phi0=0., dphi=2 * np.pi, num=None):
        half = self.half_width
        if not half:
            return []
        edge = np.linspace(-half, half, 2)
        x, y = np.meshgrid(edge, edge)
        gx, gy, gz = to_global(x, y, np.zeros_like(x), self.placement)
        return [Patch(self.title, self.kind, gx, gy, gz, self.style)]


class DetectorPlane(Flat):
    """
    Where the rays are allowed to stop, placed rather than solved for.

    The alternative terminator to :class:`AutoFocus`: instead of finding best
    focus from the rays, this puts a plane exactly where the design says one
    is and reports what lands on it.  Defocus and detector tilt then show up
    in the spot and the half-power diameter as real degradations, which is
    the whole point -- an autofocus quietly absorbs both.

    Rays finish in detector coordinates, which is what the spot and HPD
    want; :meth:`Element.sample` still hands the layout views global mm.
    """

    key = 'detector'
    label = 'the detector'
    miss_label = 'the detector'
    title = 'Detector'
    terminal = True

    def __init__(self, placement=IDENTITY, half_width=None):
        self.placement = placement
        self.half_width = half_width
        self.aperture = None if not half_width else Rect(half_width,
                                                         half_width)
        #: Reported as the image-plane position, so that the drawn ray paths
        #: and the focus marker agree with where the detector actually is.
        self.focus_z = placement.dz

    def interact(self, beam):
        # No reflect: rays stop here.
        self.trace_to(beam.rays)
        return 0


class CurvedDetector(DetectorPlane):
    """
    A detector bent along the dispersion, as a spectrometer's is.

    A grating in a converging beam does not bring its orders to one plane.
    Measured on the default design with orders +/-3 in flight, the outer
    orders focus 0.40 mm ahead of the reference order, so on a flat detector
    they arrive 0.567 arcsec across against a floor of 0.384 -- half again
    as wide as the optics oblige them to be.  Bending the detector to follow
    that surface is what a Rowland circle is for, and it recovers nearly all
    of it: 0.383 arcsec at m = +/-3, measured.

    It is a trade, not a free win, and the numbers say so.  The middle of
    the fan pays for the wings: m = +/-1 goes from 0.090 to 0.111 arcsec,
    and m = 0 -- sitting on the apex -- does not move at all.

    Cylindrical rather than spherical: ``surfaces.cyl`` puts the cylinder
    axis along **y**, so the surface curves in the x-z plane and stays
    straight along the grooves.  That is exactly the shape wanted, and it
    means no new intersection solver.
    """

    title = 'Detector (curved)'

    def __init__(self, placement=IDENTITY, half_width=None, radius=200.):
        super(CurvedDetector, self).__init__(placement, half_width)
        #: Radius of curvature in mm, curving *towards* the telescope.
        self.radius = float(radius)

    def check(self):
        if self.radius <= 0.:
            raise ValueError('the detector radius of curvature must be '
                             'positive, not %g mm' % self.radius)

    def trace_to(self, rays):
        # Land on the apex plane first, THEN on the cylinder. Both steps
        # advance along the same straight ray, so the pair is exact -- and
        # the flat step is what makes the second one well posed.
        #
        # surfaces.cyl picks the root on the side the ray *started*, not the
        # first one along its direction. Rays reach here from the grating at
        # z ~ 500, which after the frame shift below is inside the cylinder
        # and on the wrong side, so solving directly put every ray on the far
        # branch: measured 723 arcsec per order, the width of the whole fan.
        # Advancing to z = 0 first puts them outside and on the near side,
        # where the root is the intended one.
        surf.flat(rays)

        # The frame is pushed by +radius so the cylinder's axis sits behind
        # the apex, putting the apex at local z = 0 and leaving the surface
        # z = R - sqrt(R^2 - x^2): curving towards the telescope, which is
        # the direction that helps. Bent the other way it roughly doubles the
        # outer orders instead, so the sign is load bearing and was settled
        # by measuring both.
        tran.transform(rays, 0., 0., self.radius, 0., 0., 0.)
        surf.cyl(rays, self.radius)
        tran.itransform(rays, 0., 0., self.radius, 0., 0., 0.)

    def sag(self, x):
        """How far the surface stands proud of its apex, at local ``x``."""
        x = np.asarray(x, dtype=float)
        return self.radius - np.sqrt(np.maximum(self.radius ** 2 - x ** 2, 0.))

    def patches(self, n_azimuth=32, phi0=0., dphi=2 * np.pi, num=None):
        """
        The bent surface as a quad grid, so a 3D view can draw it.

        A grid and not a flat quad, or the curvature -- the entire point of
        the element -- would be invisible in the very view meant to show it.
        """
        half = self.half_width
        if not half:
            return []
        across = np.linspace(-half, half, max(8, n_azimuth))
        along = np.linspace(-half, half, 2)
        x, y = np.meshgrid(across, along)
        gx, gy, gz = to_global(x, y, self.sag(x), self.placement)
        return [Patch(self.title, self.kind, gx, gy, gz, self.style)]


class LinearGrating(Flat):
    """
    A grating with straight, evenly spaced grooves.

    ``tran.grat`` sets the outgoing direction outright, so there is no
    reflection step -- and it marks an evanescent order by zeroing the
    direction cosines, exactly as the surface solvers mark a non-converged
    ray.  That is why :meth:`interact` drops dead rays *after* diffracting
    rather than before: dropping first would leave the evanescent ones to be
    swallowed by the aperture test and reported as rays that missed the
    grating, which is a different physical statement.
    """

    key = 'grating'
    label = 'the grating'
    miss_label = 'the grating'
    title = 'Grating'
    kind = 'grating'

    def __init__(self, placement, period, order, wavelength, half_width=None,
                 orders=None, substride=1):
        self.placement = placement
        #: Groove period in nm, order, and wavelength in nm.
        self.period = float(period)
        self.order = int(order)
        self.wavelength = float(wavelength)
        self.half_width = half_width
        self.aperture = None if not half_width else Rect(half_width,
                                                         half_width)
        #: Every order to put in flight, or None to trace only :attr:`order`.
        #: :attr:`order` stays the *reference* order either way -- the one the
        #: metrics are measured on -- so that adding an order to look at it
        #: cannot move a number.
        self.orders = None if orders is None else tuple(orders)
        #: Id spacing between this grating's order slots.  1 for the only
        #: grating in a system; see :meth:`System.assign_order_slots`.
        self.substride = int(substride)

    def check(self):
        if self.period <= 0.:
            raise ValueError('the grating period must be positive, not %g nm'
                             % self.period)
        if self.wavelength < 0.:
            raise ValueError('the wavelength must not be negative, not %g nm'
                             % self.wavelength)

    def interact(self, beam):
        # The fan goes between arriving and diffracting: every copy leaves
        # from the same point on the grating and only the outgoing direction
        # differs, which is what a diffraction order is.
        self.trace_to(beam.rays)
        if self.orders is not None and len(self.orders) > 1:
            beam.fan(self.orders, self.wavelength, self.substride)
            beam.reference = self.order
        self._diffract(beam)
        return beam.drop_dead()

    def _diffract(self, beam):
        # grat takes order and wavelength PER RAY -- and declares them
        # intent(inout), so these are built fresh every call rather than
        # cached. (radgrat, inconsistently, takes both as scalars; hence a
        # separate override rather than one shared call.)
        if beam.fanned:
            tran.grat(beam.rays, self.period, beam.orders.copy(),
                      beam.waves.copy())
            return
        count = len(beam)
        tran.grat(beam.rays, self.period,
                  np.repeat(float(self.order), count),
                  np.repeat(self.wavelength, count))

    def polylines(self):
        """Grooves and the direction the orders disperse in."""
        half = self.half_width
        if not half:
            return []
        out = [self._line(np.repeat(x, 2), np.array([-half, half]))
               for x in np.linspace(-half, half, GROOVE_LINES)]
        out.extend(self._dispersion_arrow())
        return out

    def _line(self, x, y, name=None):
        """A local-frame curve at z = 0, as a global :class:`Polyline`."""
        gx, gy, gz = to_global(np.asarray(x, dtype=float),
                               np.asarray(y, dtype=float),
                               np.zeros(len(x)), self.placement)
        return Polyline(name or self.title, self.kind, gx, gy, gz, self.style)

    def _dispersion_arrow(self):
        """
        An arrow pointing where the reference order actually goes.

        ``tran.grat`` does ``l -= order * wave / d``, so a *positive* order
        is deflected towards -x.  Pointing it the intuitive way round would
        be a picture that contradicts the trace drawn beside it.

        Three separate polylines -- shaft and two barbs -- rather than one
        with NaN breaks in it.  A NaN vertex is a line matplotlib politely
        skips and a hole a GL vertex buffer renders as a wild triangle.
        """
        half = self.half_width
        if not half or not self.order:
            return []
        sign = -1. if self.order > 0 else 1.
        length, tip = 0.7 * half, 0.12 * half
        # Along local x for both kinds of grating.  A linear grating's grooves
        # run along y; a radial grating's run towards a hub that sits on the y
        # axis, so at the centre they run along y too.  Dispersion is
        # perpendicular to the grooves, and that is x either way.
        start = np.array([0., 0.])
        end = start + np.array([sign * length, 0.])
        name = 'Dispersion m=%+d' % self.order
        barbs = [np.stack([end, end - np.array([sign * tip, side * tip])])
                 for side in (1., -1.)]
        out = [self._line(np.array([start[0], end[0]]),
                          np.array([start[1], end[1]]), name=name)]
        out.extend(self._line(b[:, 0], b[:, 1], name=name) for b in barbs)
        return out


class RadialGrating(LinearGrating):
    """
    A grating whose grooves converge on a hub, for a converging beam.

    The period varies with radius -- ``dpermm`` nm of period per mm -- so
    that every groove points at the same hub.  This is the arrangement an
    X-ray spectrometer actually uses behind a Wolter telescope, where the
    beam is converging rather than collimated.

    ``tran.radgrat`` puts the hub at the *local origin*, which is where the
    optical axis pierces this plane, so with ``hub_offset`` of zero nothing
    needs moving: the grooves radiate from the axis, the local period is
    ``dpermm * r``, and the dispersion is azimuthal.  A non-zero offset is
    applied inside :meth:`_diffract` rather than through the placement,
    because the placement would drag the ``Rect`` aperture and the drawn
    rectangle off the beam along with the hub.

    Note ``period`` is *deleted* rather than inherited.  The number this
    grating is specified by is nm per mm of radius, not nm, and leaving an
    attribute of the right name carrying the wrong quantity is how a caller
    ends up quietly reading it.
    """

    title = 'Radial grating'

    def __init__(self, placement, dpermm, order, wavelength, half_width=None,
                 hub_offset=0., **kwargs):
        super(RadialGrating, self).__init__(
            placement, float('nan'), order, wavelength,
            half_width=half_width, **kwargs)
        del self.period
        #: Groove period per mm of distance from the hub, in nm/mm.
        self.dpermm = float(dpermm)
        #: How far the hub sits off the optical axis, in mm, towards -y.
        self.hub_offset = float(hub_offset)

    def check(self):
        if self.dpermm <= 0.:
            raise ValueError('the radial groove period gradient must be '
                             'positive, not %g nm/mm' % self.dpermm)
        if self.wavelength < 0.:
            raise ValueError('the wavelength must not be negative, not %g nm'
                             % self.wavelength)

    def _diffract(self, beam):
        shift = (0., -self.hub_offset, 0., 0., 0., 0.)
        if self.hub_offset:
            tran.transform(beam.rays, *shift)
        try:
            if not beam.fanned:
                tran.radgrat(beam.rays, self.dpermm, float(self.order),
                             float(self.wavelength))
            else:
                # radgrat takes order and wavelength as SCALARS, so a fanned
                # beam is diffracted one order at a time through ind=.
                #
                # NOT radgratW, which does take a wavelength array: it
                # derives the outgoing sign of n from the sign of *y*
                # (transformationsf.f95:255) where radgrat correctly uses the
                # sign of n (line 216).  Our rays travel in -z at +y, so
                # radgratW would send half a converging beam back up the
                # telescope.
                for m in np.unique(beam.orders):
                    tran.radgrat(beam.rays, self.dpermm, float(m),
                                 float(self.wavelength),
                                 ind=(beam.orders == m))
        finally:
            if self.hub_offset:
                tran.itransform(beam.rays, *shift)

    def polylines(self):
        """
        Grooves radiating from the hub, a hub marker, and the dispersion.

        The hub is usually outside the drawn rectangle -- on the axis, with
        the grating off to one side of it -- so the marker is what makes the
        converging grooves mean anything.
        """
        half = self.half_width
        if not half:
            return []
        hub = np.array([0., -self.hub_offset])
        out = []
        for x in np.linspace(-half, half, GROOVE_LINES):
            # Along the local radius from the hub, extended past both edges
            # so a groove reads as a ray from the hub and not a chord.
            direction = np.array([x, half]) - hub
            length = np.hypot(*direction) or 1.
            direction = direction / length
            ends = np.stack([hub - direction * half,
                             hub + direction * 2. * half])
            out.append(self._line(ends[:, 0], ends[:, 1]))
        out.extend(self._hub_marker(hub, half * 0.08))
        out.extend(self._dispersion_arrow())
        return out

    def _hub_marker(self, hub, radius):
        phi = np.linspace(0., 2 * np.pi, 33)
        out = [self._line(hub[0] + radius * np.cos(phi),
                          hub[1] + radius * np.sin(phi), name='Hub')]
        out.append(self._line(hub[0] + np.array([-radius, radius]),
                              np.repeat(hub[1], 2), name='Hub'))
        out.append(self._line(np.repeat(hub[0], 2),
                              hub[1] + np.array([-radius, radius]),
                              name='Hub'))
        return out


class AutoFocus(Element):
    """
    Best focus, found from the rays themselves.

    ``surf.focusI`` moves the coordinate *frame* -- it calls ``tran.transform``
    internally -- so this element's placement is not known until it runs, and
    every ray sits at local z = 0 once it has.  Because the element is
    terminal, :meth:`Element.sample` maps that back through the placement, and
    the focal-plane stage lands at global ``focus_z`` on its own.  The flat
    pipeline patched this up afterwards with a ``zs[-1] += focus_z`` line;
    there is nothing left to patch.
    """

    key = 'focus'
    label = 'the focal plane'
    miss_label = 'the focal plane'
    title = 'Focus'
    kind = 'detector'
    terminal = True

    def __init__(self):
        self.focus_z = np.nan
        self.placement = IDENTITY

    def apply(self, beam, record=True):
        # Weighted to the reference order when several are in flight. An
        # unweighted solve would put the plane between the dispersed spots,
        # which is not where any of them is in focus -- so a drawn order
        # would silently move the HPD, the RMS and the focal length.
        dz = surf.focusI(beam.rays, weights=beam.focus_weights())
        self.focus_z = dz
        self.placement = Placement(0., 0., dz, 0., 0., 0.)
        return (self.sample(beam) if record else None), None, 0


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------

#: One independent bundle: a source and the elements it passes through.
Channel = collections.namedtuple('Channel', 'source elements')


class System(object):
    """
    An optical system: parallel channels sharing one terminator.

    A channel is one bundle's path from its source to the last element before
    the image.  Nested mirror shells are channels -- each has its own entrance
    annulus and its own pair of surfaces, and the survivors merge just before
    the terminator.  Rays do not cross between channels; modelling that would
    need a branching tracer, which this is not.

    ``focal_length`` is the denominator that turns millimetres at the image
    into arcseconds on the sky.  It is a property of the system rather than of
    any one parameter, which is what lets a design whose detector is not at
    ``z0`` still report angles correctly.

    ``problem`` is set by whatever built the system when the geometry is
    degenerate -- an aperture that closed up, a shell with no annulus.  It is
    reported rather than raised, so one bad shell in a nest does not take the
    rest of the design with it.
    """

    def __init__(self, channels, terminator, focal_length, entrance_area,
                 label='system', problem='', warnings=None, common=()):
        self.channels = list(channels)
        #: Elements every channel's survivors pass through together, after
        #: the merge and before the terminator. A grating behind a nest of
        #: shells is one object in the real instrument, not one per shell.
        self.common = list(common)
        self.terminator = terminator
        self.focal_length = focal_length
        self.entrance_area = entrance_area
        self.label = label
        self.problem = problem
        self.warnings = list(warnings or ())

    @property
    def elements(self):
        """Every element, channels flattened, terminator last."""
        out = []
        for channel in self.channels:
            out.extend(channel.elements)
        out.extend(self.common)
        out.append(self.terminator)
        return out

    # -- diffraction orders ------------------------------------------------

    @property
    def fanning_elements(self):
        """Elements that split one incoming ray into several outgoing ones."""
        return [e for e in self.elements
                if getattr(e, 'orders', None) is not None
                and len(e.orders) > 1]

    @property
    def order_stride(self):
        """How many id slots one launched ray needs."""
        stride = 1
        for element in self.fanning_elements:
            stride *= len(element.orders)
        return stride

    def assign_order_slots(self):
        """
        Give each fanning element its own digit of the id block.

        Mixed radix, so two gratings compose rather than collide: the first
        moves the ray between wide slots and the second subdivides them.
        With one grating -- which is every design the GUI builds today --
        this is simply a substride of 1.
        """
        substride = self.order_stride
        for element in self.fanning_elements:
            substride //= len(element.orders)
            element.substride = substride
        return self.order_stride

    def order_values(self):
        """Every order in flight, in id-slot order."""
        elements = self.fanning_elements
        if not elements:
            return None
        if len(elements) == 1:
            return tuple(elements[0].orders)
        combined = [()]
        for element in elements:
            combined = [prefix + (m,) for prefix in combined
                        for m in element.orders]
        return tuple(combined)

    def check(self):
        """
        Fan out to every element's own check.

        Note this is *not* where the Wolter misalignment guard lives.  That
        guard protects against a genuine hang in the Fortran secondary solver
        and is deliberately kept as a narrow, specific test against the
        parameters the panel shows -- generalising it into a check over
        arbitrary placements is exactly how it would lose its teeth.
        """
        for element in self.elements:
            element.check()

    # -- geometry ----------------------------------------------------------

    def patches(self, **kwargs):
        out = []
        for element in self.elements:
            out.extend(element.patches(**kwargs))
        return out

    def profiles(self, **kwargs):
        """``[(label, kind, z, r, style), ...]`` for every drawable element."""
        out = []
        for element in self.elements:
            for z, r in element.profile(**kwargs):
                out.append((element.title, element.kind, z, r, element.style))
        return out

    def polylines(self):
        out = []
        for element in self.elements:
            out.extend(element.polylines())
        return out

    def mirror_z_range(self):
        """
        ``(zlo, zhi)`` spanning every element that has an axial extent.

        This is what a zoom on "the optics" means, without anyone having to
        know that the optics are two Wolter mirrors between ``z0 - L`` and
        ``z0 + L``.  Returns None when nothing in the system has an extent.
        """
        ranges = [e.z_range() for e in self.elements]
        ranges = [r for r in ranges if r is not None]
        if not ranges:
            return None
        return min(r[0] for r in ranges), max(r[1] for r in ranges)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

class TraceResult(object):
    """Rays at the image plane plus the numbers derived from them."""

    def __init__(self, params, focal_length=None):
        self.params = params
        #: Millimetres-to-arcseconds denominator for this system.  Taken from
        #: the system rather than from ``params.z0`` so that a design whose
        #: detector is not at the Wolter node still reports angles correctly.
        self.focal_length = (getattr(params, 'z0', np.nan)
                             if focal_length is None else focal_length)
        self.rays = None
        #: Ray paths through the system, shape (stage, ray), stages being
        #: launch / primary / secondary / focus.  path_z and path_r are what
        #: the 2D layout draws; path_x and path_y are what 3D needs.
        self.path_z = None
        self.path_r = None
        self.path_x = None
        self.path_y = None
        #: Launch index of each drawn path, and of each surviving ray, so a
        #: path can be tied back to the ray it belongs to.
        self.path_ids = None
        self.ray_ids = None
        #: Diffraction order per surviving ray and per drawn path, or None
        #: when nothing in the system fanned the beam.
        self.orders = None
        self.path_orders = None
        #: Every order that was put in flight, and the one the metrics below
        #: are measured on.  They are not the same thing on purpose: extra
        #: orders exist to be looked at, and must not move a number.
        self.order_values = None
        self.reference_order = None
        #: Performance metrics.
        self.hpd_arcsec = np.nan
        self.rms_arcsec = np.nan
        self.hpd_mm = np.nan
        self.rms_mm = np.nan
        self.focus_z = np.nan
        self.num_launched = 0
        #: Surviving rays *in the reference order*, which is what throughput
        #: has always meant.  :attr:`num_surviving_all_orders` is the total
        #: across the fan, and differs only when one is being traced.
        self.num_surviving = 0
        self.num_surviving_all_orders = 0
        self.geometric_area = 0.
        self.message = ''
        #: Rays the Fortran surface solver gave up on, keyed by element.
        #: These are not geometric misses -- the solver hit its iteration cap
        #: and marked the ray dead.  Counted before the aperture test so they
        #: are never mistaken for rays that simply missed.
        self.nonconverged_by_element = collections.OrderedDict()
        #: Rays dropped for carrying a non-finite coordinate.
        self.num_nonfinite = 0
        #: Human-readable caveats about the numbers above.
        self.warnings = []

    def count_nonconverged(self, element, ndead):
        if ndead:
            key = element.key
            self.nonconverged_by_element[key] = (
                self.nonconverged_by_element.get(key, 0) + ndead)

    @property
    def nonconverged_primary(self):
        return self.nonconverged_by_element.get('primary', 0)

    @property
    def nonconverged_secondary(self):
        return self.nonconverged_by_element.get('secondary', 0)

    @property
    def num_nonconverged(self):
        """Total rays lost to solver non-convergence."""
        return sum(self.nonconverged_by_element.values())

    @property
    def metrics_are_bounds(self):
        """
        True when rays were lost to non-convergence rather than geometry.

        Those rays are excluded from every metric, so throughput and
        collecting area understate the real system: they are lower bounds,
        not measurements.
        """
        return self.num_nonconverged > 0 or self.num_nonfinite > 0

    @property
    def throughput(self):
        """
        Fraction of launched rays that made it through the system.

        Rays the solver failed to converge on count as lost, so when
        :attr:`metrics_are_bounds` is set this is a lower bound.
        """
        if self.num_launched == 0:
            return 0.
        return float(self.num_surviving) / self.num_launched

    @property
    def collecting_area(self):
        """
        Geometric aperture surviving vignetting, in cm^2.

        This is *not* effective area.  It counts the entrance annulus that
        makes it through the optics and nothing else -- there is no mirror
        reflectivity in it, because PyXFocus ships no reflectivity model.  A
        real Wolter-I loses roughly 10-20 per cent per bounce with a good
        coating, twice, and far more as photon energy rises, so treat this as
        a hard upper bound.

        Turning it into a true effective area needs a coating reflectivity
        table indexed by graze angle and energy; ``analyses.grazeAngle``
        already supplies the per-ray graze angles.

        Like :attr:`throughput`, this is a lower bound whenever
        :attr:`metrics_are_bounds` is set.
        """
        return self.geometric_area * self.throughput

    @property
    def reference_mask(self):
        """Which surviving rays are in the reference order; None if unfanned."""
        if self.orders is None or self.reference_order is None:
            return None
        return self.orders == self.reference_order

    @property
    def spot(self):
        """Image-plane (x, y) in mm, centred on the centroid."""
        if self.rays is None or self.num_surviving == 0:
            return np.array([]), np.array([])
        x, y = self.rays[1], self.rays[2]
        mask = self.reference_mask
        if mask is not None:
            x, y = x[mask], y[mask]
        return x - np.mean(x), y - np.mean(y)

    def spot_by_order(self):
        """
        ``{order: (x, y)}`` in mm, all of them about the reference centroid.

        About the *reference* centroid and not each order's own: centring
        every order on itself would stack the dispersed spots on top of one
        another and hide the only thing this is for.  Returns None when the
        beam was never fanned.
        """
        if self.rays is None or self.orders is None:
            return None
        x, y = self.rays[1], self.rays[2]
        mask = self.reference_mask
        if mask is None or not mask.any():
            x0, y0 = np.mean(x), np.mean(y)
        else:
            x0, y0 = np.mean(x[mask]), np.mean(y[mask])
        out = collections.OrderedDict()
        for m in self.order_values or ():
            here = self.orders == m
            out[m] = (x[here] - x0, y[here] - y0)
        return out

    def resolving_power(self):
        """
        ``{order: R}`` where R = lambda / delta-lambda, or None.

        The number a grating spectrometer is specified by: how close two
        wavelengths can be and still be told apart.  Delta-lambda is the
        shift that moves a spot by its own width, so
        ``R = lambda * (dx/dlambda) / w``.

        Measured rather than derived from the grating equation.  Because
        ``x`` is proportional to ``m * lambda``, ``dx/dlambda`` is
        ``(m/lambda) * dx/dm`` -- and ``dx/dm`` is just the spacing between
        adjacent orders, which a fan has already put on the detector.  So
        this needs no extra trace, assumes no particular groove law, and
        works for a radial grating as readily as a linear one:

            R = m * (spacing between adjacent orders) / (spot width)

        Returns None when nothing was fanned, and omits an order whose
        neighbours are missing rather than inventing a spacing for it.  One
        order in flight means no spacing to measure, and a fabricated
        resolving power is worse than none.
        """
        if self.rays is None or self.orders is None or not self.order_values:
            return None
        centres, widths = {}, {}
        for m in self.order_values:
            here = self.orders == m
            if not here.any():
                continue
            centres[m] = float(np.mean(self.rays[1][here]))
            widths[m] = float(anal.hpd([c[here] for c in self.rays]))

        out = collections.OrderedDict()
        for m in self.order_values:
            if m == 0 or m not in centres or not widths.get(m):
                # Order zero is undiffracted: it does not move with
                # wavelength, so it resolves nothing and has no R at all.
                continue
            neighbours = [n for n in (m - 1, m + 1) if n in centres]
            if not neighbours:
                continue
            spacing = np.mean([abs(centres[m] - centres[n])
                               for n in neighbours])
            out[m] = abs(m) * spacing / widths[m]
        return out or None

    def spot_arcsec_by_order(self):
        """:meth:`spot_by_order` in arcseconds, as the plots want it."""
        spots = self.spot_by_order()
        if spots is None:
            return None
        scale = _ARCSEC_PER_RAD / self.focal_length
        return collections.OrderedDict(
            (m, (x * scale, y * scale)) for m, (x, y) in spots.items())

    @property
    def spot_arcsec(self):
        """
        The image-plane spot in arcseconds, as the plots want it.

        ``hpd_arcsec`` and ``encircled_energy`` are already in arcseconds; the
        spot was the one member of the family that was not, which is why the
        GUI had to reach in here for a private constant and divide by z0
        itself.
        """
        x, y = self.spot
        scale = _ARCSEC_PER_RAD / self.focal_length
        return x * scale, y * scale


# ---------------------------------------------------------------------------
# Path bookkeeping
# ---------------------------------------------------------------------------

def choose_paths(launch_stage, final_ids, num, stride=1):
    """
    Pick which surviving rays to draw, spread evenly around the aperture.

    Taking the first N would cluster them wherever the random source happened
    to put its low indices; sorting by launch azimuth gives the even fan a
    layout drawing wants.

    Whole ray *families* when a grating has fanned the beam: an incident ray
    splitting into a coloured fan of orders is the picture worth drawing, and
    picking orders independently would draw three unrelated rays in three
    colours instead.  ``id // stride`` is the launched ray a slot belongs to.
    """
    ids0, x0, y0, _ = launch_stage
    if stride == 1:
        row = np.searchsorted(ids0, final_ids)
        order = np.argsort(np.arctan2(y0[row], x0[row]))
        if len(order) > num:
            order = order[np.linspace(0, len(order) - 1, num).astype(int)]
        # Sorted, because every lookup below is a searchsorted.
        return np.sort(final_ids[order])

    families = np.unique(final_ids // stride)
    row = np.searchsorted(ids0 // stride, families)
    order = np.argsort(np.arctan2(y0[row], x0[row]))
    # num is a budget of drawn lines, not of rays, so that turning on five
    # orders does not quintuple what is on screen.
    want = max(1, num // stride)
    if len(order) > want:
        order = order[np.linspace(0, len(order) - 1, want).astype(int)]
    keep = np.isin(final_ids // stride, families[order])
    return np.sort(final_ids[keep])


def stack_paths(stages, num, stride=1):
    """
    Assemble per-stage samples into ``(stages, rays)`` arrays.

    Alignment is by *ray id*, not by position.  ``tran.vignette`` returns
    ``[rays[i][ind] ...]``, so survivors are a subsequence and not a prefix --
    an earlier "truncate every stage to the shortest" scheme joined one ray's
    launch point to a different ray's mirror hit.  Measured at offaxis=10: of
    40 drawn polylines only 3 connected the same ray.  That was invisible in
    the 2D radius plot, because every launch and primary radius lies in a
    0.65 mm band, and is glaring in 3D where azimuth is visible.

    Each stage's ids are a subset of the previous stage's and both are sorted,
    so ``searchsorted`` recovers each chosen ray's row exactly.

    Every stage arrives already in global coordinates -- :meth:`Element.sample`
    sees to that, including for the terminal element whose frame has moved --
    so there is no post-hoc correction here.
    """
    if not stages:
        return None, None, None, None
    final_ids = stages[-1][0]
    if len(final_ids) == 0:
        return None, None, None, None

    chosen = choose_paths(stages[0], final_ids, num, stride)
    shape = (len(stages), len(chosen))
    xs = np.empty(shape)
    ys = np.empty(shape)
    zs = np.empty(shape)
    for k, (ids, x, y, z) in enumerate(stages):
        # A stage before a grating holds one row per launched ray; a stage
        # after it holds one per (ray, order).  Ids are handed out in blocks
        # of ``stride`` and a fan only fills slots inside a block, so the
        # largest stage id not greater than a chosen id is exactly the row
        # that ray came from: the pre-fan row upstream, the ray itself
        # downstream.  With stride 1 and exact ids this is the plain
        # searchsorted it replaced.
        row = np.searchsorted(ids, chosen, side='right') - 1
        xs[k], ys[k], zs[k] = x[row], y[row], z[row]
    return chosen, xs, ys, zs


def merge_stages(stage_sets):
    """
    Concatenate each stage across channels, keeping ids sorted.

    Channels hold contiguous, increasing id blocks, so concatenating in
    channel order is already globally sorted -- which is what
    :func:`stack_paths` needs.  A single channel is passed straight through.
    """
    stage_sets = [s for s in stage_sets if s]
    if not stage_sets:
        return []
    if len(stage_sets) == 1:
        return stage_sets[0]
    depth = min(len(s) for s in stage_sets)
    out = []
    for k in range(depth):
        parts = [s[k] for s in stage_sets]
        out.append(tuple(np.concatenate([p[i] for p in parts])
                         for i in range(4)))
    return out


# ---------------------------------------------------------------------------
# The tracer
# ---------------------------------------------------------------------------

def trace_system(system, params, record_paths=True, num_paths=40):
    """
    Trace every channel of ``system`` and measure the image it forms.

    Parameters
    ----------
    system : System
        What to trace.  Built by a design-specific factory --
        ``wolter.build_system`` is the first -- so that this function never
        has to know what kind of telescope it is looking at.
    params : object
        Carried onto the result untouched, for the GUI's benefit.
    record_paths : bool
        Capture ray positions at each surface, for the layout plots.
    num_paths : int
        How many rays to record paths for.

    Returns
    -------
    TraceResult
        Rays at the image plane and the derived performance metrics.  If every
        ray vignettes, ``message`` explains where, and the metrics stay NaN.
    """
    system.check()

    result = TraceResult(params, focal_length=system.focal_length)
    result.geometric_area = system.entrance_area
    result.warnings.extend(system.warnings)
    if system.problem:
        result.message = system.problem
        return result

    beams = []
    stage_sets = []
    notes = []
    for channel in system.channels:
        beam = channel.source.launch()
        result.num_launched += channel.source.count
        stages = [channel.source.sample(beam)] if record_paths else []

        emptied = None
        for element in channel.elements:
            sample, reason, ndead = element.apply(beam, record_paths)
            result.count_nonconverged(element, ndead)
            if reason is not None:
                emptied = element.empty_message(reason)
                break
            if record_paths:
                stages.append(sample)

        if emptied is not None:
            notes.append(emptied)
        elif len(beam):
            beams.append(beam)
            stage_sets.append(stages)

    if not beams:
        # One channel: its own message, verbatim. Several: say how many.
        result.message = (notes[0] if len(notes) == 1
                          else _summarise(notes, len(system.channels)))
        return result

    beam = merge(beams)
    result.num_nonfinite = beam.drop_nonfinite()
    if not len(beam):
        result.message = 'No rays survived the trace.'
        return result

    stages = merge_stages(stage_sets) if record_paths else []
    for element in system.common:
        sample, reason, ndead = element.apply(beam, record_paths)
        result.count_nonconverged(element, ndead)
        if reason is not None:
            result.message = element.empty_message(reason)
            return result
        if record_paths:
            stages.append(sample)

    terminator = system.terminator
    sample, reason, ndead = terminator.apply(beam, record_paths)
    result.count_nonconverged(terminator, ndead)
    if reason is not None or not len(beam):
        # A placed detector can be missed entirely; an autofocus cannot.
        result.message = (terminator.empty_message(reason or 'aperture'))
        result.focus_z = getattr(terminator, 'focus_z', np.nan)
        return result

    result.focus_z = getattr(terminator, 'focus_z', np.nan)
    if record_paths:
        stages.append(sample)

    result.rays = beam.rays
    result.ray_ids = beam.ids
    result.orders = beam.orders
    result.reference_order = beam.reference
    result.order_values = system.order_values()
    result.num_surviving_all_orders = len(beam)

    # Every metric below is measured on the reference order alone. Orders
    # added to be looked at must not move a number -- see
    # Beam.focus_weights, which keeps best focus honest for the same reason.
    metric_rays = beam.rays
    mask = beam.reference_mask()
    if mask is not None:
        metric_rays = [component[mask] for component in beam.rays]
    result.num_surviving = len(metric_rays[1])

    if result.num_surviving:
        result.hpd_mm = anal.hpd(metric_rays)
        result.rms_mm = anal.rmsCentroid(metric_rays)
        result.hpd_arcsec = (result.hpd_mm / system.focal_length
                             * _ARCSEC_PER_RAD)
        result.rms_arcsec = (result.rms_mm / system.focal_length
                             * _ARCSEC_PER_RAD)
    else:
        result.warnings.append(
            'No rays survived in order %s, so there are no metrics to '
            'report; the other orders are drawn but not measured.'
            % result.reference_order)

    if record_paths:
        ids, xs, ys, zs = stack_paths(stages, num_paths, beam.stride)
        result.path_ids = ids
        if ids is not None and result.order_values is not None:
            # Which order a drawn path is in falls straight out of the id
            # arithmetic: the slot within its block is the index into the
            # orders that were put in flight.
            values = np.asarray(result.order_values)
            result.path_orders = values[ids % beam.stride]
        result.path_x, result.path_y, result.path_z = xs, ys, zs
        # path_r keeps its old name and shape so the 2D layout tab needs no
        # change and inherits the id-alignment fix for free.
        if xs is not None:
            result.path_r = np.hypot(xs, ys)

    if notes:
        result.warnings.extend(notes)
    _add_warnings(result)
    return result


def _summarise(notes, total):
    """One message for a multi-channel system where every channel emptied."""
    if not notes:
        return 'No rays survived the trace.'
    unique = sorted(set(notes))
    if len(unique) == 1:
        return '%s (all %d channels)' % (unique[0], total)
    return 'No rays survived the trace: ' + ' '.join(unique)


def _add_warnings(result):
    """Spell out, in words, when the metrics are bounds rather than values."""
    lost = result.num_nonconverged
    if lost:
        where = []
        for key, count in result.nonconverged_by_element.items():
            where.append('%d on the %s' % (count, key))
        result.warnings.append(
            '%d of %d rays (%.1f%%) did not converge (%s). They are excluded '
            'from every metric, so throughput and collecting area are lower '
            'bounds.'
            % (lost, result.num_launched,
               100. * lost / max(result.num_launched, 1), ', '.join(where)))
    if result.num_nonfinite:
        result.warnings.append(
            '%d rays were dropped for non-finite coordinates.'
            % result.num_nonfinite)
