"""
Read and write explorer configurations as versioned JSON.

The files are meant to be opened in an editor, hand-edited, committed next to
a paper and emailed to a collaborator, so the format is plain and stable:
no timestamps, no generator string, ordered keys. ``diff`` should answer
"did this configuration change", not "was it saved again".

Qt-free on purpose, for the same reason :mod:`PyXFocus.gui.wolter` is: a
script or a notebook should be able to write a configuration without a GUI.
(It is not dependency-free -- importing ``wolter`` pulls in the compiled
Fortran extensions -- only Qt-free.)

Three layers decide three different questions, and keeping them apart is what
makes this module usable outside the GUI:

* here          -- is this *file* a usable configuration?
* ``WolterParams.from_dict``  -- is this *field* a usable value?
* ``ParameterPanel.set_params`` -- will this *widget* accept it?

So a value outside its ``ParamSpec`` range is **reported here and loaded
verbatim**; the clamping happens in the panel, where the spin box that
enforces the range actually lives. Clamping here would make the module
useless to a caller that has no widget.

Errors versus notes: :class:`ConfigError` means "this is not a
configuration" and nothing is loaded. Everything else is a note appended to
a list, and the configuration still loads. A file that quietly loaded as
something other than what it said is worse than one that refused, so callers
are expected to surface ``problems`` rather than discard it.

The ``units`` block is **written and never read**. It exists for whoever
opens the file wondering whether ``sec_rx`` is arcmin or arcsec. Reading it
back would mean this module converts units, which it does not -- if you are
here to "fix" that oversight, it is a decision, and
``test_config_units_are_written_but_not_read`` is the tripwire that says so.
"""

import collections
import json
import math
import os
import stat
import tempfile

from PyXFocus.gui import wolter


#: Identifies the file as ours. Distinguishes "not our file" from "our file,
#: wrong vintage" -- without it, opening someone's data.json reports fifteen
#: missing parameters instead of one clear sentence.
FORMAT = 'pyxfocus-wolter-config'

#: The version this build writes.
VERSION = 4

#: The oldest version this build can still read.
OLDEST = 1

FILE_SUFFIX = '.json'
FILE_FILTER = 'Wolter-I configuration (*.json)'

UI_TAB = 'tab'
UI_AUTO_TRACE = 'auto_trace'
UI_SWEEP = 'sweep'

_KNOWN_TOP_LEVEL = frozenset(('format', 'version', 'parameters', 'units', 'ui'))
_UI_KEYS = frozenset((UI_TAB, UI_AUTO_TRACE, UI_SWEEP))
_SWEEP_KEYS = frozenset(('parameter', 'start', 'stop', 'steps'))


class ConfigError(Exception):
    """
    The file is not a usable configuration and nothing was loaded.

    Anything recoverable is a note in ``LoadedConfig.problems`` instead.
    """

    def __init__(self, message, path=None):
        Exception.__init__(self, message)
        self.path = path


class LoadedConfig(object):
    """
    What came out of a configuration file.

    ``problems`` is a list of human-readable notes -- unknown keys, fields
    filled from defaults, values outside the panel's range. It is *not* an
    error list: ``params`` is usable regardless. Callers should show it.
    """

    __slots__ = ('params', 'ui', 'problems', 'version', 'path')

    def __init__(self, params, ui, problems, version, path=None):
        self.params = params
        self.ui = ui
        self.problems = problems
        #: The version recorded in the file, before any migration.
        self.version = version
        self.path = path

    def summary(self, limit=6):
        """The notes as one block of text, truncated for a dialog."""
        if not self.problems:
            return ''
        shown = self.problems[:limit]
        extra = len(self.problems) - len(shown)
        if extra > 0:
            shown = shown + ['... and %d more' % extra]
        return '\n'.join(shown)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def config_payload(params, ui=None, problems=None):
    """Build the configuration as a JSON-ready mapping, in stable key order."""
    if problems is None:
        problems = []

    payload = collections.OrderedDict()
    payload['format'] = FORMAT
    payload['version'] = VERSION
    payload['parameters'] = params.to_dict()
    #: Written for a human reader; never read back. See the module docstring.
    payload['units'] = collections.OrderedDict(
        (spec.name, spec.unit) for spec in wolter.PARAM_SPECS)

    cleaned = _clean_ui(ui, problems)
    if cleaned:
        payload['ui'] = cleaned
    return payload


def config_text(params, ui=None, problems=None):
    """
    The exact text of a configuration file.

    Kept separate from :func:`save_config` so the whole document exists
    before any file is touched -- a save cannot fail halfway through
    serialising, and every failure mode is testable without a filesystem.
    """
    payload = config_payload(params, ui, problems)
    try:
        # allow_nan=False: the default emits a bare NaN/Infinity token, which
        # is not valid JSON. Python reads it back happily, so the file looks
        # healthy right up until it is opened by anything else. (One of four
        # NaN guards -- the others are in from_dict, check_ranges and
        # ParameterPanel.set_params; each covers a different entry point.)
        text = json.dumps(payload, indent=2, allow_nan=False)
    except ValueError as err:
        raise ConfigError('cannot save: a parameter is not a finite '
                          'number (%s)' % err)
    return text + '\n'


def save_config(params, path, ui=None, problems=None):
    """
    Write a configuration, leaving the previous file intact if anything fails.

    The document is serialised in full, written to a temporary file beside
    the target, then renamed over it. A failure at any point leaves either
    the old file or no file -- never a truncated one.
    """
    text = config_text(params, ui, problems)      # may raise; no I/O yet

    path = os.path.abspath(path)
    directory = os.path.dirname(path) or '.'
    temp = None
    try:
        # The temporary file must live in the target's directory: os.replace
        # is a rename, and across filesystems it fails outright rather than
        # falling back to a copy. /tmp is not an option.
        handle, temp = tempfile.mkstemp(
            dir=directory, prefix=os.path.basename(path) + '.', suffix='.tmp')
        with os.fdopen(handle, 'w') as stream:
            stream.write(text)
            stream.flush()
            # Rename ordering and data ordering are independent; without this
            # a crash can leave a correctly named, empty file.
            os.fsync(stream.fileno())

        if os.path.exists(path):
            # mkstemp creates 0600. Without copying the old mode across,
            # saving over a group-readable config silently makes it private.
            try:
                os.chmod(temp, stat.S_IMODE(os.stat(path).st_mode))
            except OSError:
                pass

        # os.replace, never os.rename: rename refuses an existing
        # destination on Windows.
        os.replace(temp, path)
        temp = None
    except OSError as err:
        raise ConfigError('could not save %s: %s' % (path, err), path)
    finally:
        if temp is not None:
            try:
                os.unlink(temp)
            except OSError:
                pass
    return path


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def _migrate_1_to_2(payload, problems):
    """
    Version 1 predates nested shells, so a version 1 design is one shell.

    Without this every configuration written before nesting existed would
    open with two "missing; using default" notes, which is technically
    accurate and reads like the file is damaged.
    """
    # Migrate what is there; never conjure a parameters block. A file with
    # none is an error the loader must still be able to raise, and a
    # migration that helpfully invents one would turn that into a silent
    # load of all-defaults -- the exact misread this module exists to stop.
    params = payload.get('parameters')
    if not isinstance(params, dict):
        return payload
    params.setdefault('num_shells', 1)
    params.setdefault('shell_gap', 1.)
    problems.append('this configuration predates nested shells; '
                    'reading it as a single shell')
    return payload


def _migrate_2_to_3(payload, problems):
    """
    Version 2 predates grating types and multi-order tracing.

    Every version 2 grating was linear and traced one order, so defaulting
    the four new fields to exactly that reproduces what the file described.
    No note is appended: unlike nesting, nothing about how the design is
    read has changed, so there is nothing the user needs to be told.
    """
    params = payload.get('parameters')
    if not isinstance(params, dict):
        return payload
    params.setdefault('grating_type', 0)          # linear
    params.setdefault('grating_order_span', 0)    # the reference order only
    params.setdefault('grating_dpermm', 15.)
    params.setdefault('grating_hub', 0.)
    return payload


def _migrate_3_to_4(payload, problems):
    """
    Version 3 predates a detector with a size, a shape or a second tilt.

    Every version 3 detector was flat, unbounded and tilted only about x,
    so defaulting the four new fields to exactly that reproduces the design
    the file describes -- det_size in particular must stay 0, since any
    positive value vignettes and would move the numbers.
    """
    params = payload.get('parameters')
    if not isinstance(params, dict):
        return payload
    params.setdefault('det_shape', 0)       # flat
    params.setdefault('det_size', 0.)       # unbounded, as it always was
    params.setdefault('det_tilt_y', 0.)
    params.setdefault('det_radius', 200.)
    return payload


#: Migration table: version N -> a function returning a payload at N+1.
#:
#: One entry per step, so a version 1 file still opens after five format
#: changes without anyone writing a 1->6 case.
#:
#: A version with nothing to do still needs an entry -- an identity function
#: with a comment saying why -- because
#: test_config_every_past_version_has_a_migration refuses to let the table
#: develop gaps. The runtime is forgiving; the test suite is not.
_MIGRATIONS = {1: _migrate_1_to_2, 2: _migrate_2_to_3,
               3: _migrate_3_to_4}


def _read_version(payload, problems):
    """The version the file claims, defaulting to the oldest we can read."""
    raw = payload.get('version')
    if raw is None:
        problems.append('no version recorded; reading it as version %d'
                        % OLDEST)
        return OLDEST
    # bool before int: True *is* an int in Python and would read as version 1.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        problems.append('version %r is not a number; reading it as version %d'
                        % (raw, OLDEST))
        return OLDEST
    if float(raw) != int(raw):
        problems.append('version %r is not a whole number; reading it as %d'
                        % (raw, int(raw)))
    return int(raw)


def _migrate(payload, version, problems):
    """
    Bring an older payload up to the current version, in single hops.

    Asymmetric on purpose. A version from the future is refused by the
    caller, because we cannot know what its fields mean. A version from the
    past is never refused: the worst case is that it is read as it stands
    and said so, and a file that will not open is a file whose parameters
    are lost.
    """
    while version < VERSION:
        step = _MIGRATIONS.get(version)
        if step is None:
            problems.append(
                'this configuration is version %d and there is no migration '
                'to version %d; reading it as it stands'
                % (version, version + 1))
            break
        payload = step(payload, problems)
        version += 1
    return payload


def migrate_parameters(data, version, problems):
    """
    Run a bare ``parameters`` block through the file migrations.

    A remembered session is the same parameter block a file carries, so it
    deserves the same treatment.  Wraps it as a payload, migrates, unwraps --
    which keeps the migration functions written once, against the shape they
    already know, rather than in two subtly different flavours.
    """
    payload = _migrate({'parameters': data}, version, problems)
    migrated = payload.get('parameters', data)
    return migrated if isinstance(migrated, dict) else data


def check_ranges(params, problems):
    """
    Note any value outside the range its :class:`ParamSpec` declares.

    Reports; never clamps. The clamping belongs to
    ``ParameterPanel.set_params``, where the spin box lives -- so this stays
    usable from a script with no widget to clamp against.
    """
    for spec in wolter.PARAM_SPECS:
        if spec.lo is None or spec.hi is None:
            continue                # seed: carried through, never bounded
        value = getattr(params, spec.name)
        if value is None:
            continue
        value = float(value)
        unit = (' ' + spec.unit) if spec.unit else ''
        if not math.isfinite(value):
            problems.append('%s is not a finite number' % spec.name)
        elif not (spec.lo <= value <= spec.hi):
            problems.append(
                '%s = %g%s is outside the range the parameter panel accepts '
                '(%g to %g%s); loaded as it stands'
                % (spec.name, value, unit, spec.lo, spec.hi, unit))
    return problems


def _clean_sweep(raw, problems):
    """A sweep is all or nothing: a range missing one end is not a range."""
    if not isinstance(raw, dict):
        problems.append('"ui.sweep" is not an object; ignoring the sweep '
                        'settings')
        return None

    for key in sorted(raw):
        if key not in _SWEEP_KEYS:
            problems.append('ignored unknown sweep key %r' % key)

    name = raw.get('parameter')
    if name not in [spec[0] for spec in wolter.SWEEPABLE]:
        problems.append('ignoring the sweep settings: %r is not a parameter '
                        'a sweep can vary' % (name,))
        return None

    sweep = collections.OrderedDict(parameter=name)
    for key in ('start', 'stop'):
        try:
            value = float(raw[key])
        except (KeyError, TypeError, ValueError):
            problems.append('ignoring the sweep settings: %s is missing or '
                            'not a number' % key)
            return None
        if not math.isfinite(value):
            problems.append('ignoring the sweep settings: %s is not finite'
                            % key)
            return None
        sweep[key] = value

    steps = raw.get('steps')
    if isinstance(steps, bool) or not isinstance(steps, int):
        problems.append('ignoring the sweep settings: steps is missing or '
                        'not a whole number')
        return None
    if steps < 2:
        problems.append('a sweep needs at least two points; steps = %d will '
                        'be raised to 2' % steps)
    sweep['steps'] = steps
    return sweep


def _clean_ui(raw, problems):
    """
    Keep only the interface hints we recognise, with the types we expect.

    Strictly optional and strictly separate: nothing here is ever handed to
    ``WolterParams.from_dict``. Unlike ``parameters``, which degrades field
    by field, ``ui`` degrades block by block -- half a sweep range is not a
    usable sweep, whereas half a parameter set is a usable parameter set.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append('"ui" is not an object; ignoring the interface state')
        return {}

    ui = collections.OrderedDict()
    for key in sorted(raw):
        if key not in _UI_KEYS:
            problems.append('ignored unknown ui key %r' % key)

    if UI_TAB in raw:
        tab = raw[UI_TAB]
        if isinstance(tab, bool) or not isinstance(tab, int) or tab < 0:
            problems.append('ui tab %r is not a tab number; ignoring it'
                            % (tab,))
        else:
            # No upper bound: how many tabs exist is a Qt fact this module
            # does not know. app.py clamps against tabs.count().
            ui[UI_TAB] = tab

    if UI_AUTO_TRACE in raw:
        auto = raw[UI_AUTO_TRACE]
        if not isinstance(auto, bool):
            # JSON has true/false. Anything else was written by something
            # that did not understand the format -- and the string 'false'
            # is truthy, the same trap the QSettings reads guard with type=.
            problems.append('ui auto_trace %r is not true or false; '
                            'ignoring it' % (auto,))
        else:
            ui[UI_AUTO_TRACE] = auto

    if raw.get(UI_SWEEP) is not None:
        sweep = _clean_sweep(raw[UI_SWEEP], problems)
        if sweep:
            ui[UI_SWEEP] = sweep
    return ui


def load_config(path):
    """Read a configuration file. Raises :class:`ConfigError` if it is not one."""
    try:
        with open(path, 'r') as stream:
            text = stream.read()
    except UnicodeDecodeError:
        raise ConfigError('%s is not a text file' % path, path)
    except (OSError, IOError) as err:
        raise ConfigError('cannot read %s: %s'
                          % (path, getattr(err, 'strerror', None) or err), path)
    return load_config_text(text, path)


def load_config_text(text, path=None):
    """
    Parse configuration text -- everything :func:`load_config` does but the file.

    Separate so each failure mode is a string test, with no temporary
    directories and no filesystem in the way of the logic.
    """
    problems = []
    where = path or 'this configuration'

    # json.loads keeps the last of a duplicated key, in total silence. This
    # format is meant to be hand-edited, so a duplicate is a realistic
    # mistake and deserves to be said out loud.
    duplicates = []

    def pairs_hook(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen and key not in duplicates:
                duplicates.append(key)
            seen.add(key)
        return dict(pairs)

    try:
        payload = json.loads(text, object_pairs_hook=pairs_hook)
    except ValueError as err:               # JSONDecodeError subclasses this
        raise ConfigError('%s is not valid JSON: %s' % (where, err), path)

    if not isinstance(payload, dict):
        raise ConfigError('%s: the top level is a %s, not an object'
                          % (where, type(payload).__name__), path)

    found = payload.get('format')
    if found != FORMAT:
        raise ConfigError(
            '%s is not a Wolter-I Explorer configuration (expected format '
            '%r, found %r)' % (where, FORMAT, found), path)

    for key in duplicates:
        problems.append('%r appears more than once; the last one wins' % key)

    file_version = _read_version(payload, problems)
    if file_version > VERSION:
        raise ConfigError(
            '%s was written by a newer version of the Wolter-I Explorer '
            '(configuration version %d); this build understands up to '
            'version %d.' % (where, file_version, VERSION), path)

    payload = _migrate(payload, file_version, problems)

    if 'parameters' not in payload:
        raise ConfigError('%s has no "parameters" block' % where, path)
    raw = payload['parameters']
    if not isinstance(raw, dict):
        # from_dict would return a full set of defaults plus one note, and a
        # file that loads as "all defaults" while looking healthy is exactly
        # the silent misread this module exists to prevent.
        raise ConfigError('%s: "parameters" is a %s, not an object'
                          % (where, type(raw).__name__), path)

    # Only the parameters block is ever handed to from_dict. A tab index is
    # not a parameter and must never reach it.
    params = wolter.WolterParams.from_dict(raw, problems)
    check_ranges(params, problems)

    ui = _clean_ui(payload.get('ui'), problems)

    for key in sorted(payload):
        if key not in _KNOWN_TOP_LEVEL:
            problems.append('ignored unknown top-level key %r' % key)

    # file_version, not the migrated one: "what version is this file" is a
    # question about the file.
    return LoadedConfig(params, ui, problems, file_version, path)
