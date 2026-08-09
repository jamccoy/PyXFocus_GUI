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
    for module in ('PyXFocus.gui.wolter', 'PyXFocus.gui.config'):
        subprocess.check_call([
            sys.executable, '-c',
            'import sys, %s; assert "PyQt5" not in sys.modules, '
            '"%s pulled in Qt"' % (module, module)])


def test_reproducible():
    """The same seed gives the same answer twice."""
    from PyXFocus.gui.wolter import WolterParams, trace
    a = trace(WolterParams(offaxis=3., seed=42, num_rays=5000))
    b = trace(WolterParams(offaxis=3., seed=42, num_rays=5000))
    assert a.hpd_arcsec == b.hpd_arcsec, 'trace is not reproducible'


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
