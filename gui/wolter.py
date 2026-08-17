"""
Wolter-I telescope trace, packaged as a single call.

This wraps the raw PyXFocus routines (sources -> primary -> secondary ->
focus) into one function that takes a parameter object and hands back rays
plus the performance numbers you actually want to look at.  The GUI calls
this, but it is deliberately usable on its own from a script or notebook::

    from PyXFocus.gui.wolter import WolterParams, trace

    result = trace(WolterParams(r0=220., z0=8400., offaxis=1.0))
    print(result.hpd_arcsec)

Geometry convention (inherited from PyXFocus):
    * The Wolter focus sits at the origin, +z points back toward the sky.
    * The primary/secondary node is at z = z0, radius r0, so z0 is the
      focal length.
    * The primary spans z0 -> z0 + primary_length.
    * The secondary spans z0 - secondary_length -> z0.
"""

import collections
import math

import numpy as np

import PyXFocus.conicsolve as conic
import PyXFocus.sources as sources
import PyXFocus.surfaces as surf
import PyXFocus.transformations as tran
import PyXFocus.gui.optics as optics

#: Radians per arcminute, and arcseconds per radian.
_ARCMIN = np.pi / (180. * 60.)
_ARCSEC_PER_RAD = 180. / np.pi * 3600.

#: Misalignment limits, in mm and arcminutes.
#:
#: These are not physics limits -- they guard a real defect in the Fortran
#: secondary solver (``woltsurf.f95``).  Once the secondary is displaced far
#: enough that rays no longer intersect the hyperboloid, the Newton iteration
#: stops converging and spins forever, hanging the caller with no error.
#: Measured empirically: translations hang between 80 and 100 mm, rotations
#: between 20 and 40 arcmin.  The caps below sit well inside that, and are
#: still enormous next to real Wolter-I alignment tolerances (microns and
#: arcseconds).  Past roughly a millimetre only a handful of rays survive
#: vignetting anyway, so the results stop meaning anything long before here.
MAX_TRANSLATION_MM = 20.
MAX_ROTATION_ARCMIN = 15.


def check_misalignment(params):
    """
    Raise if a misalignment would trip the non-converging secondary solver.

    Raises
    ------
    ValueError
        If any translation or rotation exceeds the documented safe limit.
    """
    for name in ('sec_dx', 'sec_dy', 'sec_dz'):
        val = getattr(params, name)
        if abs(val) > MAX_TRANSLATION_MM:
            raise ValueError(
                '%s = %g mm exceeds the safe limit of %g mm. Beyond this the '
                'Fortran secondary solver fails to converge and hangs.'
                % (name, val, MAX_TRANSLATION_MM))
    for name in ('sec_rx', 'sec_ry', 'sec_rz'):
        val = getattr(params, name)
        if abs(val) > MAX_ROTATION_ARCMIN:
            raise ValueError(
                '%s = %g arcmin exceeds the safe limit of %g arcmin. Beyond '
                'this the Fortran secondary solver fails to converge and hangs.'
                % (name, val, MAX_ROTATION_ARCMIN))


class WolterParams(object):
    """
    Every knob the Wolter-I explorer exposes.

    Parameters
    ----------
    r0 : float
        Shell radius at the node, in mm.
    z0 : float
        Focal length (node to focus), in mm.
    primary_length, secondary_length : float
        Axial mirror lengths, in mm.
    psi : float
        Wolter prescription parameter; 1.0 is a classic Wolter-I.
    offaxis : float
        Source off-axis angle, in arcminutes.
    azimuth : float
        Azimuth of the off-axis direction, in degrees.
    num_rays : int
        Rays launched before vignetting, across the whole nest.
    num_shells : int
        Concentric shells, close-packed outward from ``r0``.
    shell_gap : float
        Radial wall thickness plus clearance between shells, in mm.
    sec_dx ... sec_rz : float
        Secondary mirror misalignment: translations in mm, rotations in
        arcminutes.
    use_grating : int
        Put a diffraction grating in the converging beam.
    grating_type : int
        0 for a linear grating, 1 for a radial one whose grooves converge on
        a hub.  A radial grating is what an X-ray spectrometer actually uses
        behind a Wolter, because the beam is converging rather than
        collimated.
    grating_z : float
        Where it sits, in mm above the focus.
    grating_period : float
        Groove period in nm.  Linear gratings only.
    grating_dpermm : float
        Period *gradient* in nm per mm of distance from the hub; the local
        period is this times the radius.  Radial gratings only, and a
        different quantity from ``grating_period`` despite both being about
        groove spacing -- which is why they are separate fields.
    grating_hub : float
        How far off the optical axis, in mm, the grooves converge.  Radial
        gratings only.  0 puts the hub where the axis pierces the grating --
        which is inside the beam, where the local period runs to zero and the
        image smears to hundreds of arcseconds.  A real spectrometer puts the
        hub metres away, so that the grooves are near enough parallel across
        the beam.
    grating_order : int
        Diffraction order. Zero reproduces the system without a grating.
        This is the *reference* order: every metric is measured on it.
    grating_order_span : int
        Also put orders +/- this many in flight, so the dispersion can be
        seen.  They are drawn and never measured, so raising this cannot
        move the HPD, the RMS, the focus or the throughput.
    wavelength : float
        Wavelength in nm.
    grating_size : float
        Grating half-width in mm; rays outside it are vignetted.
    use_detector : int
        Put the image plane where the design says, instead of at best focus.
    det_shape : int
        0 for a flat detector, 1 for one bent along the dispersion.  A
        grating in a converging beam does not bring its orders to a single
        plane, so a flat detector costs the outer ones: measured with orders
        +/-3 in flight, m = +/-3 arrives 0.567 arcsec across against a floor
        of 0.384.  Bending the detector recovers nearly all of that, at the
        cost of the middle of the fan -- which is the trade a Rowland circle
        makes.
    det_z : float
        Detector position in mm; 0 is the nominal focus.
    det_size : float
        Detector half-width in mm.  0 means unbounded, which is what this
        has always been -- it caught every ray however far off and drew
        nothing in the 3D view.  Any positive value both vignettes and draws.
    det_tilt : float
        Detector tilt about x, in degrees.
    det_tilt_y : float
        Detector tilt about y, in degrees.  This is the one that leans the
        detector *along* the dispersion, which runs in x; the x tilt above
        rotates perpendicular to it and cannot follow a dispersed focus.
    det_radius : float
        Radius of curvature in mm when ``det_shape`` is curved, bending
        towards the telescope.
    seed : int or None
        Seed for the random ray pattern, so a trace is repeatable.
    """

    def __init__(self, r0=220., z0=8400., primary_length=100.,
                 secondary_length=100., psi=1., offaxis=0., azimuth=0.,
                 num_rays=20000, num_shells=1, shell_gap=1.,
                 sec_dx=0., sec_dy=0., sec_dz=0.,
                 sec_rx=0., sec_ry=0., sec_rz=0.,
                 use_grating=0, grating_type=0, grating_z=500.,
                 grating_period=200., grating_dpermm=200. / 8400.,
                 grating_hub=8400.,
                 grating_order=1, grating_order_span=0,
                 wavelength=2., grating_size=120.,
                 use_detector=0, det_shape=0, det_z=0., det_size=0.,
                 det_tilt=0., det_tilt_y=0., det_radius=200., seed=0):
        self.r0 = r0
        self.z0 = z0
        self.primary_length = primary_length
        self.secondary_length = secondary_length
        self.psi = psi
        self.num_shells = num_shells
        self.shell_gap = shell_gap
        self.offaxis = offaxis
        self.azimuth = azimuth
        self.num_rays = num_rays
        self.sec_dx = sec_dx
        self.sec_dy = sec_dy
        self.sec_dz = sec_dz
        self.sec_rx = sec_rx
        self.sec_ry = sec_ry
        self.sec_rz = sec_rz
        self.use_grating = use_grating
        self.grating_type = grating_type
        self.grating_z = grating_z
        self.grating_period = grating_period
        self.grating_dpermm = grating_dpermm
        self.grating_hub = grating_hub
        self.grating_order = grating_order
        self.grating_order_span = grating_order_span
        self.wavelength = wavelength
        self.grating_size = grating_size
        self.use_detector = use_detector
        self.det_shape = det_shape
        self.det_z = det_z
        self.det_size = det_size
        self.det_tilt = det_tilt
        self.det_tilt_y = det_tilt_y
        self.det_radius = det_radius
        self.seed = seed

    def misalignment(self):
        """Secondary misalignment as PyXFocus expects it (mm and radians)."""
        return (self.sec_dx, self.sec_dy, self.sec_dz,
                self.sec_rx * _ARCMIN, self.sec_ry * _ARCMIN,
                self.sec_rz * _ARCMIN)

    def copy(self):
        new = WolterParams()
        new.__dict__.update(self.__dict__)
        return new

    def to_dict(self):
        """
        Parameters as plain JSON-ready values, in a stable field order.

        Deliberately Qt-free so configurations can be written and read from
        a script or a notebook, not only from the GUI.

        ``seed`` may be None, which means "do not seed" and is a different
        trace from seeding with zero -- so it is carried through as None
        rather than coerced.
        """
        out = collections.OrderedDict()
        for name in PARAM_FIELDS:
            value = getattr(self, name)
            if name == 'seed' and value is None:
                out[name] = None
            elif name in INT_FIELDS:
                out[name] = int(value)
            else:
                out[name] = float(value)
        return out

    @classmethod
    def from_dict(cls, data, problems=None):
        """
        Build from a dict, defaulting anything absent or unusable.

        Parameters
        ----------
        data : dict
            Field values. Extra keys are ignored rather than assigned --
            a typo like ``sec_dyy`` must not become a silent attribute.
        problems : list, optional
            Human-readable notes are appended here. Nothing raises: one bad
            field should cost you that field, not the whole configuration.
            A caller wanting strictness inspects the list itself.

        Returns
        -------
        WolterParams
        """
        if problems is None:
            problems = []
        params = cls()

        if not isinstance(data, dict):
            problems.append('parameters must be a mapping; using defaults')
            return params

        for key in sorted(data):
            if key not in _SPEC_BY_NAME:
                problems.append('ignored unknown parameter %r' % key)

        for name in PARAM_FIELDS:
            if name not in data:
                problems.append('%s missing; using default %r'
                                % (name, getattr(params, name)))
                continue
            raw = data[name]
            # A null seed means "do not seed", which trace() honours and
            # which is a different run from seeding with zero. Accept it
            # rather than reporting it as a bad number.
            if name == 'seed' and raw is None:
                params.seed = None
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                problems.append('%s is not a number (%r); using default'
                                % (name, raw))
                continue
            # json.loads accepts bare NaN and Infinity, and a spin box turns
            # NaN into its *maximum* without complaint -- which would read
            # back as a deliberate extreme. Refuse them here instead.
            if not math.isfinite(value):
                problems.append('%s is not finite (%r); using default'
                                % (name, raw))
                continue
            setattr(params, name, int(value) if name in INT_FIELDS else value)

        return params


#: Rays at focus plus the numbers derived from them.
#:
#: Lives in :mod:`PyXFocus.gui.optics` now, because nothing about it is
#: Wolter-specific, and is re-exported here so that ``wolter.TraceResult``
#: keeps meaning what it always did.
TraceResult = optics.TraceResult


def shell_radii(params, r0=None):
    """Inner and outer radius of the primary entrance aperture, in mm."""
    if r0 is None:
        r0 = params.r0
    rin = conic.primrad(params.z0, r0, params.z0, psi=params.psi)
    rout = conic.primrad(params.z0 + params.primary_length,
                         r0, params.z0, psi=params.psi)
    return rin, rout


class WolterSource(optics.Source):
    """
    The entrance annulus of one shell, illuminated from a given direction.

    Rays start 500 mm above the top of the primary, travelling in -z.  The
    off-axis direction is applied by setting the direction cosines outright
    rather than by rotating the frame, so that the ray *positions* stay put
    across the aperture and only the incoming angle changes.
    """

    def __init__(self, params, rin, rout, count, id_offset=0, seed=None):
        self.params = params
        self.rin = rin
        self.rout = rout
        self.count = int(count)
        self.id_offset = id_offset
        self.seed = seed

    def emit(self):
        params = self.params
        if self.seed is not None:
            np.random.seed(self.seed)
        rays = sources.annulus(self.rin, self.rout, self.count)

        start_z = params.z0 + params.primary_length + 500.
        tran.transform(rays, 0, 0, -start_z, 0, 0, 0)

        theta = params.offaxis * _ARCMIN
        phi = np.radians(params.azimuth)
        if theta != 0.:
            n_rays = len(rays[1])
            rays[4] = np.repeat(np.sin(theta) * np.cos(phi), n_rays)
            rays[5] = np.repeat(np.sin(theta) * np.sin(phi), n_rays)
            rays[6] = np.repeat(-np.cos(theta), n_rays)
        return rays


class WolterPrimary(optics.SurfaceOfRevolution):
    """The paraboloid, spanning z0 -> z0 + primary_length."""

    key = 'primary'
    label = 'the primary'
    miss_label = 'the primary mirror'
    title = 'Primary'
    kind = 'mirror'

    def __init__(self, params, r0=None):
        self.params = params
        self.r0 = params.r0 if r0 is None else r0
        self.zlo = params.z0
        self.zhi = params.z0 + params.primary_length
        self.aperture = optics.AxialExtent(self.zlo, self.zhi)

    def trace_to(self, rays):
        surf.wolterprimary(rays, self.r0, self.params.z0, psi=self.params.psi)

    def radius_at(self, z):
        return conic.primrad(z, self.r0, self.params.z0, psi=self.params.psi)


class WolterSecondary(optics.SurfaceOfRevolution):
    """
    The hyperboloid, spanning z0 - secondary_length -> z0.

    This is the element the misalignment knobs move.  Its aperture is tested
    in the parent frame, after the placement is popped -- see
    ``Element.extent_frame`` for why that is preserved rather than chosen.
    """

    key = 'secondary'
    label = 'the secondary'
    miss_label = 'the secondary mirror'
    title = 'Secondary'
    kind = 'secondary'

    def __init__(self, params, r0=None):
        self.params = params
        self.r0 = params.r0 if r0 is None else r0
        self.zlo = params.z0 - params.secondary_length
        self.zhi = params.z0
        self.aperture = optics.AxialExtent(self.zlo, self.zhi)
        self.placement = optics.Placement(*params.misalignment())

    def trace_to(self, rays):
        surf.woltersecondary(rays, self.r0, self.params.z0,
                             psi=self.params.psi)

    def radius_at(self, z):
        return conic.secrad(z, self.r0, self.params.z0, psi=self.params.psi)


def shell_radii_all(params):
    """
    Node radius of every shell in the nest, close-packed outward from r0.

    A shell's outer edge is its primary's radius at the top, so the next
    shell starts there plus the wall thickness.  ``conic.primrad(z0, r0, z0)``
    is exactly ``r0``, which is what makes this a forward recurrence rather
    than a root-find: each shell is placed directly, not solved for.
    """
    count = max(1, int(getattr(params, 'num_shells', 1)))
    gap = float(getattr(params, 'shell_gap', 1.))
    out = []
    r = params.r0
    for _ in range(count):
        out.append(r)
        r = conic.primrad(params.z0 + params.primary_length, r, params.z0,
                          psi=params.psi) + gap
    return out


def _split_rays(total, areas):
    """
    Divide ``total`` rays among shells in proportion to collecting area.

    Largest remainder, so the parts sum to exactly ``total``.  Two reasons
    the total is what is fixed, rather than the per-shell count: trace time
    then does not grow with shell count, which matters behind a 250 ms
    auto-trace debounce; and the spot keeps a constant number of points, so
    adding a shell changes the image because of optics rather than because
    of sampling.
    """
    total_area = float(sum(areas))
    if total_area <= 0.:
        return [0] * len(areas)
    exact = [total * area / total_area for area in areas]
    counts = [int(math.floor(value)) for value in exact]
    short = total - sum(counts)
    order = sorted(range(len(areas)),
                   key=lambda i: exact[i] - counts[i], reverse=True)
    for i in order[:short]:
        counts[i] += 1
    return counts


def build_system(params):
    """
    Turn a :class:`WolterParams` into an :class:`optics.System`.

    This is the only place that knows what a ``WolterParams`` *is*.  Panel,
    configuration, sweeps and session persistence all keep talking in
    parameters; :func:`optics.trace_system` only ever sees a system.  A future
    per-element editor would emit a ``System`` directly and need no change to
    the tracer at all -- that is the whole point of the split, so please do
    not reintroduce a shortcut around it.

    Each shell of a nest becomes one channel: its own entrance annulus, its
    own pair of surfaces, and its own slice of the ray budget.  Rays do not
    pass between shells, which is a real simplification -- a ray leaving one
    shell and striking its neighbour needs a branching tracer -- and the
    standard one at this level of modelling.

    A degenerate aperture is reported on the system rather than raised, so
    that one impossible shell does not take a whole design with it.
    """
    radii = shell_radii_all(params)

    usable = []
    notes = []
    for index, r0 in enumerate(radii):
        rin, rout = shell_radii(params, r0)
        if not np.isfinite(rin) or not np.isfinite(rout) or rout <= rin:
            notes.append('Shell %d (r0 = %.4g mm) has no aperture and was '
                         'skipped.' % (index + 1, r0))
            continue
        usable.append((index, r0, rin, rout))

    if not usable:
        return optics.System(
            [], optics.AutoFocus(), params.z0, 0., label='Wolter-I',
            problem=('Invalid geometry: the primary has no aperture. '
                     'Check r0, focal length and psi.'))

    # Geometric aperture of each annulus, in cm^2.
    areas = [np.pi * (rout ** 2 - rin ** 2) / 100.
             for _, _, rin, rout in usable]
    counts = _split_rays(int(params.num_rays), areas)

    channels = []
    offset = 0
    for (index, r0, rin, rout), count in zip(usable, counts):
        if count <= 0:
            notes.append('Shell %d (r0 = %.4g mm) got no rays; raise the ray '
                         'count.' % (index + 1, r0))
            continue
        # Seeding per shell, offset by its index, so that adding an outer
        # shell leaves the inner shells' ray patterns exactly as they were --
        # otherwise every comparison between two designs is contaminated by a
        # reshuffle. Shell 0 keeps the bare seed, so one shell is unchanged.
        seed = None if params.seed is None else params.seed + index
        source = WolterSource(params, rin, rout, count, id_offset=offset,
                              seed=seed)
        channels.append(optics.Channel(source, [WolterPrimary(params, r0),
                                                WolterSecondary(params, r0)]))
        offset += count

    common = []
    if int(getattr(params, 'use_grating', 0)):
        common.append(build_grating(params))

    # The lever arm that turns millimetres at the image into arcseconds is
    # node to image plane, not node to origin. They differ as soon as the
    # detector is placed rather than solved for.
    focal_length = params.z0
    if int(getattr(params, 'use_detector', 0)):
        terminator = build_detector(params)
        focal_length = params.z0 - float(params.det_z)
    else:
        terminator = optics.AutoFocus()

    label = ('Wolter-I' if len(channels) == 1
             else 'Wolter-I, %d shells' % len(channels))
    if common:
        label += ' + grating'
    system = optics.System(channels, terminator, focal_length, sum(areas),
                           label=label, warnings=notes, common=common)

    # Ids are handed out in blocks wide enough for every order in flight, so
    # that a grating can fan a ray into slots inside its own block. Must come
    # after the system exists and before anything launches.
    stride = system.assign_order_slots()
    for channel in system.channels:
        channel.source.id_stride = stride
    return system


def traced_orders(params):
    """
    Which orders this design puts in flight, low to high.

    The reference order is always among them: it is what every metric is
    measured on, and a span that excluded it would report NaN for all of
    them without saying why.
    """
    span = int(getattr(params, 'grating_order_span', 0))
    reference = int(params.grating_order)
    if span <= 0:
        # Not range(-0, 1), which is [0] -- that would quietly add the
        # undiffracted beam to every single-order design and halve the
        # measured dispersion by averaging the two.
        return (reference,)
    return tuple(sorted(set(range(-span, span + 1)) | {reference}))


def build_detector(params):
    """
    The image surface this design asks for, flat or bent.

    Both tilts are applied, and the y one is the interesting one: dispersion
    runs along x, so a tilt about y is what leans the detector along it.
    """
    placement = optics.Placement(
        0., 0., float(params.det_z),
        np.radians(float(params.det_tilt)),
        np.radians(float(getattr(params, 'det_tilt_y', 0.))), 0.)
    # 0 means unbounded, which is what a detector with no size has always
    # been here -- it caught every ray however far off and drew nothing.
    half_width = float(getattr(params, 'det_size', 0.)) or None

    if int(getattr(params, 'det_shape', 0)):
        return optics.CurvedDetector(
            placement, half_width=half_width,
            radius=float(getattr(params, 'det_radius', 200.)))
    return optics.DetectorPlane(placement, half_width=half_width)


def build_grating(params):
    """The grating this design asks for, linear or radial."""
    placement = optics.Placement(0., 0., float(params.grating_z), 0., 0., 0.)
    orders = traced_orders(params)
    shared = dict(half_width=float(params.grating_size), orders=orders)
    if int(getattr(params, 'grating_type', 0)):
        return optics.RadialGrating(
            placement, float(getattr(params, "grating_dpermm", 200. / 8400.)),
            int(params.grating_order), params.wavelength,
            hub_offset=float(getattr(params, "grating_hub", 8400.)), **shared)
    return optics.LinearGrating(
        placement, params.grating_period, int(params.grating_order),
        params.wavelength, **shared)


def trace(params, record_paths=True, num_paths=40):
    """
    Trace a Wolter-I shell and measure its focus.

    Parameters
    ----------
    params : WolterParams
        System definition.
    record_paths : bool
        Capture ray positions at each surface for the layout plot.
    num_paths : int
        How many rays to record paths for.

    Returns
    -------
    TraceResult
        Rays at best focus and the derived performance metrics.  If every
        ray vignettes, ``message`` explains it and the metrics stay NaN.
    """
    check_misalignment(params)
    return optics.trace_system(build_system(params), params,
                               record_paths=record_paths,
                               num_paths=num_paths)


_SCRIPT_HEAD = '''"""Equivalent PyXFocus script for the current settings."""
import numpy as np
import PyXFocus.sources as sources
import PyXFocus.surfaces as surf
import PyXFocus.transformations as tran
import PyXFocus.analyses as anal
import PyXFocus.conicsolve as conic

r0, z0 = {r0!r}, {z0!r}
primary_length, secondary_length, psi = {pl!r}, {sl!r}, {psi!r}

# Secondary misalignment: mm, then radians.
misalign = ({dx!r}, {dy!r}, {dz!r},
            np.radians({rx!r} / 60.), np.radians({ry!r} / 60.),
            np.radians({rz!r} / 60.))

# Off-axis source: {off!r} arcmin at azimuth {az!r} deg.
theta = np.radians({off!r} / 60.)
phi = np.radians({az!r})


def drop_dead(rays):
    """Remove rays the Fortran solver abandoned; it zeroes their cosines.

    Must run straight after the surface call. Left in, these are swallowed
    by the mirror-extent test and miscounted as rays that missed, and one
    survivor with n = 0 turns anal.hpd into NaN.
    """
    alive = rays[4] ** 2 + rays[5] ** 2 + rays[6] ** 2 >= 0.1
    return tran.vignette(rays, ind=alive)


# One entry per shell, close-packed outward from r0, with the ray budget
# split between them by collecting area.
shells = {shells!r}

bundles = []
for index, (shell_r0, count) in enumerate(shells):
    rin = conic.primrad(z0, shell_r0, z0, psi=psi)
    rout = conic.primrad(z0 + primary_length, shell_r0, z0, psi=psi)
{seed_line}
    rays = sources.annulus(rin, rout, count)
    tran.transform(rays, 0, 0, -(z0 + primary_length + 500.), 0, 0, 0)
    if theta:
        n_rays = len(rays[1])
        rays[4] = np.repeat(np.sin(theta) * np.cos(phi), n_rays)
        rays[5] = np.repeat(np.sin(theta) * np.sin(phi), n_rays)
        rays[6] = np.repeat(-np.cos(theta), n_rays)

    # Primary.
    surf.wolterprimary(rays, shell_r0, z0, psi=psi)
    rays = drop_dead(rays)
    tran.reflect(rays)
    ind = np.logical_and(rays[3] > z0, rays[3] < z0 + primary_length)
    rays = tran.vignette(rays, ind=ind)

    # Secondary, in its misaligned frame.
    tran.transform(rays, *misalign)
    surf.woltersecondary(rays, shell_r0, z0, psi=psi)
    rays = drop_dead(rays)
    tran.reflect(rays)
    tran.itransform(rays, *misalign)
    ind = np.logical_and(rays[3] > z0 - secondary_length, rays[3] < z0)
    rays = tran.vignette(rays, ind=ind)
    bundles.append(rays)

rays = [np.concatenate([b[i] for b in bundles]) for i in range(10)]
good = np.isfinite(rays[1]) & np.isfinite(rays[2]) & np.isfinite(rays[3])
rays = tran.vignette(rays, ind=good)
'''

_SCRIPT_FAN = '''
# {n_orders!r} diffraction orders from one beam: every ray is copied once
# per order, so `ray_order` stays parallel to `rays` through every vignette
# below. Order {order!r} is the reference -- the one the metrics are measured
# on -- and the rest are along to be looked at.
orders = {orders!r}
rays = [np.repeat(component, len(orders)) for component in rays]
ray_order = np.tile(np.array(orders, dtype=float), len(rays[1]) // len(orders))
'''

_SCRIPT_GRATING = '''
# Linear grating, {size!r} mm half-width, at z = {gz!r} mm.
tran.transform(rays, 0, 0, {gz!r}, 0, 0, 0)
surf.flat(rays){fan}
count = len(rays[1])
tran.grat(rays, {period!r}, {order_arg},
          np.repeat({wave!r}, count))
{keep_dead}rays = drop_dead(rays)
ind = np.logical_and(np.abs(rays[1]) <= {size!r}, np.abs(rays[2]) <= {size!r})
{keep_ind}rays = tran.vignette(rays, ind=ind)
tran.itransform(rays, 0, 0, {gz!r}, 0, 0, 0)
'''

_SCRIPT_RADIAL = '''
# Radial grating, {size!r} mm half-width, at z = {gz!r} mm.
# tran.radgrat puts the hub at the LOCAL ORIGIN, so the local period is
# {dpermm!r} nm/mm times the distance from it and the dispersion is azimuthal.
# The hub shift wraps the diffraction ONLY: the grating's own extent is
# measured about the grating, not about its hub, so an aperture test inside
# the shifted frame would vignette every ray {hub_mm} mm off centre.
tran.transform(rays, 0, 0, {gz!r}, 0, 0, 0)
surf.flat(rays){fan}
{hub_in}{diffract}{hub_out}{keep_dead}rays = drop_dead(rays)
ind = np.logical_and(np.abs(rays[1]) <= {size!r}, np.abs(rays[2]) <= {size!r})
{keep_ind}rays = tran.vignette(rays, ind=ind)
tran.itransform(rays, 0, 0, {gz!r}, 0, 0, 0)
'''

#: radgrat takes order and wavelength as scalars, so a fan goes one order at
#: a time. Never radgratW, whose sign of n comes from the sign of y.
_SCRIPT_RADIAL_FAN = '''for m in np.unique(ray_order):
    tran.radgrat(rays, {dpermm!r}, float(m), {wave!r}, ind=(ray_order == m))
'''
_SCRIPT_RADIAL_ONE = '''tran.radgrat(rays, {dpermm!r}, float({order!r}), {wave!r})
'''

#: Keeps ray_order aligned with rays across a cut. `drop_dead` and
#: `tran.vignette` both return a subsequence, never a prefix, so an index
#: array is the only thing that stays right.
_SCRIPT_KEEP_DEAD = '''alive = rays[4]**2 + rays[5]**2 + rays[6]**2 >= 0.1
ray_order = ray_order[alive]
'''
_SCRIPT_KEEP_IND = 'ray_order = ray_order[ind]\n'

_SCRIPT_DETECTOR = '''
# Detector at z = {dz!r} mm, tilted {tilt!r} deg about x, {tilty!r} about y.
tran.transform(rays, 0, 0, {dz!r}, np.radians({tilt!r}), np.radians({tilty!r}), 0)
surf.flat(rays){curve}{clip}
image_z = {dz!r}
'''

#: Bend the detector along the dispersion. surf.flat first is not cosmetic:
#: surf.cyl takes the root on the side the ray started, and rays arriving
#: from the grating are on the wrong one -- solving directly puts every ray
#: on the far branch, which measures 723 arcsec per order instead of 0.4.
_SCRIPT_CURVE = '''
tran.transform(rays, 0, 0, {radius!r}, 0, 0, 0)
surf.cyl(rays, {radius!r})
tran.itransform(rays, 0, 0, {radius!r}, 0, 0, 0)'''

_SCRIPT_DETECTOR_CLIP = '''
ind = np.logical_and(np.abs(rays[1]) <= {size!r}, np.abs(rays[2]) <= {size!r})
{keep}rays = tran.vignette(rays, ind=ind)'''

_SCRIPT_AUTOFOCUS = '''
# Best focus, found from the rays.
image_z = surf.focusI(rays)
'''

#: Weighted to the reference order. Unweighted, the plane lands between the
#: dispersed spots, where none of them is in focus: measured at HPD 0.089 ->
#: 237 arcsec with seven orders in flight.
_SCRIPT_AUTOFOCUS_WEIGHTED = '''
# Best focus, found from the REFERENCE order alone.
reference = ray_order == {order!r}
image_z = surf.focusI(rays, weights=reference.astype(float))
'''

_SCRIPT_TAIL = '''
focal_length = {focal!r}
hpd_arcsec = anal.hpd(rays) / focal_length * 180. / np.pi * 3600.
print("rays surviving:", len(rays[1]))
print("HPD [arcsec]:", hpd_arcsec)
'''

#: The metrics are measured on the reference order, the others only drawn.
_SCRIPT_TAIL_WEIGHTED = '''
focal_length = {focal!r}
measured = tran.vignette(rays, ind=(ray_order == {order!r}))
hpd_arcsec = anal.hpd(measured) / focal_length * 180. / np.pi * 3600.
print("rays surviving:", len(measured[1]))
print("rays surviving, all orders:", len(rays[1]))
print("HPD [arcsec]:", hpd_arcsec)
'''


def script_for(params):
    """
    The equivalent bare-PyXFocus script for ``params``, as text.

    Generated from :func:`build_system` rather than kept as a fixed template
    beside it.  The template it replaced was a second, hand-maintained
    transcription of the trace, and by the time nesting, gratings and placed
    detectors existed it described a telescope nobody had asked for while
    still claiming to be "the equivalent script".

    Shell radii and per-shell ray counts are emitted as literals so that the
    script does not have to re-derive the close-packing and area-splitting
    rules -- one fewer thing that can drift.
    """
    system = build_system(params)
    if system.problem:
        return '# %s\n' % system.problem

    shells = [(channel.elements[0].r0, channel.source.count)
              for channel in system.channels]
    if params.seed is None:
        seed_line = '    # No seed: the ray pattern differs every run.'
    else:
        seed_line = '    np.random.seed(%r + index)' % int(params.seed)

    text = _SCRIPT_HEAD.format(
        r0=params.r0, z0=params.z0, pl=params.primary_length,
        sl=params.secondary_length, psi=params.psi, off=params.offaxis,
        az=params.azimuth, dx=params.sec_dx, dy=params.sec_dy,
        dz=params.sec_dz, rx=params.sec_rx, ry=params.sec_ry,
        rz=params.sec_rz, shells=shells, seed_line=seed_line)

    fanned = False
    for element in system.common:
        fragment = _script_for_element(element)
        if fragment is None:
            text += ('\n# NOTE: %s is in this system but the script exporter '
                     'does not know how to write it out yet.\n'
                     % type(element).__name__)
            continue
        text += fragment
        fanned = fanned or _fans(element)

    if int(getattr(params, 'use_detector', 0)):
        size = float(getattr(params, 'det_size', 0.))
        text += _SCRIPT_DETECTOR.format(
            dz=float(params.det_z), tilt=float(params.det_tilt),
            tilty=float(getattr(params, 'det_tilt_y', 0.)),
            curve=(_SCRIPT_CURVE.format(
                radius=float(getattr(params, 'det_radius', 200.)))
                if int(getattr(params, 'det_shape', 0)) else ''),
            clip=(_SCRIPT_DETECTOR_CLIP.format(
                size=size, keep=_SCRIPT_KEEP_IND if fanned else '')
                if size else ''))
    elif fanned:
        text += _SCRIPT_AUTOFOCUS_WEIGHTED.format(
            order=float(params.grating_order))
    else:
        text += _SCRIPT_AUTOFOCUS

    tail = _SCRIPT_TAIL_WEIGHTED if fanned else _SCRIPT_TAIL
    return text + tail.format(focal=system.focal_length,
                              order=float(params.grating_order))


def _fans(element):
    """True when this element copies each ray into several orders."""
    orders = getattr(element, 'orders', None)
    return orders is not None and len(orders) > 1


def _script_for_element(element):
    """
    The script fragment for one common element, or None if there is none.

    An explicit dispatch, because the loop this replaced formatted *every*
    element in ``system.common`` with the linear-grating template and read
    ``.period``, ``.order`` and ``.wavelength`` off it.  That worked only
    because a grating was the sole thing ever placed there; a radial grating
    has no ``.period`` at all and would have raised AttributeError.

    RadialGrating is tested first on purpose: it is a subclass of
    LinearGrating, so the other order would match it and emit the wrong
    physics rather than fail.
    """
    fan = ''
    keep_dead, keep_ind = '', ''
    if _fans(element):
        fan = '\n' + _SCRIPT_FAN.format(orders=list(element.orders),
                                        n_orders=len(element.orders),
                                        order=element.order).strip('\n')
        keep_dead, keep_ind = _SCRIPT_KEEP_DEAD, _SCRIPT_KEEP_IND

    if isinstance(element, optics.RadialGrating):
        if _fans(element):
            diffract = _SCRIPT_RADIAL_FAN.format(dpermm=element.dpermm,
                                                 wave=element.wavelength)
        else:
            diffract = _SCRIPT_RADIAL_ONE.format(dpermm=element.dpermm,
                                                 order=element.order,
                                                 wave=element.wavelength)
        hub = element.hub_offset
        return _SCRIPT_RADIAL.format(
            gz=element.placement.dz, dpermm=element.dpermm,
            size=element.half_width, fan=fan, diffract=diffract,
            keep_dead=keep_dead, keep_ind=keep_ind, hub_mm=hub,
            hub_in=('tran.transform(rays, 0, %r, 0, 0, 0, 0)\n' % -hub
                    if hub else ''),
            hub_out=('tran.itransform(rays, 0, %r, 0, 0, 0, 0)\n' % -hub
                     if hub else ''))

    if isinstance(element, optics.LinearGrating):
        order_arg = ('ray_order.copy()' if _fans(element)
                     else 'np.repeat(float(%r), count)' % element.order)
        return _SCRIPT_GRATING.format(
            gz=element.placement.dz, period=element.period,
            order=element.order, wave=element.wavelength,
            size=element.half_width, fan=fan, order_arg=order_arg,
            keep_dead=keep_dead, keep_ind=keep_ind)

    return None


def mirror_profile(params, num=200):
    """
    Radius vs z along both mirrors, for drawing the telescope in profile.

    The numbers come from the elements themselves rather than from a second
    copy of the radius law here.  That matters more than it looks: a 3D view
    builds its meshes by sweeping the very same ``profile`` calls, so the two
    views cannot drift into disagreeing about where a mirror is.

    Returns
    -------
    (zp, rp), (zs, rs)
        Primary and secondary profiles.
    """
    (zp, rp), = WolterPrimary(params).profile(num)
    (zs, rs), = WolterSecondary(params).profile(num)
    return (zp, rp), (zs, rs)


def mirror_profiles(params, num=200):
    """
    :func:`mirror_profile` for every shell in the nest, innermost first.

    A single-shell design gives a one-element list whose entry is exactly
    what :func:`mirror_profile` returns, so a caller that draws this list
    draws the old picture unchanged when there is nothing new to draw.
    """
    out = []
    for r0 in shell_radii_all(params):
        (zp, rp), = WolterPrimary(params, r0).profile(num)
        (zs, rs), = WolterSecondary(params, r0).profile(num)
        out.append(((zp, rp), (zs, rs)))
    return out


def mirror_z_range(params):
    """
    Axial span of the optics, for a view that wants to zoom on them.

    Asks the system rather than recomputing ``z0`` plus a mirror length, so
    that a design with more surfaces -- or with surfaces that are not Wolter
    conics -- widens the zoom without the caller learning anything new.
    """
    span = build_system(params).mirror_z_range()
    if span is None:
        # A degenerate design builds no channels at all. Fall back to the
        # nominal extents so a viewer still has something to scale to.
        return (params.z0 - params.secondary_length,
                params.z0 + params.primary_length)
    return span


#: One tunable parameter: how it is labelled, its range, and how finely it
#: is edited.  ``lo``/``hi`` are None for parameters a configuration carries
#: but the panel gives no field of its own (currently just ``seed``).
ParamSpec = collections.namedtuple(
    'ParamSpec', 'name label unit default lo hi decimals step choices')
#: ``choices`` names the options of a field that is a choice rather than a
#: quantity, and is None for every ordinary one.  The panel draws a combo box
#: for it and stores the selected *index*, which is why such a field is also
#: an integer -- see INT_FIELDS.
ParamSpec.__new__.__defaults__ = (None,)

#: Every parameter the explorer exposes, in panel order.
#:
#: This lives here rather than inside the Qt widget that draws it so that
#: anything Qt-free -- the config loader, the presets, the tests -- can
#: validate against the same ranges the fields enforce.  A value a file
#: offers and a value a spin box will accept then cannot drift apart.
#: ``wolter`` already owns display metadata (see SWEEPABLE below), so this
#: follows the module's existing habit rather than introducing a new one.
PARAM_SPECS = (
    ParamSpec('r0', 'Shell radius r₀', 'mm', 220., 1., 5000., 3, 5.),
    ParamSpec('z0', 'Focal length z₀', 'mm', 8400., 100., 100000., 1, 100.),
    ParamSpec('primary_length', 'Primary length', 'mm', 100., 1., 2000., 1, 10.),
    ParamSpec('secondary_length', 'Secondary length', 'mm', 100., 1., 2000., 1, 10.),
    ParamSpec('psi', 'Prescription ψ', '', 1., 0.1, 10., 3, 0.1),
    ParamSpec('num_shells', 'Number of shells', '', 1, 1, 50, 0, 1),
    ParamSpec('shell_gap', 'Shell wall + gap', 'mm', 1., 0.01, 50., 3, 0.1),

    ParamSpec('offaxis', 'Off-axis angle', 'arcmin', 0., 0., 120., 3, 0.5),
    ParamSpec('azimuth', 'Azimuth', 'deg', 0., 0., 360., 1, 15.),
    ParamSpec('num_rays', 'Number of rays', '', 20000., 100., 500000., 0, 5000.),

    #: Misalignment ranges are capped by the backend's safe limits --
    #: see check_misalignment for why those limits exist.
    ParamSpec('sec_dx', 'Shift x', 'mm', 0.,
              -MAX_TRANSLATION_MM, MAX_TRANSLATION_MM, 4, 0.01),
    ParamSpec('sec_dy', 'Shift y', 'mm', 0.,
              -MAX_TRANSLATION_MM, MAX_TRANSLATION_MM, 4, 0.01),
    ParamSpec('sec_dz', 'Shift z', 'mm', 0.,
              -MAX_TRANSLATION_MM, MAX_TRANSLATION_MM, 4, 0.01),
    ParamSpec('sec_rx', 'Tilt about x', 'arcmin', 0.,
              -MAX_ROTATION_ARCMIN, MAX_ROTATION_ARCMIN, 4, 0.05),
    ParamSpec('sec_ry', 'Tilt about y', 'arcmin', 0.,
              -MAX_ROTATION_ARCMIN, MAX_ROTATION_ARCMIN, 4, 0.05),
    ParamSpec('sec_rz', 'Tilt about z', 'arcmin', 0.,
              -MAX_ROTATION_ARCMIN, MAX_ROTATION_ARCMIN, 4, 0.05),

    ParamSpec('grating_type', 'Grating type', '', 0, 0, 1, 0, 1,
              ('Linear', 'Radial')),
    ParamSpec('grating_z', 'Grating position z', 'mm', 500., 1., 100000., 1, 25.),
    ParamSpec('grating_period', 'Groove period (linear)', 'nm', 200., 1.,
              100000., 2, 10.),
    # Defaults chosen together: a hub 8400 mm off-axis with a gradient of
    # 200/8400 nm/mm gives a 200 nm period at the hub, so switching a design
    # from Linear to Radial compares like with like.  The hub is deliberately
    # far outside the beam -- at z = 500 mm the beam is a 13 mm annulus
    # centred on the axis, so a hub near it would put the grating almost on
    # top of its own convergence point, where the local period runs to zero
    # and the image smears to hundreds of arcseconds.  Measured: 0.4 arcsec
    # at a hub of 8400 mm, 6 at 500 mm, 233 at 0.
    ParamSpec('grating_dpermm', 'Period gradient (radial)', 'nm/mm',
              200. / 8400., 1e-5, 10000., 5, 0.005),
    ParamSpec('grating_hub', 'Hub offset (radial)', 'mm', 8400., 0., 100000.,
              1, 100.),
    ParamSpec('grating_order', 'Order', '', 1, -20, 20, 0, 1),
    ParamSpec('grating_order_span', 'Extra orders +/-', '', 0, 0, 6, 0, 1),
    ParamSpec('wavelength', 'Wavelength', 'nm', 2., 0., 100., 4, 0.5),
    ParamSpec('grating_size', 'Grating half-width', 'mm', 120., 1., 5000., 1, 10.),

    ParamSpec('det_shape', 'Detector shape', '', 0, 0, 1, 0, 1,
              ('Flat', 'Cylindrical')),
    ParamSpec('det_z', 'Detector position z', 'mm', 0., -1000., 1000., 3, 1.),
    # Default 0 means "unbounded", which is what this has always been: the
    # detector had no size at all and caught every ray however far off. Any
    # positive default would vignette, and every existing result would move.
    ParamSpec('det_size', 'Detector half-width', 'mm', 0., 0., 5000., 1, 5.),
    ParamSpec('det_tilt', 'Detector tilt about x', 'deg', 0., -80., 80., 3, 1.),
    # Dispersion runs along x, so this is the tilt that leans the detector
    # along it -- the one that can follow a dispersed focal surface. The x
    # tilt above cannot: it rotates perpendicular to the dispersion.
    ParamSpec('det_tilt_y', 'Detector tilt about y', 'deg', 0., -80., 80.,
              3, 1.),
    # 200 mm is the measured optimum for the default design (a 500 mm lever
    # and a 200 nm period). It is NOT a constant: the best radius follows
    # grating_z, period and wavelength, so it is worth sweeping for any
    # design you care about. Below ~175 mm it degrades sharply.
    ParamSpec('det_radius', 'Detector radius (curved)', 'mm', 200., 1.,
              1000000., 1, 10.),

    #: Carried through configurations but given no field of their own.
    #:
    #: ``seed`` so that a saved seed survives a round trip instead of
    #: silently reverting to 0; the two ``use_`` flags because they are
    #: driven by their group's checkbox rather than by a spin box.
    ParamSpec('use_grating', 'Grating fitted', '', 0, None, None, 0, 1),
    ParamSpec('use_detector', 'Detector placed', '', 0, None, None, 0, 1),
    ParamSpec('seed', 'Random seed', '', 0, None, None, 0, 1),
)

#: How the panel groups the fields, as (heading, parameter names, enable).
#:
#: ``enable`` is None for a group that is always on, or the name of a 0/1
#: parameter the group's own checkbox drives.  An optional part of the
#: instrument is then one entry here rather than a special case in the panel.
PARAM_GROUPS = (
    ('Geometry', ('r0', 'z0', 'primary_length', 'secondary_length', 'psi',
                  'num_shells', 'shell_gap'), None),
    ('Source', ('offaxis', 'azimuth', 'num_rays'), None),
    ('Secondary misalignment', ('sec_dx', 'sec_dy', 'sec_dz',
                                'sec_rx', 'sec_ry', 'sec_rz'), None),
    # A type choice is a field *inside* an optional group, not a second kind
    # of gate: the group's checkbox still answers "is there a grating".
    ('Grating', ('grating_type', 'grating_z', 'grating_period',
                 'grating_dpermm', 'grating_hub', 'grating_order',
                 'grating_order_span', 'wavelength', 'grating_size'),
     'use_grating'),
    ('Detector', ('det_shape', 'det_z', 'det_size', 'det_tilt',
                  'det_tilt_y', 'det_radius'), 'use_detector'),
)

#: Field names carried in a saved configuration, in a stable order.
PARAM_FIELDS = tuple(spec.name for spec in PARAM_SPECS)

#: Fields stored as integers rather than floats. Public because the
#: parameter panel needs the same list to round its spin boxes, and a
#: second copy over there is exactly how num_shells arrived as 3.0000001.
INT_FIELDS = frozenset(('num_rays', 'num_shells', 'grating_order',
                        'grating_order_span', 'grating_type', 'det_shape',
                        'use_grating', 'use_detector', 'seed'))

_SPEC_BY_NAME = dict((spec.name, spec) for spec in PARAM_SPECS)


def param_spec(name):
    """The :class:`ParamSpec` for ``name``."""
    return _SPEC_BY_NAME[name]


#: Parameters a sweep can vary: name -> (label, unit).
#: Ordered so the alignment terms, which are what tolerancing actually
#: cares about, sit together at the end.
SWEEPABLE = [
    ('offaxis', 'Off-axis angle', 'arcmin'),
    ('azimuth', 'Azimuth', 'deg'),
    ('r0', 'Shell radius r0', 'mm'),
    ('z0', 'Focal length z0', 'mm'),
    ('primary_length', 'Primary length', 'mm'),
    ('secondary_length', 'Secondary length', 'mm'),
    ('psi', 'Prescription psi', ''),
    ('num_shells', 'Number of shells', ''),
    ('shell_gap', 'Shell wall + gap', 'mm'),
    ('sec_dx', 'Secondary shift x', 'mm'),
    ('sec_dy', 'Secondary shift y', 'mm'),
    ('sec_dz', 'Secondary shift z', 'mm'),
    ('sec_rx', 'Secondary tilt x', 'arcmin'),
    ('sec_ry', 'Secondary tilt y', 'arcmin'),
    ('sec_rz', 'Secondary tilt z', 'arcmin'),
    ('det_z', 'Detector position z', 'mm'),
    ('det_tilt', 'Detector tilt about x', 'deg'),
    ('wavelength', 'Wavelength', 'nm'),
]


#: A useful default range to sweep each parameter over, as (start, stop).
#:
#: Entries whose sensible range depends on the design are stored as
#: callables of the baseline params, so radius and focal length scale with
#: the system rather than being fixed numbers that only suit the default.
#:
#: Lives here beside SWEEPABLE rather than in the GUI so the two cannot
#: drift: a name in one and not the other is a bug that
#: test_every_sweepable_parameter_has_a_default_range catches, and scripted
#: sweeps get the same defaults the GUI offers.
SWEEP_RANGES = {
    'offaxis': (0., 10.),
    'azimuth': (0., 360.),
    'r0': (lambda p: p.r0 * .5, lambda p: p.r0 * 1.5),
    'z0': (lambda p: p.z0 * .8, lambda p: p.z0 * 1.2),
    'primary_length': (10., 300.),
    'secondary_length': (10., 300.),
    'psi': (0.5, 2.0),
    'num_shells': (1., 12.),
    'shell_gap': (0.5, 10.),
    'sec_dx': (0., 1.), 'sec_dy': (0., 1.), 'sec_dz': (0., 1.),
    'sec_rx': (0., 2.), 'sec_ry': (0., 2.), 'sec_rz': (0., 2.),
    # A through-focus scan: the classic use for sweeping a placed detector.
    'det_z': (-10., 10.),
    'det_tilt': (0., 20.),
    'wavelength': (0.5, 5.),
}


def sweep_range(name, params):
    """
    Default start and stop for sweeping ``name``, as two floats.

    Resolves the design-dependent entries against ``params``, so callers
    never have to know which ranges are callables.

    The (0., 1.) fallback should be unreachable --
    test_every_sweepable_parameter_has_a_default_range asserts every
    sweepable name has an entry -- and is kept as the belt to that braces.
    """
    low, high = SWEEP_RANGES.get(name, (0., 1.))
    if callable(low):
        low = low(params)
    if callable(high):
        high = high(params)
    return float(low), float(high)


def sweep_label(name):
    """Human label and unit for a sweepable parameter name."""
    for key, label, unit in SWEEPABLE:
        if key == name:
            return label, unit
    raise ValueError('%r is not a sweepable parameter' % name)


class SweepResult(object):
    """
    Performance vs. one varied parameter.

    Points that could not be traced -- a misalignment past the guard, a
    geometry where every ray vignettes -- are left as NaN and their reason
    recorded in ``notes``, so one bad point never aborts the sweep.
    """

    def __init__(self, params, name, values):
        self.params = params
        self.name = name
        self.values = np.asarray(values, dtype=float)
        n = len(self.values)
        self.hpd_arcsec = np.full(n, np.nan)
        self.rms_arcsec = np.full(n, np.nan)
        self.throughput = np.full(n, np.nan)
        self.collecting_area = np.full(n, np.nan)
        self.num_surviving = np.zeros(n, dtype=int)
        #: Rays lost to solver non-convergence at each point. Where this is
        #: nonzero the metrics at that point are lower bounds.
        self.nonconverged = np.zeros(n, dtype=int)
        self.notes = {}
        self.completed = 0

    @property
    def label(self):
        label, unit = sweep_label(self.name)
        return '%s [%s]' % (label, unit) if unit else label

    @property
    def valid(self):
        """Mask of points that traced successfully."""
        return np.isfinite(self.hpd_arcsec)

    def truncate(self, n):
        """Drop everything from index ``n`` on, after a cancel."""
        self.values = self.values[:n]
        self.hpd_arcsec = self.hpd_arcsec[:n]
        self.rms_arcsec = self.rms_arcsec[:n]
        self.throughput = self.throughput[:n]
        self.collecting_area = self.collecting_area[:n]
        self.num_surviving = self.num_surviving[:n]
        self.nonconverged = self.nonconverged[:n]
        self.completed = n

    def to_csv(self, path):
        """Write the sweep to a CSV file."""
        label, unit = sweep_label(self.name)
        header = ','.join([
            '%s[%s]' % (self.name, unit) if unit else self.name,
            'hpd_arcsec', 'rms_arcsec', 'throughput',
            'collecting_area_cm2', 'rays_surviving', 'rays_nonconverged'])
        rows = [header]
        for i in range(len(self.values)):
            rows.append('%g,%g,%g,%g,%g,%d,%d' % (
                self.values[i], self.hpd_arcsec[i], self.rms_arcsec[i],
                self.throughput[i], self.collecting_area[i],
                self.num_surviving[i], self.nonconverged[i]))
        with open(path, 'w') as handle:
            handle.write('\n'.join(rows) + '\n')


def sweep(params, name, start, stop, steps, progress=None, should_stop=None):
    """
    Trace the system repeatedly while varying one parameter.

    This is the tolerancing workflow: how far can a mirror shift before the
    image quality budget blows?

    Parameters
    ----------
    params : WolterParams
        Baseline configuration; every point starts from a copy of this.
    name : str
        Parameter to vary. Must appear in :data:`SWEEPABLE`.
    start, stop : float
        Range to sweep over, inclusive.
    steps : int
        Number of points.
    progress : callable, optional
        Called as ``progress(done, total)`` after each point.
    should_stop : callable, optional
        Polled before each point; return True to stop early.

    Returns
    -------
    SweepResult
        Metrics at each point, NaN where a point could not be traced.
    """
    sweep_label(name)  # validates the name
    steps = max(2, int(steps))
    values = np.linspace(float(start), float(stop), steps)
    result = SweepResult(params, name, values)

    for i, value in enumerate(values):
        if should_stop is not None and should_stop():
            result.truncate(i)
            break

        point = params.copy()
        setattr(point, name, value)

        try:
            traced = trace(point, record_paths=False)
        except ValueError as err:
            # Guarded misalignment, or otherwise unusable input.
            result.notes[i] = str(err)
        else:
            if traced.message:
                result.notes[i] = traced.message
            else:
                result.hpd_arcsec[i] = traced.hpd_arcsec
                result.rms_arcsec[i] = traced.rms_arcsec
                result.throughput[i] = traced.throughput
                result.collecting_area[i] = traced.collecting_area
                result.num_surviving[i] = traced.num_surviving
                result.nonconverged[i] = traced.num_nonconverged

        result.completed = i + 1
        if progress is not None:
            progress(i + 1, steps)

    return result


def encircled_energy(result, num=200):
    """
    Encircled-energy curve: radius from centroid (arcsec) vs enclosed fraction.
    """
    x, y = result.spot
    if len(x) == 0:
        return np.array([]), np.array([])
    rad = np.sort(np.sqrt(x ** 2 + y ** 2))
    frac = np.arange(1, len(rad) + 1) / float(len(rad))
    if len(rad) > num:
        idx = np.linspace(0, len(rad) - 1, num).astype(int)
        rad, frac = rad[idx], frac[idx]
    return rad / result.focal_length * _ARCSEC_PER_RAD, frac
