#!/usr/bin/env python
"""
Smoke tests: does the package import, and does it still do physics?

Run from the directory *containing* the PyXFocus folder::

    python -m PyXFocus.test_smoke

Deliberately dependency-light (no pytest) so it can be run straight after
building the Fortran extensions to confirm the install is sound.
"""

from __future__ import print_function

import sys
import traceback

import numpy as np

RESULTS = []


def check(name, fn):
    """Run one check, recording pass/fail rather than raising."""
    try:
        fn()
    except Exception:
        RESULTS.append((name, False, traceback.format_exc().strip()))
        print('FAIL  %s' % name)
    else:
        RESULTS.append((name, True, ''))
        print('ok    %s' % name)


def test_imports():
    """Every core module imports without the optional `utilities` package."""
    import PyXFocus.analyses          # noqa: F401
    import PyXFocus.conicsolve        # noqa: F401
    import PyXFocus.lenses            # noqa: F401
    import PyXFocus.sources           # noqa: F401
    import PyXFocus.surfaces          # noqa: F401
    import PyXFocus.transformations   # noqa: F401


def test_wolter_surfaces_bound():
    """woltsurf is actually imported, so the Wolter routines can run."""
    import PyXFocus.surfaces as surf
    assert hasattr(surf.wolt, 'wolterprimary'), 'woltsurf not bound'


def test_optional_dependency_message():
    """A missing optional dep fails late, with a useful message."""
    from PyXFocus._optional import optional_module
    mod = optional_module('definitely_not_installed_xyz', 'testing', 'pip install x')
    try:
        mod.anything
    except ImportError as err:
        assert 'pip install x' in str(err), 'install hint missing'
    else:
        raise AssertionError('expected ImportError')


def test_onaxis_focus_is_sharp():
    """A perfect on-axis Wolter-I focuses to essentially a point."""
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(offaxis=0., num_rays=10000))
    assert result.num_surviving == 10000, 'unexpected vignetting on axis'
    assert result.hpd_arcsec < 0.01, 'on-axis HPD too large: %g' % result.hpd_arcsec
    assert abs(result.focus_z) < 1e-3, 'focus not at origin: %g' % result.focus_z


def test_offaxis_blur_grows():
    """Off-axis coma grows with field angle, and vignetting bites."""
    from PyXFocus.gui.wolter import WolterParams, trace
    hpds, counts = [], []
    for off in (0., 2., 5., 10.):
        r = trace(WolterParams(offaxis=off, num_rays=10000))
        hpds.append(r.hpd_arcsec)
        counts.append(r.num_surviving)
    assert all(np.diff(hpds) > 0), 'HPD should grow off-axis: %s' % hpds
    assert all(np.diff(counts) < 0), 'throughput should fall off-axis: %s' % counts


def test_misalignment_degrades():
    """Displacing the secondary makes the image worse."""
    from PyXFocus.gui.wolter import WolterParams, trace
    clean = trace(WolterParams(offaxis=1., num_rays=10000))
    bent = trace(WolterParams(offaxis=1., sec_dy=0.2, num_rays=10000))
    assert bent.hpd_arcsec > clean.hpd_arcsec, 'misalignment should degrade HPD'


def test_misalignment_guard():
    """The guard blocks inputs that hang the Fortran secondary solver."""
    from PyXFocus.gui.wolter import WolterParams, trace, MAX_TRANSLATION_MM
    try:
        trace(WolterParams(sec_dy=MAX_TRANSLATION_MM * 10))
    except ValueError:
        pass
    else:
        raise AssertionError('expected ValueError for extreme misalignment')


def test_encircled_energy_consistent():
    """The half-power radius from the EE curve matches the reported HPD."""
    from PyXFocus.gui.wolter import WolterParams, trace, encircled_energy
    result = trace(WolterParams(offaxis=5., num_rays=20000))
    rad, frac = encircled_energy(result)
    half_power_radius = rad[np.abs(frac - 0.5).argmin()]
    assert np.isclose(half_power_radius * 2, result.hpd_arcsec, rtol=.05), (
        'EE half-power radius %g disagrees with HPD %g'
        % (half_power_radius, result.hpd_arcsec))


def test_sweep_tracks_offaxis():
    """A sweep reproduces the off-axis trend, point by point."""
    from PyXFocus.gui.wolter import WolterParams, sweep
    result = sweep(WolterParams(num_rays=4000), 'offaxis', 0., 8., 8)
    assert result.valid.all(), 'every off-axis point should trace'
    hpd = result.hpd_arcsec
    assert np.all(np.diff(hpd) > 0), 'HPD should grow across the sweep: %s' % hpd
    assert np.all(np.diff(result.throughput) < 0), 'throughput should fall'


def test_sweep_survives_ungraceable_points():
    """Points past the misalignment guard become NaN, not an exception."""
    from PyXFocus.gui.wolter import WolterParams, sweep, MAX_ROTATION_ARCMIN
    result = sweep(WolterParams(num_rays=2000), 'sec_ry', 0.,
                   MAX_ROTATION_ARCMIN * 3, 7)
    assert result.valid.any(), 'low-tilt points should still trace'
    assert not result.valid.all(), 'high-tilt points should be rejected'
    assert result.notes, 'rejected points should record a reason'


def test_sweep_can_be_cancelled():
    """should_stop truncates the sweep instead of running to completion."""
    from PyXFocus.gui.wolter import WolterParams, sweep
    done = []
    result = sweep(WolterParams(num_rays=2000), 'offaxis', 0., 10., 20,
                   progress=lambda d, t: done.append(d),
                   should_stop=lambda: len(done) >= 4)
    assert len(result.values) == 4, 'expected truncation to 4 points'
    assert result.completed == 4


def test_sweep_csv_roundtrip(tmp_path=None):
    """The CSV export has one header plus one row per point."""
    import os
    import tempfile
    from PyXFocus.gui.wolter import WolterParams, sweep
    result = sweep(WolterParams(num_rays=2000), 'sec_dy', 0., .3, 5)
    path = os.path.join(tempfile.mkdtemp(), 'sweep.csv')
    result.to_csv(path)
    lines = open(path).read().strip().split('\n')
    assert len(lines) == 6, 'expected header + 5 rows, got %d' % len(lines)
    assert lines[0].startswith('sec_dy[mm]'), 'unexpected header: %s' % lines[0]


def test_nonconvergence_is_counted_not_silently_vignetted():
    """
    Solver failures are reported, not disguised as geometric misses.

    r0=5mm is ill-conditioned enough that most rays hit the iteration cap.
    Before the cap existed this hung; before the counting existed the dead
    rays were absorbed by the mirror-extent test and quietly reported as
    rays that missed the mirror, which understated throughput with no hint
    that anything had gone wrong.
    """
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(r0=5., num_rays=2000))
    assert result.num_nonconverged > 0, 'expected solver failures at r0=5'
    assert result.metrics_are_bounds, 'metrics should be flagged as bounds'
    assert result.warnings, 'a non-converged trace must warn'
    assert 'lower bound' in ' '.join(result.warnings)
    total = (result.num_surviving + result.num_nonconverged
             + result.num_nonfinite)
    assert total <= result.num_launched, 'accounting exceeds rays launched'


def test_clean_trace_reports_no_nonconvergence():
    """A well-conditioned system must not raise false alarms."""
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(num_rays=5000))
    assert result.num_nonconverged == 0
    assert result.num_nonfinite == 0
    assert not result.metrics_are_bounds
    assert result.warnings == []


def test_sweep_records_nonconvergence():
    """A sweep carries the per-point non-convergence count into its CSV."""
    import os
    import tempfile
    from PyXFocus.gui.wolter import WolterParams, sweep
    result = sweep(WolterParams(num_rays=1000), 'r0', 3., 60., 8)
    assert (result.nonconverged > 0).any(), 'expected degraded points'
    path = os.path.join(tempfile.mkdtemp(), 'sweep.csv')
    result.to_csv(path)
    lines = open(path).read().strip().split('\n')
    assert lines[0].endswith('rays_nonconverged'), lines[0]
    assert len(lines) == 9


def test_collecting_area_is_geometric_only():
    """Collecting area is aperture x throughput, with no reflectivity in it."""
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(offaxis=2., num_rays=5000))
    expected = result.geometric_area * result.throughput
    assert np.isclose(result.collecting_area, expected), (
        'collecting_area should be geometric_area * throughput')


def test_param_specs_cover_every_field():
    """
    The spec table and WolterParams cannot drift apart.

    This is the test that would have caught `seed`: it is a WolterParams
    field with no entry in the panel, so a saved configuration silently
    reverted it to 0 on reload.
    """
    from PyXFocus.gui.wolter import WolterParams, PARAM_FIELDS, PARAM_SPECS
    fields = set(vars(WolterParams()))
    assert set(PARAM_FIELDS) == fields, (
        'spec/field mismatch: %s' % (set(PARAM_FIELDS) ^ fields))
    assert len(PARAM_SPECS) == len(fields), 'duplicate spec name'


def test_param_spec_defaults_match_constructor():
    """A spec default that disagrees with WolterParams is a silent trap."""
    from PyXFocus.gui.wolter import WolterParams, PARAM_SPECS
    defaults = WolterParams()
    for spec in PARAM_SPECS:
        assert float(spec.default) == float(getattr(defaults, spec.name)), (
            '%s default %r != constructor %r'
            % (spec.name, spec.default, getattr(defaults, spec.name)))


def test_params_dict_roundtrip():
    """to_dict -> json -> from_dict preserves all 15 fields exactly."""
    import json
    from PyXFocus.gui.wolter import WolterParams, PARAM_FIELDS
    p = WolterParams(r0=300., z0=12000., offaxis=2.5, azimuth=90.,
                     sec_dy=0.3, sec_rx=1.25, num_rays=12345, seed=42)
    back = WolterParams.from_dict(json.loads(json.dumps(p.to_dict())))
    for name in PARAM_FIELDS:
        assert getattr(p, name) == getattr(back, name), (
            '%s did not survive: %r -> %r'
            % (name, getattr(p, name), getattr(back, name)))
    assert isinstance(back.num_rays, int) and isinstance(back.seed, int)


def test_params_from_dict_collects_problems():
    """One bad field costs that field, not the whole configuration."""
    from PyXFocus.gui.wolter import WolterParams
    problems = []
    p = WolterParams.from_dict(
        {'r0': 300., 'psi': float('nan'), 'z0': 'banana', 'coating': 'Ir'},
        problems)
    assert p.r0 == 300., 'the good field should still be applied'
    assert p.psi == WolterParams().psi, 'non-finite must fall back to default'
    assert p.z0 == WolterParams().z0, 'non-numeric must fall back to default'
    assert not hasattr(p, 'coating'), 'unknown keys must never be setattr-ed'
    joined = ' '.join(problems)
    for expect in ('coating', 'psi', 'z0'):
        assert expect in joined, 'no problem recorded for %s' % expect


def test_params_from_dict_rejects_non_finite():
    """
    NaN must not reach a spin box.

    json.loads accepts a bare NaN token, and QDoubleSpinBox.setValue(nan)
    silently yields the field MAXIMUM -- so an unguarded NaN would read
    back as a deliberate extreme misalignment.
    """
    import json
    from PyXFocus.gui.wolter import WolterParams
    data = json.loads('{"sec_ry": NaN, "sec_rx": Infinity}')
    p = WolterParams.from_dict(data)
    assert p.sec_ry == 0. and p.sec_rx == 0.


def _config_doc(**parameters):
    """A minimal valid configuration document, as text."""
    import json
    from PyXFocus.gui import config
    return json.dumps({'format': config.FORMAT, 'version': config.VERSION,
                       'parameters': parameters})


def test_every_sweepable_parameter_has_a_default_range():
    """
    SWEEPABLE and SWEEP_RANGES cannot drift apart.

    They used to live in different modules, and sweep_range's fallback
    would silently hand back (0, 1) for a name that had been added to one
    and not the other.
    """
    from PyXFocus.gui.wolter import SWEEPABLE, SWEEP_RANGES
    names = set(name for name, _, _ in SWEEPABLE)
    assert names == set(SWEEP_RANGES), (
        'mismatch: %s' % (names ^ set(SWEEP_RANGES)))


def test_sweep_range_scales_with_the_design():
    """Design-dependent ranges resolve against the params they are given."""
    from PyXFocus.gui.wolter import WolterParams, SWEEPABLE, sweep_range
    assert sweep_range('r0', WolterParams(r0=200.)) == (100., 300.)
    # Calling every one is what actually exercises each lambda.
    for name, _, _ in SWEEPABLE:
        low, high = sweep_range(name, WolterParams())
        assert np.isfinite(low) and np.isfinite(high), name


def test_spot_arcsec_matches_encircled_energy_units():
    """
    The spot and the encircled-energy curve are in the same units.

    np.isclose rather than ==: spot_arcsec computes ``x * (A / z0)`` and
    encircled_energy computes ``(r / z0) * A``. The association order
    differs on purpose -- spot_arcsec keeps the GUI's original expression
    term for term, so moving it out of the widget left the drawn pixels
    bit-identical.
    """
    from PyXFocus.gui.wolter import WolterParams, trace, encircled_energy
    result = trace(WolterParams(offaxis=3., num_rays=4000))
    x, y = result.spot_arcsec
    rad, _ = encircled_energy(result)
    assert np.isclose(np.hypot(x, y).max(), rad[-1], rtol=1e-9)


def test_config_text_round_trip():
    """params -> text -> params preserves all 15 fields and reports nothing."""
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams, PARAM_FIELDS
    p = WolterParams(r0=300., z0=12000., offaxis=2.5, sec_dy=0.3,
                     num_rays=12345, seed=42)
    loaded = config.load_config_text(config.config_text(p))
    for name in PARAM_FIELDS:
        assert getattr(p, name) == getattr(loaded.params, name), name
    assert loaded.problems == [], loaded.problems
    assert loaded.version == config.VERSION
    assert isinstance(loaded.params.num_rays, int)
    assert isinstance(loaded.params.seed, int)


def test_config_file_round_trip():
    """The same through an actual file."""
    import os
    import tempfile
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    path = os.path.join(tempfile.mkdtemp(), 'cfg.json')
    config.save_config(WolterParams(r0=333.), path)
    loaded = config.load_config(path)
    assert loaded.params.r0 == 333.
    assert loaded.path == path


def test_config_envelope_shape():
    """The written document says what it is, and annotates its own units."""
    import json
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams, PARAM_FIELDS
    text = config.config_text(WolterParams())
    assert text.endswith('\n'), 'text files should end with a newline'
    doc = json.loads(text)
    assert doc['format'] == config.FORMAT
    assert doc['version'] == config.VERSION
    assert set(doc['units']) == set(doc['parameters']) == set(PARAM_FIELDS)
    assert 'ui' not in doc, 'an empty ui block should be omitted entirely'


def test_config_refuses_to_write_non_finite():
    """A NaN is refused before any file is touched."""
    import os
    import tempfile
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    path = os.path.join(tempfile.mkdtemp(), 'never.json')
    try:
        config.save_config(WolterParams(sec_dy=float('nan')), path)
    except config.ConfigError:
        pass
    else:
        raise AssertionError('expected ConfigError for a NaN parameter')
    assert not os.path.exists(path), 'a refused save must create no file'


def test_config_failed_save_leaves_the_old_file_intact():
    """
    The point of writing to a temp file and renaming.

    A save that fails must leave the previous configuration readable, and
    must not litter the directory with partial files.
    """
    import glob
    import os
    import tempfile
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, 'cfg.json')
    config.save_config(WolterParams(r0=333.), path)
    try:
        config.save_config(WolterParams(r0=444., sec_dy=float('nan')), path)
    except config.ConfigError:
        pass
    assert config.load_config(path).params.r0 == 333., 'old file was damaged'
    assert not glob.glob(os.path.join(folder, '*.tmp')), 'temp file left behind'


def test_config_rejects_a_foreign_file():
    """Anything that is not one of our configurations is an error, not notes."""
    import json
    from PyXFocus.gui import config
    cases = ['not json at all', '[]', '{}',
             json.dumps({'format': 'chandra-model', 'version': 1})]
    for text in cases:
        try:
            config.load_config_text(text)
        except config.ConfigError:
            continue
        raise AssertionError('expected ConfigError for %r' % text[:40])


def test_config_refuses_a_future_version():
    """
    A newer format is refused by name.

    The parameters block is deliberately valid: refusal is about the
    version, not the contents.
    """
    import json
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    text = json.dumps({'format': config.FORMAT,
                       'version': config.VERSION + 1,
                       'parameters': WolterParams().to_dict()})
    try:
        config.load_config_text(text)
    except config.ConfigError as err:
        assert str(config.VERSION + 1) in str(err)
        assert 'newer' in str(err)
    else:
        raise AssertionError('expected ConfigError for a future version')


def test_config_every_past_version_has_a_migration():
    """
    The migration table may not develop gaps.

    Vacuous while VERSION is 1. The moment someone bumps it this fails
    until they write the hop -- even an identity function. The runtime
    stays forgiving about missing migrations; the suite does not.
    """
    from PyXFocus.gui import config
    expected = list(range(config.OLDEST, config.VERSION))
    assert sorted(config._MIGRATIONS) == expected, (
        'migration table %s does not cover %s'
        % (sorted(config._MIGRATIONS), expected))


def test_config_missing_parameters_is_an_error():
    """
    A file with no usable parameters must not load as "all defaults".

    from_dict alone would happily return a full set of defaults with one
    note, which is exactly the silent misread this module exists to stop.
    """
    import json
    from PyXFocus.gui import config
    for payload in ({'format': config.FORMAT, 'version': 1},
                    {'format': config.FORMAT, 'version': 1, 'parameters': []}):
        try:
            config.load_config_text(json.dumps(payload))
        except config.ConfigError:
            continue
        raise AssertionError('expected ConfigError for %r' % (payload,))


def test_config_missing_field_defaults_with_a_note():
    """One absent field costs that field, not the configuration."""
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    loaded = config.load_config_text(_config_doc(r0=300.))
    assert loaded.params.r0 == 300.
    assert loaded.params.psi == WolterParams().psi
    assert any('psi' in note for note in loaded.problems)


def test_config_unknown_keys_are_noted_never_set():
    """A typo must not become a silent attribute."""
    import json
    from PyXFocus.gui import config
    text = json.dumps({'format': config.FORMAT, 'version': 1,
                       'coating': 'Ir',
                       'parameters': {'r0': 300., 'sec_dyy': 5.}})
    loaded = config.load_config_text(text)
    assert not hasattr(loaded.params, 'sec_dyy')
    joined = ' '.join(loaded.problems)
    assert 'sec_dyy' in joined and 'coating' in joined


def test_config_non_finite_field_falls_back():
    """
    Bare NaN and Infinity tokens are accepted by json but not by us.

    QDoubleSpinBox.setValue(nan) silently yields the field maximum, so an
    unguarded NaN would read back as a deliberate extreme misalignment.
    """
    from PyXFocus.gui import config
    text = ('{"format": "%s", "version": 1, "parameters": '
            '{"sec_ry": NaN, "sec_rx": Infinity}}' % config.FORMAT)
    loaded = config.load_config_text(text)
    assert loaded.params.sec_ry == 0.
    assert loaded.params.sec_rx == 0.


def test_config_reports_out_of_range_but_loads_it_verbatim():
    """
    Range violations are reported here and clamped in the panel.

    Loading verbatim is what keeps this module usable from a script that
    has no widget to clamp against.
    """
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import MAX_TRANSLATION_MM
    over = MAX_TRANSLATION_MM * 2
    loaded = config.load_config_text(_config_doc(sec_dy=over))
    assert loaded.params.sec_dy == over, 'value must not be clamped here'
    assert any('sec_dy' in note for note in loaded.problems)


def test_config_range_check_skips_seed():
    """seed has no range; check_ranges must not trip over lo=None."""
    from PyXFocus.gui import config
    loaded = config.load_config_text(_config_doc(seed=10 ** 9))
    assert loaded.params.seed == 10 ** 9
    assert not any('seed' in note for note in loaded.problems)


def test_config_seed_none_round_trips():
    """
    An unseeded configuration stays unseeded.

    seed=None means "do not seed" and trace() honours it, which is a
    different run from seeding with zero.
    """
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    loaded = config.load_config_text(config.config_text(WolterParams(seed=None)))
    assert loaded.params.seed is None
    assert loaded.problems == [], loaded.problems


def test_config_ui_is_optional_and_round_trips():
    """Interface state survives, and its absence is not a complaint."""
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    # A complete parameters block, so the only thing that could raise a
    # note here is the absent ui block -- which must not.
    loaded = config.load_config_text(_config_doc(**WolterParams().to_dict()))
    assert loaded.ui == {}, loaded.ui
    assert loaded.problems == [], loaded.problems

    ui = {'tab': 3, 'auto_trace': True,
          'sweep': {'parameter': 'sec_dy', 'start': 0., 'stop': 1., 'steps': 30}}
    back = config.load_config_text(config.config_text(WolterParams(), ui))
    assert back.ui['tab'] == 3
    assert back.ui['auto_trace'] is True
    assert back.ui['sweep']['parameter'] == 'sec_dy'
    assert back.ui['sweep']['steps'] == 30


def test_config_ui_never_reaches_parameters():
    """The two blocks are sealed off from each other in both directions."""
    import json
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    text = json.dumps({'format': config.FORMAT, 'version': 1,
                       'parameters': {'tab': 3},
                       'ui': {'r0': 1.0}})
    loaded = config.load_config_text(text)
    assert not hasattr(loaded.params, 'tab')
    assert loaded.params.r0 == WolterParams().r0
    assert 'r0' not in loaded.ui


def test_config_ui_rejects_bad_types():
    """
    ui degrades block by block, and only real booleans are booleans.

    The string 'false' is truthy -- the same trap the QSettings reads
    guard against with type=.
    """
    import json
    from PyXFocus.gui import config
    text = json.dumps({
        'format': config.FORMAT, 'version': 1, 'parameters': {},
        'ui': {'auto_trace': 'false', 'tab': -1,
               'sweep': {'parameter': 'sec_dy', 'start': 0.}}})
    loaded = config.load_config_text(text)
    assert 'auto_trace' not in loaded.ui
    assert 'tab' not in loaded.ui
    assert 'sweep' not in loaded.ui, 'a half-specified range is not a range'


def test_config_units_are_written_but_not_read():
    """
    The tripwire for anyone who wires the units map up.

    Rewriting every unit to nonsense must change nothing and raise no note.
    Reading units would mean this module converts them, which it does not.
    """
    import json
    from PyXFocus.gui import config
    from PyXFocus.gui.wolter import WolterParams
    doc = json.loads(config.config_text(WolterParams(r0=250., sec_rx=1.5)))
    doc['units'] = dict((key, 'furlong') for key in doc['units'])
    loaded = config.load_config_text(json.dumps(doc))
    assert loaded.params.r0 == 250. and loaded.params.sec_rx == 1.5
    assert loaded.problems == [], loaded.problems


def test_config_duplicate_key_is_noted():
    """json.loads keeps the last silently; a hand-edited file deserves better."""
    from PyXFocus.gui import config
    text = ('{"format": "%s", "version": 1, '
            '"parameters": {"r0": 1.0, "r0": 250.0}}' % config.FORMAT)
    loaded = config.load_config_text(text)
    assert loaded.params.r0 == 250.
    assert any('more than once' in note for note in loaded.problems)


def test_config_unreadable_path_is_an_error():
    """A missing file and a directory both fail with the path in the message."""
    import tempfile
    from PyXFocus.gui import config
    for path in ('/no/such/file/anywhere.json', tempfile.mkdtemp()):
        try:
            config.load_config(path)
        except config.ConfigError as err:
            assert path in str(err)
        else:
            raise AssertionError('expected ConfigError for %r' % path)


def test_settings_is_the_only_qsettings_consumer():
    """
    QSettings stays behind the AppSettings facade.

    Pure string work -- no import, no Qt -- so it belongs in the install
    check. Without it the facade gets bypassed the first time someone wants
    to remember one more thing, and key strings start appearing in two
    files with nothing keeping them in step.
    """
    import os
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gui')
    offenders = []
    # os.walk, not glob('gui/*.py'): a non-recursive glob would let an
    # entire subpackage (gui/tabs/) sit outside the only check keeping
    # QSettings behind the facade.
    for root, _, names in os.walk(folder):
        for name in sorted(names):
            if not name.endswith('.py') or name == 'settings.py':
                continue
            path = os.path.join(root, name)
            # 'QSettings(' -- a construction, not a mention. Other modules
            # legitimately talk *about* QSettings in comments; what must not
            # happen is another module reaching for one.
            if 'QSettings(' in open(path).read():
                offenders.append(os.path.relpath(path, folder))
    assert not offenders, 'QSettings constructed outside settings.py: %s' % offenders


def test_qt_free_modules_stay_qt_free():
    """
    The scriptable modules must stay importable without PyQt5.

    The README promises PyQt5 is needed "only if you want the GUI", and
    this suite is the post-build install check.

    A subprocess import rather than an AST scan, because this catches
    *transitive* Qt imports too -- a pure module that grows an innocent
    import of something that itself pulls in Qt would pass a source scan
    and fail here, which is the right way round.
    """
    import subprocess
    import sys
    for module in ('PyXFocus.gui.wolter', 'PyXFocus.gui.config',
                   'PyXFocus.gui.optics', 'PyXFocus.gui.docs_index'):
        subprocess.check_call([
            sys.executable, '-c',
            'import sys, %s; assert "PyQt5" not in sys.modules, '
            '"%s pulled in Qt"' % (module, module)])


#: What `trace` produced before the flat source->primary->secondary->focus
#: pipeline was reorganised into gui/optics.py elements.
#:
#: Captured from the pre-refactor code and asserted with ``==``, not
#: ``isclose``: the refactor was supposed to move code, not numbers, and any
#: drift at all is a defect rather than a tolerance question.  The cases span
#: the paths a refactor could plausibly disturb -- the aperture test, the
#: misaligned secondary frame, the solver's non-convergence path, and a
#: non-unity prescription.
#:
#: ``xsum`` is the sum of surviving x. Half-power diameter is a spread about
#: a centroid, so it survives a reordering or a swap of which rays lived;
#: the raw sum does not, which is what makes it worth carrying.
_PARITY = (
    ('on axis', dict(num_rays=4000, seed=1),
     dict(hpd_arcsec=0.0006415432391093105,
          rms_arcsec=0.00040951888183856185,
          focus_z=-9.45246392802801e-06,
          num_surviving=4000, num_nonconverged=0, num_nonfinite=0,
          xsum=-0.0009697033638054603)),
    ('off axis with azimuth',
     dict(azimuth=35.0, num_rays=4000, offaxis=3.0, seed=2),
     dict(hpd_arcsec=0.08371271685125067,
          rms_arcsec=0.058741264780416386,
          focus_z=0.21336970392803778,
          num_surviving=1809, num_nonconverged=0, num_nonfinite=0,
          xsum=10865.16245512421)),
    ('heavy misalignment',
     dict(num_rays=4000, offaxis=1.0, sec_dy=0.2, sec_rx=0.7, sec_rz=0.3,
          seed=3),
     dict(hpd_arcsec=12.002628664737362,
          rms_arcsec=7.760205133426471,
          focus_z=-0.9482342311421235,
          num_surviving=427, num_nonconverged=0, num_nonfinite=0,
          xsum=1096.1715462728944)),
    ('non-convergence', dict(num_rays=2000, r0=5.0, seed=4),
     dict(hpd_arcsec=1.2045795918739144e-05,
          rms_arcsec=8.261874496175472e-06,
          focus_z=1.9832683392451145e-05,
          num_surviving=145, num_nonconverged=1855, num_nonfinite=0,
          xsum=4.764346686933485e-06)),
    ('non-unity psi',
     dict(num_rays=4000, primary_length=180.0, psi=1.6,
          secondary_length=60.0, seed=5),
     dict(hpd_arcsec=0.0006315763057937328,
          rms_arcsec=0.0004067524193333303,
          focus_z=-4.472800355870277e-06,
          num_surviving=806, num_nonconverged=0, num_nonfinite=0,
          xsum=-0.00017864554690899537)),
)


def test_trace_is_byte_identical_to_the_flat_pipeline():
    """
    Generalising the tracer changed no numbers. Not one bit of one number.

    This is the spine of the element refactor and stays useful well past it:
    every later change -- nested shells, gratings, a placed detector -- must
    leave a plain single-shell Wolter-I exactly where it found it.
    """
    from PyXFocus.gui.wolter import WolterParams, trace
    for name, kwargs, want in _PARITY:
        result = trace(WolterParams(**kwargs))
        got = dict(hpd_arcsec=result.hpd_arcsec,
                   rms_arcsec=result.rms_arcsec,
                   focus_z=result.focus_z,
                   num_surviving=result.num_surviving,
                   num_nonconverged=result.num_nonconverged,
                   num_nonfinite=result.num_nonfinite,
                   xsum=float(np.sum(result.rays[1])))
        for key in sorted(want):
            assert got[key] == want[key], (
                '%s: %s drifted, %r != %r' % (name, key, got[key], want[key]))


def test_ray_ids_stay_aligned_with_rays():
    """
    A beam's ids and its rays are cut together, always.

    `tran.vignette` returns a new list rather than editing in place, so the
    old pipeline re-indexed a parallel id array by hand at four separate
    sites.  Beam.cut is now the only way rays leave a beam; this is what
    says so.
    """
    from PyXFocus.gui import optics
    from PyXFocus.gui.wolter import WolterParams, build_system

    params = WolterParams(offaxis=4., num_rays=3000, seed=7)
    system = build_system(params)
    channel = system.channels[0]
    beam = channel.source.launch()
    assert len(beam.ids) == len(beam)
    for element in channel.elements:
        element.apply(beam, record=False)
        assert len(beam.ids) == len(beam), (
            '%s left %d ids for %d rays'
            % (element.key, len(beam.ids), len(beam)))
        assert np.all(np.diff(beam.ids) > 0), 'ids must stay sorted, unique'
    assert isinstance(beam, optics.Beam)


def test_paths_are_recorded_in_global_coordinates():
    """
    Every drawn stage is in global mm, the focal plane included.

    `surf.focusI` moves the coordinate *frame*, so at the last stage every
    ray sits at local z = 0.  The old pipeline corrected for that afterwards
    with a `zs[-1] += focus_z` line; the terminal element now carries its own
    placement and the correction falls out.  The failure mode is quiet -- the
    last leg would land at z=0, which looks very nearly right -- so assert it.
    """
    from PyXFocus.gui.wolter import WolterParams, trace
    params = WolterParams(offaxis=2., num_rays=3000, seed=11)
    result = trace(params)

    assert np.all(result.path_z[-1] == result.focus_z), (
        'the focal-plane stage is not in global z')
    assert np.all(result.path_z[1] > params.z0), 'primary hits below z0'
    assert np.all(result.path_z[1] < params.z0 + params.primary_length)
    assert np.all(np.diff(result.path_ids) > 0), 'path ids must be sorted'
    assert np.all(np.in1d(result.path_ids, result.ray_ids)), (
        'a drawn path belongs to a ray that did not survive')


def test_placement_round_trips_a_point():
    """
    to_global inverts tran.transform, rotations and all.

    Drawing a misaligned optic depends on this: the element generates its
    geometry in its own frame and the viewer needs it in the telescope's.
    """
    import PyXFocus.transformations as tran
    from PyXFocus.gui import optics

    place = optics.Placement(1.5, -2.25, 3.75, 0.004, -0.007, 0.011)
    x = np.array([0., 10., -30., 220.])
    y = np.array([5., -1., 12., 0.])
    z = np.array([8400., 8500., 8300., 8400.])

    # Put a ray list at these points, move the frame, and read the local
    # coordinates back out -- that is the map to_global has to invert.
    rays = [np.zeros(len(x)), x.copy(), y.copy(), z.copy(),
            np.zeros(len(x)), np.zeros(len(x)), -np.ones(len(x)),
            np.zeros(len(x)), np.zeros(len(x)), np.ones(len(x))]
    tran.transform(rays, *place)
    back = optics.to_global(rays[1], rays[2], rays[3], place)

    for got, want, axis in zip(back, (x, y, z), 'xyz'):
        assert np.allclose(got, want, atol=1e-9), (
            '%s did not round trip: %r' % (axis, got - want))


def test_pure_translation_round_trips_exactly():
    """
    A translation-only placement is exact, not merely close.

    The focal plane is placed this way, and the parity table above compares
    path coordinates with ==. A 4x4 dot product that happens to equal x + dx
    is not good enough; to_global takes a fast path so that it truly is.
    """
    from PyXFocus.gui import optics
    place = optics.Placement(0., 0., -0.9482342311421235, 0., 0., 0.)
    z = np.array([0., 1e-9, -3.25, 8400.123456789])
    _, _, got = optics.to_global(np.zeros(4), np.zeros(4), z, place)
    assert np.all(got == z + place.dz), 'translation is not exact'


def test_the_system_knows_where_its_optics_are():
    """
    mirror_z_range replaces the layout tab's hard-coded z0 +/- length.

    A viewer should be able to zoom on "the optics" without knowing that the
    optics are two Wolter mirrors.
    """
    from PyXFocus.gui.wolter import WolterParams, build_system
    params = WolterParams(primary_length=120., secondary_length=80.)
    zlo, zhi = build_system(params).mirror_z_range()
    assert zlo == params.z0 - params.secondary_length
    assert zhi == params.z0 + params.primary_length


def test_zeroth_order_is_the_system_without_a_grating():
    """
    Order zero must not disperse. It is the control for every test below.

    Not compared bit-for-bit against a gratingless trace: the rays take a
    detour to the grating plane and back, so the focus solve sees different
    rounding. Sub-milliarcsecond on an 8.4 m telescope is zero.
    """
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(num_rays=3000, seed=1, use_grating=1,
                                grating_order=0))
    assert not result.message, result.message
    assert result.hpd_arcsec < 0.01, result.hpd_arcsec
    assert abs(np.mean(result.rays[1])) < 1e-3, 'order zero moved the spot'


def test_a_grating_disperses_by_order_times_wavelength():
    """
    The grating equation, measured: sin(beta) = m * lambda / d.

    Grooves run along +y, so dispersion is along x. The lever arm is the
    grating's height above the focus, which makes the predicted displacement
    a number this test can check outright rather than a trend.
    """
    from PyXFocus.gui.wolter import WolterParams, trace

    def centroid_x(**kwargs):
        result = trace(WolterParams(num_rays=3000, seed=1, use_grating=1,
                                    **kwargs))
        assert not result.message, result.message
        return float(np.mean(result.rays[1])), float(np.mean(result.rays[2]))

    params = WolterParams()
    lever = params.grating_z
    period = params.grating_period

    for order, wave in ((1, 2.), (2, 2.), (1, 4.), (-1, 2.)):
        x, y = centroid_x(grating_order=order, wavelength=wave)
        # sin(beta) = m*lambda/d, and the beam travels `lever` mm to the
        # image. Small angles here: m*lambda/d is 0.01 at most.
        expected = -lever * order * wave / period
        assert abs(x - expected) < 0.05 * abs(expected) + 0.01, (
            'order %+d at %g nm landed at x=%.4f, expected %.4f'
            % (order, wave, x, expected))
        assert abs(y) < 0.01, 'grooves run along y; dispersion must not'

    # Order times wavelength is the only thing that matters.
    assert np.isclose(centroid_x(grating_order=2, wavelength=2.)[0],
                      centroid_x(grating_order=1, wavelength=4.)[0],
                      rtol=1e-3), 'm*lambda is not the dispersion variable'


def _fan_params(**kwargs):
    from PyXFocus.gui.wolter import WolterParams
    base = dict(num_rays=3000, seed=1, use_grating=1, grating_order=1,
                wavelength=2., grating_period=200.)
    base.update(kwargs)
    return WolterParams(**base)


def test_a_span_of_zero_traces_only_the_reference_order():
    """
    No span means one order, and range(-0, 1) is [0].

    Left as a range, a design asking for order 1 alone would quietly also
    fly the undiffracted beam, and the measured dispersion would be the
    average of the two -- exactly half of what the grating equation says.
    """
    from PyXFocus.gui.wolter import build_system, traced_orders
    assert traced_orders(_fan_params()) == (1,)
    assert traced_orders(_fan_params(grating_order=-2)) == (-2,)
    assert build_system(_fan_params()).order_stride == 1
    assert build_system(_fan_params()).order_values() is None

    # The reference order is always present, however narrow the span.
    assert traced_orders(_fan_params(grating_order=5,
                                     grating_order_span=1)) == (-1, 0, 1, 5)


def test_one_trace_produces_every_order():
    """
    A fan, in one pass, dispersed by the grating equation.

    Spacing rather than absolute position, because spot_by_order centres
    everything on the reference order's centroid -- which is what makes the
    dispersion visible instead of stacking every order on zero.
    """
    from PyXFocus.gui.wolter import trace
    params = _fan_params(grating_order_span=2)
    result = trace(params)
    assert not result.message, result.message

    assert set(result.order_values) == set([-2, -1, 0, 1, 2]), \
        result.order_values
    assert result.reference_order == 1

    spots = result.spot_by_order()
    step = params.grating_z * params.wavelength / params.grating_period
    for m, (x, _) in spots.items():
        assert len(x), 'order %+d produced no rays' % m
        # Measured from the reference order, which sits at zero by
        # construction: (m - 1) steps away, and negative because tran.grat
        # subtracts order*wave/d from l.
        expected = -step * (m - result.reference_order)
        assert abs(np.mean(x) - expected) < 0.05 * abs(expected) + 0.01, (
            'order %+d landed at %.4f mm, expected %.4f'
            % (m, np.mean(x), expected))


def test_extra_orders_do_not_move_the_metrics():
    """
    Orders added to be looked at must not change a number.

    This is what Beam.focus_weights is for: an unweighted focus solve puts
    the image plane between the dispersed spots, where none of them is in
    focus, and drags the HPD and the RMS with it.  Measured with the
    weighting removed and seven orders in flight: HPD 0.089 -> 237 arcsec,
    focus 0.05 -> 184 mm.  So this is not a last-bit test.
    """
    from PyXFocus.gui.wolter import trace
    one = trace(_fan_params())
    fan = trace(_fan_params(grating_order_span=3))

    for name in ('hpd_arcsec', 'rms_arcsec', 'hpd_mm', 'rms_mm',
                 'num_surviving'):
        assert getattr(one, name) == getattr(fan, name), (
            '%s moved when orders were added: %r -> %r'
            % (name, getattr(one, name), getattr(fan, name)))

    # focus_z is the one exception, and only in the last bits: np.average
    # with a 0/1 weight array and np.average with weights=None are not
    # obliged to sum in the same order. Measured at 6e-14 mm on an 8.4 m
    # telescope, which is a rounding difference and not a moved focus.
    assert abs(fan.focus_z - one.focus_z) < 1e-9, (
        'focus moved: %.17g -> %.17g' % (one.focus_z, fan.focus_z))

    assert fan.num_surviving_all_orders == 7 * one.num_surviving, (
        'seven orders did not each survive: %d' % fan.num_surviving_all_orders)


def test_a_fan_keeps_ray_ids_sorted_and_unique():
    """
    Ids stay strictly increasing across a fan.

    merge, stack_paths and choose_paths all reach for searchsorted, so this
    is a contract rather than tidiness: ids are handed out in blocks of
    `stride` and a fan may only fill slots inside a block.
    """
    from PyXFocus.gui.wolter import trace
    result = trace(_fan_params(grating_order_span=2))
    ids = result.ray_ids
    assert (np.diff(ids) > 0).all(), 'ray ids are not strictly increasing'
    assert len(np.unique(ids)) == len(ids), 'ray ids collided across a fan'
    assert len(np.unique(ids // 5)) * 5 == len(ids), (
        'a launched ray did not produce one ray per order slot')


def test_drawn_paths_carry_their_order():
    """
    Each drawn path knows which order it is, and families stay together.

    The picture worth drawing is one incident ray splitting into a fan, so
    the paths are chosen by launched ray and every surviving order of a
    chosen ray is drawn -- not five unrelated rays in five colours.
    """
    from PyXFocus.gui.wolter import trace
    result = trace(_fan_params(grating_order_span=2))
    assert result.path_orders is not None
    assert len(result.path_orders) == result.path_x.shape[1]
    assert set(result.path_orders) <= set(result.order_values)

    stride = 5
    families = result.path_ids // stride
    counts = np.bincount(np.unique(families, return_inverse=True)[1])
    assert (counts == stride).all(), (
        'a drawn family is missing orders: %r' % counts)

    # Within a family the paths share a launch point and separate after the
    # grating: that is the whole picture.
    for family in np.unique(families):
        rows = np.where(families == family)[0]
        assert np.ptp(result.path_x[0, rows]) == 0., (
            'one family launched from several places')
        assert np.ptp(result.path_x[-1, rows]) > 0., (
            'a family never dispersed')


def test_a_radial_grating_is_specified_in_nm_per_mm():
    """
    ``period`` is deleted, not inherited carrying the wrong quantity.

    A radial grating is specified by nm of period per mm of radius, not by
    nm.  Leaving an attribute of the right name holding the wrong number is
    how a caller reads it by accident -- as the script exporter did, until
    it grew a real dispatch.
    """
    from PyXFocus.gui import optics
    from PyXFocus.gui.wolter import build_system

    grating = build_system(_fan_params(grating_type=1)).common[0]
    assert isinstance(grating, optics.RadialGrating)
    assert not hasattr(grating, 'period'), (
        'RadialGrating still carries a .period for someone to misread')
    assert grating.dpermm > 0.

    # And it says nm/mm when it complains, rather than the inherited "nm".
    bad = optics.RadialGrating(optics.IDENTITY, -1., 1, 2., half_width=10.)
    try:
        bad.check()
    except ValueError as err:
        assert 'nm/mm' in str(err), str(err)
    else:
        assert False, 'a negative period gradient was accepted'


def test_a_radial_grating_drops_its_evanescent_rays():
    """
    radgrat marks evanescence with NaN, where grat zeroes the cosines.

    Beam.drop_dead tests ``>= 0.1``, which is False for NaN, so both are
    dropped on the same path -- but only by luck of how the comparison
    falls out.  Asserted here because a system carrying NaN direction
    cosines into analyticImagePlane turns every metric into NaN, and one
    ray is enough.
    """
    import numpy as np
    from PyXFocus.gui import optics

    # A hub 1 mm away and a huge wavelength: order*wave/d exceeds 1, which
    # is evanescence by definition.
    beam = optics.Beam([np.zeros(3) for _ in range(10)])
    beam.rays[1] = np.array([0.5, 1.0, 1.5])       # x
    beam.rays[2] = np.array([1.0, 1.0, 1.0])       # y
    beam.rays[6] = np.array([-1., -1., -1.])       # n, travelling in -z
    optics.RadialGrating(optics.IDENTITY, 0.001, 50, 90.,
                         half_width=10.)._diffract(beam)
    assert np.isnan(beam.rays[6]).any(), (
        'premise changed: radgrat no longer produces NaN on evanescence')
    beam.drop_dead()
    assert len(beam) == 0, 'an evanescent ray survived into the metrics'


def test_rays_missing_the_grating_are_reported():
    """A grating too small to catch the beam says so rather than crashing."""
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(num_rays=2000, seed=1, use_grating=1,
                                grating_size=2.))
    assert result.num_surviving == 0
    assert 'grating' in result.message, result.message


def test_an_impossible_grating_is_refused_before_it_traces():
    """A non-positive period is caught by the element's own check."""
    from PyXFocus.gui.wolter import WolterParams, trace
    try:
        trace(WolterParams(num_rays=500, use_grating=1, grating_period=0.))
    except ValueError as err:
        assert 'period' in str(err), str(err)
    else:
        raise AssertionError('expected ValueError for a zero groove period')


def test_a_placed_detector_measures_defocus():
    """
    Defocus becomes a number instead of being absorbed by the autofocus.

    The blur of a converging beam moved dz off focus is 2*dz*r0/z0 across,
    which is what makes this checkable rather than merely monotonic.
    """
    from PyXFocus.gui.wolter import WolterParams, trace

    sharp = trace(WolterParams(num_rays=3000, seed=1, use_detector=1))
    assert sharp.hpd_arcsec < 0.01, (
        'a detector at nominal focus should be sharp: %r' % sharp.hpd_arcsec)

    params = WolterParams(num_rays=3000, seed=1, use_detector=1, det_z=10.)
    blurred = trace(params)
    assert blurred.focus_z == 10., blurred.focus_z
    expected = (2. * params.det_z * params.r0 / params.z0
                / (params.z0 - params.det_z) * 180. / np.pi * 3600.)
    assert abs(blurred.hpd_arcsec - expected) < 0.15 * expected, (
        'defocus blur is %.3f arcsec, expected about %.3f'
        % (blurred.hpd_arcsec, expected))


def test_the_arcsecond_scale_follows_the_detector():
    """
    Angles are measured against node-to-image, not node-to-origin.

    The two are the same only while the image plane sits at the origin, and
    a placed detector is precisely the case where they part company.
    """
    from PyXFocus.gui.wolter import WolterParams, build_system

    params = WolterParams(use_detector=1, det_z=25.)
    assert build_system(params).focal_length == params.z0 - 25.
    assert build_system(WolterParams()).focal_length == WolterParams().z0


def test_defaults_fit_no_grating_and_no_detector():
    """
    The optional parts are off by default, so nothing above changed anyone.

    The parity table would catch a change in the numbers; this catches the
    subtler version where a default flips and the numbers happen to match.
    """
    from PyXFocus.gui.wolter import WolterParams, build_system
    from PyXFocus.gui import optics
    system = build_system(WolterParams())
    assert system.common == [], 'a grating is fitted by default'
    assert isinstance(system.terminator, optics.AutoFocus), (
        'the default terminator is no longer the autofocus')


def test_nested_shells_add_collecting_area():
    """
    More shells collect more, without blurring the image or costing time.

    Throughput and area must grow; on-axis resolution must not degrade,
    because every shell shares one focus. The ray budget is fixed across the
    nest, so a design with twenty shells costs what one shell costs.
    """
    from PyXFocus.gui.wolter import WolterParams, trace

    areas, hpds = [], []
    for shells in (1, 2, 3, 5):
        result = trace(WolterParams(num_shells=shells, num_rays=6000, seed=5))
        assert result.num_launched == 6000, (
            '%d shells launched %d rays, not the budgeted 6000'
            % (shells, result.num_launched))
        assert not result.message, result.message
        areas.append(result.collecting_area)
        hpds.append(result.hpd_arcsec)

    assert areas == sorted(areas) and areas[0] < areas[-1], (
        'collecting area did not grow with shell count: %r' % areas)
    assert max(hpds) < 0.01, (
        'nesting blurred the on-axis focus: %r arcsec' % hpds)


def test_shells_are_close_packed():
    """Each shell starts exactly one wall thickness outside the last."""
    from PyXFocus.gui.wolter import WolterParams, shell_radii, shell_radii_all

    params = WolterParams(num_shells=6, shell_gap=2.5)
    radii = shell_radii_all(params)
    assert len(radii) == 6
    for k in range(5):
        _, outer = shell_radii(params, radii[k])
        inner, _ = shell_radii(params, radii[k + 1])
        assert abs((inner - outer) - params.shell_gap) < 1e-9, (
            'shell %d to %d gap is %r, not %r'
            % (k, k + 1, inner - outer, params.shell_gap))


def test_ray_ids_are_unique_across_shells():
    """
    Channels hold disjoint, increasing id blocks.

    Everything that reconstructs a ray path uses searchsorted, so ids that
    repeat or run backwards across shells would silently join one shell's
    launch point to another shell's mirror hit.
    """
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(num_shells=4, num_rays=4000, seed=2))
    assert len(set(result.ray_ids.tolist())) == len(result.ray_ids), (
        'ray ids repeat across shells')
    assert np.all(np.diff(result.ray_ids) > 0), 'ray ids are not sorted'
    assert np.all(np.diff(result.path_ids) > 0), 'path ids are not sorted'


def test_adding_a_shell_leaves_the_inner_shells_alone():
    """
    Seeding per shell, so a comparison between designs means something.

    With one global seed, adding an outer shell reshuffles every inner
    shell's ray pattern, and the resulting change in the metrics is partly
    sampling noise rather than optics.
    """
    from PyXFocus.gui.wolter import WolterParams, build_system

    one = build_system(WolterParams(num_shells=1, num_rays=2000, seed=9))
    two = build_system(WolterParams(num_shells=2, num_rays=2000, seed=9))
    assert two.channels[0].source.seed == one.channels[0].source.seed, (
        'the innermost shell changed seed when a shell was added')


def test_one_shell_is_unchanged_by_the_nesting_code():
    """The default design is bit-for-bit what it was before nesting."""
    from PyXFocus.gui.wolter import WolterParams, trace
    for name, kwargs, want in _PARITY:
        result = trace(WolterParams(num_shells=1, **kwargs))
        assert result.hpd_arcsec == want['hpd_arcsec'], name
        assert result.num_surviving == want['num_surviving'], name


def test_a_degenerate_shell_is_warned_about_not_raised():
    """
    One impossible shell must not take the rest of the design with it.

    A ray budget smaller than the shell count leaves the outermost shells
    with nothing to trace; that is worth saying out loud, and worth not
    crashing over.
    """
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(num_shells=8, num_rays=4, seed=1))
    assert result.warnings, 'starved shells produced no warning'
    assert any('rays' in w for w in result.warnings), result.warnings


def test_config_version_2_reads_a_version_1_file():
    """
    A configuration written before nesting opens as a single shell.

    One note, not two "missing; using default" complaints -- a file that
    predates a field is not a damaged file.
    """
    import json
    from PyXFocus.gui import config, wolter

    params = wolter.WolterParams()
    fields = dict(params.to_dict())
    del fields['num_shells']
    del fields['shell_gap']
    text = json.dumps({'format': config.FORMAT, 'version': 1,
                       'parameters': fields})

    loaded = config.load_config_text(text)
    assert loaded.version == 1, loaded.version
    assert loaded.params.num_shells == 1, loaded.params.num_shells
    assert loaded.params.shell_gap == 1., loaded.params.shell_gap
    assert any('predates nested shells' in p for p in loaded.problems), (
        loaded.problems)
    assert not any('num_shells missing' in p for p in loaded.problems), (
        loaded.problems)


def test_mirror_profiles_cover_the_whole_nest():
    """
    The profile list is per shell, and a single shell is the old picture.

    The 2D layout draws this list, so a one-element list whose entry equals
    mirror_profile is what keeps an unnested design looking identical.
    """
    from PyXFocus.gui.wolter import WolterParams, mirror_profile, mirror_profiles

    single = WolterParams()
    profiles = mirror_profiles(single)
    assert len(profiles) == 1
    (zp, rp), (zs, rs) = profiles[0]
    (wzp, wrp), (wzs, wrs) = mirror_profile(single)
    for got, want in ((zp, wzp), (rp, wrp), (zs, wzs), (rs, wrs)):
        assert np.array_equal(got, want), 'a single shell changed shape'

    nest = mirror_profiles(WolterParams(num_shells=5, shell_gap=3.))
    assert len(nest) == 5
    radii = [float(np.min(rp)) for (_, rp), _ in nest]
    assert radii == sorted(radii) and radii[0] < radii[-1], (
        'shells are not nested outward: %r' % radii)


def test_mirror_profile_is_unchanged():
    """
    Routing the profile through the elements moved no points.

    Compared against the literal expressions the function used to contain,
    with array_equal rather than allclose: this was a refactor.
    """
    import PyXFocus.conicsolve as conic
    from PyXFocus.gui.wolter import WolterParams, mirror_profile

    for kwargs in (dict(), dict(psi=1.6, primary_length=180.,
                                secondary_length=60., r0=95., z0=6000.)):
        p = WolterParams(**kwargs)
        (zp, rp), (zs, rs) = mirror_profile(p)
        want_zp = np.linspace(p.z0, p.z0 + p.primary_length, 200)
        want_zs = np.linspace(p.z0 - p.secondary_length, p.z0, 200)
        assert np.array_equal(zp, want_zp), 'primary z moved'
        assert np.array_equal(zs, want_zs), 'secondary z moved'
        assert np.array_equal(
            rp, conic.primrad(want_zp, p.r0, p.z0, psi=p.psi)), 'primary r moved'
        assert np.array_equal(
            rs, conic.secrad(want_zs, p.r0, p.z0, psi=p.psi)), 'secondary r moved'


def test_patch_meridian_matches_profile():
    """
    The 3D mesh and the 2D profile are the same curve.

    A patch is the profile swept about z, so the phi=0 column of the mesh
    must reproduce the profile exactly. This is the assertion that stops a
    2D and a 3D view growing two different ideas of where a mirror is.
    """
    from PyXFocus.gui.wolter import WolterParams, WolterPrimary, WolterSecondary

    params = WolterParams(psi=1.3)
    for element in (WolterPrimary(params), WolterSecondary(params)):
        (z, r), = element.profile(37)
        patch, = element.patches(n_azimuth=16, num=37)
        assert patch.x.shape == (37, 16), patch.x.shape
        # phi starts at 0, so column 0 is the +x meridian.
        assert np.allclose(np.hypot(patch.x[:, 0], patch.y[:, 0]), r), (
            '%s mesh radius disagrees with its profile' % element.key)
        assert np.allclose(patch.z[:, 0], z), (
            '%s mesh z disagrees with its profile' % element.key)
        assert np.allclose(np.hypot(patch.x, patch.y),
                           np.repeat(r[:, None], 16, axis=1)), (
            '%s is not a surface of revolution' % element.key)


def test_patches_honour_misalignment():
    """
    A tilted secondary draws tilted.

    ``mirror_profile`` never could show this -- it returned the nominal
    prescription and ignored the misalignment entirely -- which is precisely
    the gap a 3D view exists to close. At zero misalignment the geometry must
    still be exactly nominal, so the mechanism cannot perturb an aligned
    design.
    """
    from PyXFocus.gui.wolter import WolterParams, WolterSecondary

    nominal, = WolterSecondary(WolterParams()).patches(n_azimuth=24, num=12)

    same, = WolterSecondary(WolterParams(sec_rx=0., sec_dy=0.)).patches(
        n_azimuth=24, num=12)
    assert np.array_equal(same.z, nominal.z), 'an aligned optic moved'
    assert np.array_equal(same.x, nominal.x), 'an aligned optic moved'

    tilted, = WolterSecondary(WolterParams(sec_rx=5.)).patches(
        n_azimuth=24, num=12)
    assert not np.allclose(tilted.z, nominal.z), (
        'a 5 arcmin tilt left the drawn geometry unchanged')
    # A tilt about x swings the mirror in y and z, and the y extremes move
    # furthest in z. The spread across azimuth is the visible signature.
    spread = (tilted.z.max(axis=1) - tilted.z.min(axis=1)).max()
    assert spread > 1e-3, 'tilt produced no azimuthal z spread: %r' % spread

    shifted, = WolterSecondary(WolterParams(sec_dy=2.)).patches(
        n_azimuth=24, num=12)
    assert np.allclose(shifted.y - nominal.y, 2.), (
        'a 2 mm decentre did not move the drawn geometry by 2 mm')


def test_patch_vertex_budget():
    """
    A system's mesh stays small enough to rotate interactively.

    mplot3d re-projects every vertex on every mouse move, so this is a
    performance contract, not a style note.
    """
    from PyXFocus.gui.wolter import WolterParams, build_system
    patches = build_system(WolterParams()).patches(n_azimuth=32, num=8)
    total = sum(p.x.size for p in patches)
    assert total <= 20000, 'mesh is %d vertices' % total
    assert total > 0, 'a Wolter system drew nothing'


def test_styles_carry_no_matplotlib_objects():
    """
    Geometry is generated Qt-free and matplotlib-free.

    Styling crosses the boundary as plain strings and floats so that meshes
    can be built, and tested, with no GUI anywhere near them.
    """
    from PyXFocus.gui import optics
    for kind, style in optics.STYLES.items():
        for key, value in style.items():
            assert isinstance(value, (str, float, int)), (
                '%s.%s is a %s' % (kind, key, type(value).__name__))


def test_a_degenerate_aperture_is_reported_not_raised():
    """An impossible shell explains itself instead of throwing."""
    from PyXFocus.gui.wolter import WolterParams, trace
    result = trace(WolterParams(psi=0.1, r0=1., z0=100000.))
    if result.num_surviving == 0:
        assert result.message, 'an empty trace must say why'


def test_reproducible():
    """The same seed gives the same answer twice."""
    from PyXFocus.gui.wolter import WolterParams, trace
    a = trace(WolterParams(offaxis=3., seed=42, num_rays=5000))
    b = trace(WolterParams(offaxis=3., seed=42, num_rays=5000))
    assert a.hpd_arcsec == b.hpd_arcsec, 'trace is not reproducible'


def test_docs_html_is_current():
    """
    The generated documentation matches the Markdown it came from.

    This is the tripwire for the one failure mode a committed build artifact
    has: editing docs/*.md and shipping without re-running
    ``tools/build_docs.py``, so the app displays the previous wording. It
    imports no Markdown parser -- check() re-hashes the sources -- so it
    still runs in this suite's dependency-free install check.
    """
    import os
    import sys
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import build_docs
    assert build_docs.check() == 0, (
        'the bundled docs are stale -- run `python tools/build_docs.py`')


def test_docs_pages_and_sources_agree():
    """Every page in PAGES has a Markdown file, and vice versa."""
    import os
    from PyXFocus.gui import docs_index

    listed = set(docs_index.keys())
    on_disk = set(name[:-3] for name in os.listdir(docs_index.SOURCE_DIR)
                  if name.endswith('.md'))
    assert listed == on_disk, (
        'PAGES and docs/ disagree: only in PAGES %s, only on disk %s'
        % (sorted(listed - on_disk), sorted(on_disk - listed)))


def test_docs_links_resolve():
    """
    Every internal documentation link points at a page that exists.

    Catches the two ways a link goes bad here: a wiki-style extension-less
    target surviving the move out of the wiki (those render as text in the
    viewer), and a link to a page that has since been renamed.
    """
    import re
    from PyXFocus.gui import docs_index

    known = set(docs_index.keys())
    bad = []
    for key in docs_index.keys():
        with open(docs_index.source_path(key)) as fh:
            text = fh.read()
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if target.startswith(('http://', 'https://', '#')):
                continue
            name = target.split('#')[0]
            if not name.endswith('.md'):
                bad.append('%s.md -> %r (no .md suffix)' % (key, target))
            elif name[:-3] not in known:
                bad.append('%s.md -> %r (no such page)' % (key, target))
    assert not bad, 'broken documentation links:\n  ' + '\n  '.join(bad)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            check(name, fn)

    failures = [(n, tb) for n, ok, tb in RESULTS if not ok]
    print('\n%d passed, %d failed' % (len(RESULTS) - len(failures), len(failures)))
    for name, tb in failures:
        print('\n--- %s ---\n%s' % (name, tb))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
