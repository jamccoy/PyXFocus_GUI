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

import PyXFocus.analyses as anal
import PyXFocus.conicsolve as conic
import PyXFocus.sources as sources
import PyXFocus.surfaces as surf
import PyXFocus.transformations as tran

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
        Rays launched before vignetting.
    sec_dx ... sec_rz : float
        Secondary mirror misalignment: translations in mm, rotations in
        arcminutes.
    seed : int or None
        Seed for the random ray pattern, so a trace is repeatable.
    """

    def __init__(self, r0=220., z0=8400., primary_length=100.,
                 secondary_length=100., psi=1., offaxis=0., azimuth=0.,
                 num_rays=20000, sec_dx=0., sec_dy=0., sec_dz=0.,
                 sec_rx=0., sec_ry=0., sec_rz=0., seed=0):
        self.r0 = r0
        self.z0 = z0
        self.primary_length = primary_length
        self.secondary_length = secondary_length
        self.psi = psi
        self.offaxis = offaxis
        self.azimuth = azimuth
        self.num_rays = num_rays
        self.sec_dx = sec_dx
        self.sec_dy = sec_dy
        self.sec_dz = sec_dz
        self.sec_rx = sec_rx
        self.sec_ry = sec_ry
        self.sec_rz = sec_rz
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
            elif name in _INT_FIELDS:
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
            setattr(params, name, int(value) if name in _INT_FIELDS else value)

        return params


class TraceResult(object):
    """Rays at focus plus the numbers derived from them."""

    def __init__(self, params):
        self.params = params
        self.rays = None
        #: Ray positions at each stage, for the layout plot.
        #: Ray paths through the system, shape (stage, ray), stages being
        #: launch / primary / secondary / focus. path_z and path_r are what
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
        #: Rays the Fortran surface solver gave up on, per surface.  These
        #: are not geometric misses -- the solver hit its iteration cap and
        #: marked the ray dead.  Counted before the mirror-extent test so
        #: they are never mistaken for rays that simply missed.
        self.nonconverged_primary = 0
        self.nonconverged_secondary = 0
        #: Rays dropped for carrying a non-finite coordinate.
        self.num_nonfinite = 0
        #: Human-readable caveats about the numbers below.
        self.warnings = []

    @property
    def num_nonconverged(self):
        """Total rays lost to solver non-convergence."""
        return self.nonconverged_primary + self.nonconverged_secondary

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
        Fraction of launched rays that made it through both mirrors.

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
        makes it through both mirrors and nothing else -- there is no
        mirror reflectivity in it, because PyXFocus ships no reflectivity
        model.  A real Wolter-I loses roughly 10-20 per cent per bounce
        with a good coating, twice, and far more as photon energy rises,
        so treat this as a hard upper bound.

        Turning it into a true effective area needs a coating reflectivity
        table indexed by graze angle and energy; ``analyses.grazeAngle``
        already supplies the per-ray graze angles.

        Like :attr:`throughput`, this is a lower bound whenever
        :attr:`metrics_are_bounds` is set.
        """
        return self.geometric_area * self.throughput

    @property
    def spot(self):
        """Focal-plane (x, y) in mm, centred on the centroid."""
        if self.rays is None or self.num_surviving == 0:
            return np.array([]), np.array([])
        x, y = self.rays[1], self.rays[2]
        return x - np.mean(x), y - np.mean(y)

    @property
    def spot_arcsec(self):
        """
        The focal-plane spot in arcseconds, as the plots want it.

        ``hpd_arcsec`` and :func:`encircled_energy` are already in
        arcseconds; the spot was the one member of the family that was
        not, which is why the GUI had to reach in here for a private
        constant and divide by z0 itself.
        """
        x, y = self.spot
        scale = _ARCSEC_PER_RAD / self.params.z0
        return x * scale, y * scale


def shell_radii(params):
    """Inner and outer radius of the primary entrance aperture, in mm."""
    rin = conic.primrad(params.z0, params.r0, params.z0, psi=params.psi)
    rout = conic.primrad(params.z0 + params.primary_length,
                         params.r0, params.z0, psi=params.psi)
    return rin, rout


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

    result = TraceResult(params)

    rin, rout = shell_radii(params)
    if not np.isfinite(rin) or not np.isfinite(rout) or rout <= rin:
        result.message = ('Invalid geometry: the primary has no aperture. '
                          'Check r0, focal length and psi.')
        return result

    # Geometric aperture of the annulus, in cm^2.
    result.geometric_area = np.pi * (rout ** 2 - rin ** 2) / 100.

    if params.seed is not None:
        np.random.seed(params.seed)

    rays = sources.annulus(rin, rout, int(params.num_rays))
    result.num_launched = int(params.num_rays)
    #: Launch index of every ray still alive, carried through every
    #: vignette so path samples can be matched up by identity rather than
    #: by position. See _stack_paths for why position does not work.
    ray_ids = np.arange(len(rays[1]))

    # Start the rays above the primary, still travelling in -z.
    start_z = params.z0 + params.primary_length + 500.
    tran.transform(rays, 0, 0, -start_z, 0, 0, 0)

    # Point the beam off-axis.  Direction cosines are set directly so the
    # ray *positions* stay put and only the incoming angle changes.
    theta = params.offaxis * _ARCMIN
    phi = np.radians(params.azimuth)
    if theta != 0.:
        n_rays = len(rays[1])
        rays[4] = np.repeat(np.sin(theta) * np.cos(phi), n_rays)
        rays[5] = np.repeat(np.sin(theta) * np.sin(phi), n_rays)
        rays[6] = np.repeat(-np.cos(theta), n_rays)

    stages = []
    if record_paths:
        stages.append(_sample(rays, ray_ids))

    # --- Primary ---
    surf.wolterprimary(rays, params.r0, params.z0, psi=params.psi)
    rays, result.nonconverged_primary, alive = _drop_dead(rays)
    ray_ids = ray_ids[alive]
    if len(rays[1]) == 0:
        result.message = ('The surface solver failed to converge on the '
                          'primary for every ray.')
        return result
    tran.reflect(rays)
    ind = np.logical_and(rays[3] > params.z0,
                         rays[3] < params.z0 + params.primary_length)
    if not ind.any():
        result.message = 'All rays missed the primary mirror.'
        return result
    rays = tran.vignette(rays, ind=ind)
    ray_ids = ray_ids[ind]
    if record_paths:
        stages.append(_sample(rays, ray_ids))

    # --- Secondary, in its (possibly misaligned) frame ---
    misalign = params.misalignment()
    tran.transform(rays, *misalign)
    surf.woltersecondary(rays, params.r0, params.z0, psi=params.psi)
    rays, result.nonconverged_secondary, alive = _drop_dead(rays)
    ray_ids = ray_ids[alive]
    if len(rays[1]) == 0:
        tran.itransform(rays, *misalign)
        result.message = ('The surface solver failed to converge on the '
                          'secondary for every ray.')
        return result
    tran.reflect(rays)
    tran.itransform(rays, *misalign)

    ind = np.logical_and(rays[3] > params.z0 - params.secondary_length,
                         rays[3] < params.z0)
    if not ind.any():
        result.message = 'All rays missed the secondary mirror.'
        return result
    rays = tran.vignette(rays, ind=ind)
    ray_ids = ray_ids[ind]
    if record_paths:
        stages.append(_sample(rays, ray_ids))

    # Drop any ray that picked up a non-finite position.
    good = np.isfinite(rays[1]) & np.isfinite(rays[2]) & np.isfinite(rays[3])
    result.num_nonfinite = int((~good).sum())
    if not good.all():
        rays = tran.vignette(rays, ind=good)
        ray_ids = ray_ids[good]
    if len(rays[1]) == 0:
        result.message = 'No rays survived the trace.'
        return result

    # --- Best focus ---
    result.focus_z = surf.focusI(rays)
    if record_paths:
        stages.append(_sample(rays, ray_ids))

    result.rays = rays
    result.ray_ids = ray_ids
    result.num_surviving = len(rays[1])
    result.hpd_mm = anal.hpd(rays)
    result.rms_mm = anal.rmsCentroid(rays)
    result.hpd_arcsec = result.hpd_mm / params.z0 * _ARCSEC_PER_RAD
    result.rms_arcsec = result.rms_mm / params.z0 * _ARCSEC_PER_RAD

    if record_paths:
        ids, xs, ys, zs = _stack_paths(stages, num_paths, result.focus_z)
        result.path_ids = ids
        result.path_x, result.path_y, result.path_z = xs, ys, zs
        # path_r keeps its old name and shape so the 2D layout tab needs no
        # change and inherits the alignment fix for free.
        if xs is not None:
            result.path_r = np.hypot(xs, ys)

    _add_warnings(result)
    return result


def _add_warnings(result):
    """Spell out, in words, when the metrics are bounds rather than values."""
    lost = result.num_nonconverged
    if lost:
        where = []
        if result.nonconverged_primary:
            where.append('%d on the primary' % result.nonconverged_primary)
        if result.nonconverged_secondary:
            where.append('%d on the secondary' % result.nonconverged_secondary)
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


def _drop_dead(rays):
    """
    Remove rays the Fortran solver gave up on, and count them.

    The solver marks a non-converged ray by zeroing its direction cosines
    (see the iteration caps in ``woltsurf.f95``).  They must be removed
    here, immediately after the surface call, for two reasons:

    * Left in, they are swallowed by the mirror-extent test further down
      and silently counted as rays that *missed the mirror*.  That is a
      different physical statement and it quietly corrupts throughput.
    * ``analyses.analyticImagePlane`` computes ``x*l/n``.  A dead ray has
      ``n = 0``, so one survivor is enough to turn ``focus_z`` -- and every
      metric derived from it -- into NaN.

    Returns
    -------
    (rays, ndead, alive)
        ``alive`` is the keep-mask, so a caller tracking ray identities can
        index its own arrays the same way vignette indexed the rays.
    """
    alive = rays[4] ** 2 + rays[5] ** 2 + rays[6] ** 2 >= 0.1
    ndead = int((~alive).sum())
    if ndead:
        rays = tran.vignette(rays, ind=alive)
    return rays, ndead, alive


def _sample(rays, ray_ids):
    """
    Copy this surface's positions, and the launch ids they belong to.

    Copies rather than views: the Fortran surface routines and
    ``tran.transform`` mutate ``rays`` in place, so a view would be
    rewritten by the next stage.
    """
    return (ray_ids.copy(),
            np.array(rays[1], dtype=float),
            np.array(rays[2], dtype=float),
            np.array(rays[3], dtype=float))


def _choose_paths(launch_stage, final_ids, num):
    """
    Pick which surviving rays to draw, spread evenly around the aperture.

    Taking the first N would cluster them wherever the random source
    happened to put its low indices; sorting by launch azimuth gives the
    even fan a layout drawing wants.
    """
    ids0, x0, y0, _ = launch_stage
    row = np.searchsorted(ids0, final_ids)
    order = np.argsort(np.arctan2(y0[row], x0[row]))
    if len(order) > num:
        order = order[np.linspace(0, len(order) - 1, num).astype(int)]
    # Sorted, because every lookup below is a searchsorted.
    return np.sort(final_ids[order])


def _stack_paths(stages, num, focus_z):
    """
    Assemble per-stage samples into ``(stages, rays)`` arrays.

    Alignment is by *ray id*, not by position.  ``tran.vignette`` returns
    ``[rays[i][ind] ...]``, so survivors are a subsequence and not a
    prefix -- the previous "truncate every stage to the shortest" scheme
    joined one ray's launch point to a different ray's mirror hit.
    Measured at offaxis=10: of 40 drawn polylines only 3 connected the
    same ray.  That was invisible in the 2D radius plot, because every
    launch and primary radius lies in a 0.65 mm band, and would be
    glaring in 3D where azimuth is visible.

    Each stage's ids are a subset of the previous stage's and both are
    sorted, so ``searchsorted`` recovers each chosen ray's row exactly.
    """
    if not stages:
        return None, None, None, None
    final_ids = stages[-1][0]
    if len(final_ids) == 0:
        return None, None, None, None

    chosen = _choose_paths(stages[0], final_ids, num)
    shape = (len(stages), len(chosen))
    xs = np.empty(shape)
    ys = np.empty(shape)
    zs = np.empty(shape)
    for k, (ids, x, y, z) in enumerate(stages):
        row = np.searchsorted(ids, chosen)
        xs[k], ys[k], zs[k] = x[row], y[row], z[row]

    # focusI moves the coordinate FRAME, not just the rays (surfaces.focus
    # calls tran.transform twice), so at the focus stage every ray sits at
    # z = 0 in the focus frame.  Put it back into global z.
    zs[-1] += focus_z
    return chosen, xs, ys, zs


def mirror_profile(params, num=200):
    """
    Radius vs z along both mirrors, for drawing the telescope in profile.

    Returns
    -------
    (zp, rp), (zs, rs)
        Primary and secondary profiles.
    """
    zp = np.linspace(params.z0, params.z0 + params.primary_length, num)
    rp = conic.primrad(zp, params.r0, params.z0, psi=params.psi)
    zs = np.linspace(params.z0 - params.secondary_length, params.z0, num)
    rs = conic.secrad(zs, params.r0, params.z0, psi=params.psi)
    return (zp, rp), (zs, rs)


#: One tunable parameter: how it is labelled, its range, and how finely it
#: is edited.  ``lo``/``hi`` are None for parameters a configuration carries
#: but the panel gives no field of its own (currently just ``seed``).
ParamSpec = collections.namedtuple(
    'ParamSpec', 'name label unit default lo hi decimals step')

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

    #: Carried through configurations but given no field, so that a saved
    #: seed survives a round trip instead of silently reverting to 0.
    ParamSpec('seed', 'Random seed', '', 0, None, None, 0, 1),
)

#: How the panel groups the fields, as (heading, parameter names).
PARAM_GROUPS = (
    ('Geometry', ('r0', 'z0', 'primary_length', 'secondary_length', 'psi')),
    ('Source', ('offaxis', 'azimuth', 'num_rays')),
    ('Secondary misalignment', ('sec_dx', 'sec_dy', 'sec_dz',
                                'sec_rx', 'sec_ry', 'sec_rz')),
)

#: Field names carried in a saved configuration, in a stable order.
PARAM_FIELDS = tuple(spec.name for spec in PARAM_SPECS)

#: Fields stored as integers rather than floats.
_INT_FIELDS = frozenset(('num_rays', 'seed'))

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
    ('sec_dx', 'Secondary shift x', 'mm'),
    ('sec_dy', 'Secondary shift y', 'mm'),
    ('sec_dz', 'Secondary shift z', 'mm'),
    ('sec_rx', 'Secondary tilt x', 'arcmin'),
    ('sec_ry', 'Secondary tilt y', 'arcmin'),
    ('sec_rz', 'Secondary tilt z', 'arcmin'),
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
    'sec_dx': (0., 1.), 'sec_dy': (0., 1.), 'sec_dz': (0., 1.),
    'sec_rx': (0., 2.), 'sec_ry': (0., 2.), 'sec_rz': (0., 2.),
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
    return rad / result.params.z0 * _ARCSEC_PER_RAD, frac
