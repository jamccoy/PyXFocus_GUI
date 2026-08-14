# Architecture and Repository Layout

## Module table

| Module | Purpose |
| --- | --- |
| `sources.py` | Ray sources (point, annulus, converging beam, …) |
| `surfaces.py` | Surfaces to trace to (Wolter, conic, sphere, cylinder, …) |
| `transformations.py` | Coordinate transforms, reflection, refraction, gratings, vignetting |
| `analyses.py` | Centroid, RMS, HPD, wavefront and OPD fitting |
| `conicsolve.py` | Wolter-I prescription maths (radii, focus, sag) |
| `lenses.py` | Singlet and doublet lenses |
| `gui/wolter.py` | One-call Wolter-I trace, plus parameter sweeps |
| `gui/app.py` | PyQt5 Wolter-I Explorer |
| `gui/config.py` | Versioned JSON configuration format |
| `gui/settings.py` | What the app remembers between runs |
| `gui/icon.py` | The app icon, drawn programmatically |
| `build_extensions.py` | Compiles the Fortran extensions |
| `test_smoke.py` | Import and physics checks (no Qt) |
| `test_gui_smoke.py` | Settings persistence and window restore (needs PyQt5) |
| `tools/make_icon.py` | Builds `resources/PyXFocus.icns` |
| `tools/make_launcher.py` | Builds the double-clickable macOS `.app` |

`surfaces.py`, `transformations.py`, `analyses.py`, `conicsolve.py`, and `lenses.py` are the original PyXFocus engine (Ryan Allured et al.) and currently have no module-level docstrings of their own — their behavior is documented by the ray-array convention in **[Scripting and Ray Conventions](Scripting-and-Ray-Conventions.md)** and by their call sites.

## `gui/wolter.py` — the Qt-free core

The GUI is deliberately a thin shell over this module, not the other way around. `wolter.py` wraps the raw PyXFocus call sequence (sources → primary → secondary → focus) into `trace(params)` and `sweep(...)`, taking a `WolterParams` object and handing back rays plus the numbers you actually want (HPD, throughput, collecting area, ...). Because it has no Qt dependency, the exact same trace the GUI runs on every keystroke is usable from a plain script or notebook.

It also owns `MAX_TRANSLATION_MM` / `MAX_ROTATION_ARCMIN` and `check_misalignment()` — the guard against a real defect in the Fortran secondary solver. See **[Known Limitations](Known-Limitations.md)** for why that guard exists and what it protects against.

## `gui/config.py` — versioned JSON, by design

Configuration files are meant to be opened in an editor, hand-edited, committed next to a paper, and emailed to a collaborator — so the format is deliberately plain and stable: no timestamps, no generator string, ordered keys. `diff` should answer "did this configuration change", not "was it saved again."

The module is Qt-free for the same reason `wolter.py` is: a script or notebook should be able to write a configuration without a GUI. (It isn't dependency-free — importing `wolter` pulls in the compiled Fortran extensions — only Qt-free.)

Three layers decide three different questions, kept deliberately separate:

* **`config.py`** — is this *file* a usable configuration?
* **`WolterParams.from_dict`** — is this *field* a usable value?
* **`ParameterPanel.set_params`** — will this *widget* accept it?

A value outside its allowed range is reported by `config.py` and loaded verbatim; clamping happens in the panel, where the spin box enforcing the range actually lives. Clamping in `config.py` would make it useless to a caller with no widget.

Errors versus notes: a `ConfigError` means "this is not a configuration" and nothing loads. Everything else is a note appended to a list, and the configuration still loads — a file that quietly loaded as something other than what it claimed is worse than one that refused outright, so callers are expected to surface `problems` rather than discard them.

The `units` block in a saved file is **written and never read**. It exists purely for a human opening the file who wants to know whether `sec_rx` is arcmin or arcsec — reading it back would mean the module converts units, which it deliberately does not.

## `gui/settings.py` — the one QSettings consumer

Everything Qt persists between runs goes through `AppSettings`, the only place in the app that touches `QSettings`. Two reasons: every key string appears exactly once, so a typo is a bug in one file rather than a value written to `window/tab` and silently read back from `windows/tab`; and the underlying `QSettings` is injectable, so the test suite runs against a temporary INI and can never mutate the developer's real preferences.

**Every read passes an explicit `type=`.** This isn't defensive style — it's the one bug the module exists to prevent. Measured across a real process boundary (write, quit, relaunch), an INI-backed `QSettings` returns the *string* `'false'` for `value('ui/auto_trace')` (which is truthy in Python) and the string `'3'` for `value('window/tab')`. macOS's native plist backend preserves real types, so an untyped read works by accident on macOS and fails only on Linux or Windows.

`session/parameters` holds exactly the mapping `gui/config.py` writes into a configuration file's `parameters` block — there is no second serializer, so a stale session degrades field-by-field with notes exactly as a stale file does.

The dependency direction is one-way: `settings.py` may import `config.py`; `config.py` must never import `settings.py`, because `config.py` promises to work without PyQt5 installed at all.

## `gui/app.py` — the Qt layer

Wires the above into windows and widgets: a `TraceWorker`/`SweepWorker` pair (Qt threads, so a trace doesn't block the UI), the parameter panel, and the four result views described in **[Wolter-I Explorer GUI](Wolter-I-Explorer-GUI.md)**.

## A note on `examples/`

Most scripts under `examples/` predate the current package layout. They import a module named `traces` (an older name for this package) or the removed monolithic `PyTrace` API, and will not run as written. Treat them as reference for how traces were assembled rather than as runnable code; `examples/wolterSchwarzschildTest.py` uses the current `PyXFocus.*` imports and does run.
