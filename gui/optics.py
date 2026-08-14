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

    __slots__ = ('rays', 'ids')

    def __init__(self, rays, ids=None):
        self.rays = rays
        self.ids = np.arange(len(rays[1])) if ids is None else ids

    def __len__(self):
        return len(self.rays[1])

    def cut(self, keep):
        """Drop rays where ``keep`` is False, ids included."""
        self.rays = tran.vignette(self.rays, ind=keep)
        self.ids = self.ids[keep]

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
    rays = [np.concatenate([b.rays[i] for b in beams]) for i in range(10)]
    return Beam(rays, np.concatenate([b.ids for b in beams]))


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

    def emit(self):
        """Return a fresh ten-array ray list, positioned and pointed."""
        raise NotImplementedError

    def launch(self):
        """A :class:`Beam` of ``count`` rays with this channel's ids."""
        rays = self.emit()
        ids = np.arange(len(rays[1])) + self.id_offset
        return Beam(rays, ids)

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

    def __init__(self, placement, period, order, wavelength, half_width=None):
        self.placement = placement
        #: Groove period in nm, order, and wavelength in nm.
        self.period = float(period)
        self.order = int(order)
        self.wavelength = float(wavelength)
        self.half_width = half_width
        self.aperture = None if not half_width else Rect(half_width,
                                                         half_width)

    def check(self):
        if self.period <= 0.:
            raise ValueError('the grating period must be positive, not %g nm'
                             % self.period)
        if self.wavelength < 0.:
            raise ValueError('the wavelength must not be negative, not %g nm'
                             % self.wavelength)

    def interact(self, beam):
        self.trace_to(beam.rays)
        self._diffract(beam)
        return beam.drop_dead()

    def _diffract(self, beam):
        # grat takes order and wavelength PER RAY -- and declares them
        # intent(inout), so these are built fresh every call rather than
        # cached. (radgrat, inconsistently, takes both as scalars; hence a
        # separate override rather than one shared call.)
        count = len(beam)
        tran.grat(beam.rays, self.period,
                  np.repeat(float(self.order), count),
                  np.repeat(self.wavelength, count))

    def polylines(self):
        """A few grooves, so the dispersion direction is visible."""
        half = self.half_width
        if not half:
            return []
        out = []
        for x in np.linspace(-half, half, 5):
            gx, gy, gz = to_global(np.repeat(x, 2),
                                   np.array([-half, half]),
                                   np.zeros(2), self.placement)
            out.append(Polyline(self.title, self.kind, gx, gy, gz,
                                self.style))
        return out


class RadialGrating(LinearGrating):
    """
    A grating whose grooves converge on a hub, for a converging beam.

    The period varies with radius -- ``dpermm`` nm of period per mm -- so
    that every groove points at the same hub.  This is the arrangement an
    X-ray spectrometer actually uses behind a Wolter telescope, where the
    beam is converging rather than collimated.
    """

    title = 'Radial grating'

    def _diffract(self, beam):
        tran.radgrat(beam.rays, self.period, self.order, self.wavelength)


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
        dz = surf.focusI(beam.rays)
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
        #: Performance metrics.
        self.hpd_arcsec = np.nan
        self.rms_arcsec = np.nan
        self.hpd_mm = np.nan
        self.rms_mm = np.nan
        self.focus_z = np.nan
        self.num_launched = 0
        self.num_surviving = 0
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
    def spot(self):
        """Image-plane (x, y) in mm, centred on the centroid."""
        if self.rays is None or self.num_surviving == 0:
            return np.array([]), np.array([])
        x, y = self.rays[1], self.rays[2]
        return x - np.mean(x), y - np.mean(y)

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

def choose_paths(launch_stage, final_ids, num):
    """
    Pick which surviving rays to draw, spread evenly around the aperture.

    Taking the first N would cluster them wherever the random source happened
    to put its low indices; sorting by launch azimuth gives the even fan a
    layout drawing wants.
    """
    ids0, x0, y0, _ = launch_stage
    row = np.searchsorted(ids0, final_ids)
    order = np.argsort(np.arctan2(y0[row], x0[row]))
    if len(order) > num:
        order = order[np.linspace(0, len(order) - 1, num).astype(int)]
    # Sorted, because every lookup below is a searchsorted.
    return np.sort(final_ids[order])


def stack_paths(stages, num):
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

    chosen = choose_paths(stages[0], final_ids, num)
    shape = (len(stages), len(chosen))
    xs = np.empty(shape)
    ys = np.empty(shape)
    zs = np.empty(shape)
    for k, (ids, x, y, z) in enumerate(stages):
        row = np.searchsorted(ids, chosen)
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
    result.num_surviving = len(beam)
    result.hpd_mm = anal.hpd(beam.rays)
    result.rms_mm = anal.rmsCentroid(beam.rays)
    result.hpd_arcsec = result.hpd_mm / system.focal_length * _ARCSEC_PER_RAD
    result.rms_arcsec = result.rms_mm / system.focal_length * _ARCSEC_PER_RAD

    if record_paths:
        ids, xs, ys, zs = stack_paths(stages, num_paths)
        result.path_ids = ids
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
