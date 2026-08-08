# PyXFocus_GUI

A PyQt5 graphical front end for **PyXFocus**, the general purpose raytracing
package for X-ray telescope design, plus the cross-platform build and test
tooling needed to run it outside Windows.

This is a fork of [kbuffo/PyXFocus](https://github.com/kbuffo/PyXFocus).
The raytracing engine — the Fortran kernels, surfaces, sources and analysis
routines — is the original work of Ryan Allured and contributors, under the
MIT licence in `LICENSE`. This fork adds the `gui/` package, the build
script, the test suite, and fixes to make the package import off Windows.

Fuller docs, including an architecture walkthrough, live in the
[wiki](https://github.com/jamccoy/PyXFocus_GUI/wiki).

## Upstream acknowledgement

Use of this software for academic and professional optical design work is permitted and encouraged.

Any publications resulting from use of this software shall include an acknowledgement of PyXFocus.
The suggested sentence for the acknowledgements section is:

This work makes use of PyXFocus, an open source Python-based raytracing package.

---

## Installing

**The folder must be named `PyXFocus`, not `PyXFocus_GUI`.** The package is
imported as `import PyXFocus.surfaces`, so the directory Python sees has to
be called `PyXFocus`, and its **parent** must be on your Python path. Since
this repository is named `PyXFocus_GUI`, a plain `git clone` would produce a
`PyXFocus_GUI/` folder and every import would fail. Clone into an explicit
target instead:

```bash
git clone https://github.com/jamccoy/PyXFocus_GUI.git PyXFocus
cd PyXFocus
```

(The proper fix is to make the project `pip install`-able so the folder name
stops mattering; until then, the explicit clone target is the workaround.)

### 1. Requirements

* Python 3, numpy, scipy, matplotlib
* `gfortran` (macOS: `brew install gcc`; Debian/Ubuntu: `apt install gfortran`)
* PyQt5, only if you want the GUI

### 2. Build the Fortran extensions

The repository ships pre-built **Windows** `.dll` files only. On macOS and
Linux you must compile the Fortran modules once:

```bash
python build_extensions.py
```

This builds six extension modules (`surfacesf`, `woltsurf`, `zernsurf`,
`transformationsf`, `reconstruct`, `specialfunctions`). It calls f2py through
`python -m numpy.f2py`, which guarantees they are built for the same
interpreter that will import them — if you build with one Python and import
with another, the modules will appear to be missing.

### 3. Check it worked

From the directory *containing* the `PyXFocus` folder:

```bash
python -m PyXFocus.test_smoke
```

All checks should pass. These verify the package imports and that the physics
is still right (an on-axis Wolter-I focuses to a point, off-axis coma grows
with field angle, and so on). This suite imports no Qt, so it works as an
install check whether or not you want the GUI.

If you are using the GUI there is a second suite covering the Qt layer:

```bash
python -m PyXFocus.test_gui_smoke
```

### Optional dependency

A few wavefront-fitting routines (`OPDtoZernike`, `OPDtoLegendre`,
`wavefront`, and the `zernsurf` surfaces) need the external `utilities`
imaging package:

```bash
pip install git+https://github.com/rallured/utilities.git
```

It is imported lazily, so **everything else works without it**. If you call a
function that needs it, you get an ImportError naming the package and the
install command.

## The Wolter-I Explorer (GUI)

A graphical front end for the package's core use case — designing and
misaligning a Wolter-I grazing-incidence telescope:

```bash
python -m PyXFocus.gui.app
```

### A double-clickable app (macOS)

To launch it from Finder or the Dock instead of a terminal, build a launcher
once:

```bash
python tools/make_icon.py       # only needed again if the artwork changes
python tools/make_launcher.py
```

This creates `~/Documents/software_development/PyXFocus.app` — a small
bundle whose launch script has this machine's interpreter path and this
repository's location baked in. It is a local build artifact, not something
that can be committed or shared: rebuild it if the repository moves or you
switch interpreters, and `python tools/make_launcher.py --check` will tell
you if it has gone stale. If it fails to start, the failure shows up as a
dialog rather than a bounced Dock icon, and the details land in
`~/Library/Logs/PyXFocus.log`.

Set the shell radius, focal length, mirror lengths, source off-axis angle and
secondary misalignment in the left-hand panel. The trace re-runs
automatically and reports:

* **Spot Diagram** — the focal-plane spot, in arcseconds, with the
  half-power diameter circled.
* **Telescope Layout** — the system in profile with rays converging on the
  focus, plus a zoom inset on the mirrors themselves (a Wolter-I is ~8 m long
  but only ~20 cm in radius, so the mirrors need their own scale).
* **Encircled Energy** — enclosed fraction vs. radius from the centroid.
* **Parameter Sweep** — vary one parameter and plot how performance responds.

Alongside: HPD and RMS radius in arcseconds, surviving ray count, throughput,
collecting area, and the best-focus position.

### Parameter sweep (tolerancing)

Pick a parameter, give it a range and a step count, and the sweep re-traces
at each point and plots HPD against it, with throughput on a second axis.
This is the alignment-budget question — how far can a mirror shift before
image quality goes out of spec. A trace takes about 0.01 s, so a 30-point
sweep is effectively instantaneous. Results export to CSV.

Read HPD and throughput *together*. Past a large enough misalignment HPD can
start improving again purely because vignetting has thrown away every ray
except those near the sweet spot — the telescope is getting worse while its
headline number gets better. The throughput curve is what exposes that.

Points that cannot be traced (a misalignment beyond the guard, a geometry
that vignettes everything) are marked as "not traceable" and skipped rather
than aborting the sweep.

The same thing from a script:

```python
from PyXFocus.gui.wolter import WolterParams, sweep

result = sweep(WolterParams(), 'sec_dy', 0., 1.0, 30)
result.to_csv('tolerance_dy.csv')
```

### A caveat on collecting area

The reported collecting area is the **geometric** entrance aperture times the
vignetting fraction. It contains no mirror reflectivity, because PyXFocus
ships no reflectivity model — so it is an upper bound, not a true effective
area. A real Wolter-I loses roughly 10–20% per bounce with a good coating,
twice over, and considerably more as photon energy rises.

Making it a genuine effective area needs a coating reflectivity table indexed
by graze angle and photon energy; `analyses.grazeAngle` already provides the
per-ray graze angles to feed it.

**Show script** prints the plain PyXFocus script equivalent to the current
settings, so the GUI can be used to find a configuration and then hand it
back to you as code.

### What it remembers

The explorer reopens where you left it: window size and position, the
splitter, the active tab, the auto-trace setting, and the parameters you were
working on. Nothing else is stored, and nothing leaves your machine.

Settings live in the platform's usual place — on macOS,
`~/Library/Preferences/com.pyxfocus.WolterExplorer.plist`. To start from a
clean slate:

```bash
python -m PyXFocus.gui.app --reset-settings
```

Use the flag rather than deleting the file by hand: on macOS the preferences
daemon caches it in memory and rewrites it from that cache, so removing the
file appears to work and then the old settings come back.

If a remembered value no longer fits the panel's range — because a limit
changed between versions — it is clamped and the adjustment is reported in
the status bar alongside the first trace, never as a dialog.

### Misalignment limits

The secondary misalignment fields are capped at ±20 mm and ±15 arcmin. This
is not a physics limit — it guards a defect in the Fortran secondary solver
(`woltsurf.f95`): once the secondary is displaced far enough that rays no
longer intersect the hyperboloid, the Newton iteration never converges and
hangs with no error. Translations hang beyond roughly 80–100 mm and rotations
beyond 20–40 arcmin. Real Wolter-I alignment tolerances are microns and
arcseconds, and past about a millimetre so few rays survive vignetting that
the numbers stop meaning much, so the caps are generous in practice.
`PyXFocus.gui.wolter.trace` enforces the same limits with a clear `ValueError`
so scripts cannot hit the hang either.

## Scripting

The GUI is a thin shell over `PyXFocus.gui.wolter`, which has no Qt
dependency and is usable on its own:

```python
from PyXFocus.gui.wolter import WolterParams, trace

result = trace(WolterParams(r0=220., z0=8400., offaxis=1.0))
print(result.hpd_arcsec, result.num_surviving, result.collecting_area)
```

Or drive the raytracer directly. Rays are a list of ten arrays,
`[opd, x, y, z, l, m, n, ux, uy, uz]` — position, direction cosines, and the
normal of the last surface hit:

```python
import numpy as np
import PyXFocus.sources as sources
import PyXFocus.surfaces as surf
import PyXFocus.transformations as tran
import PyXFocus.analyses as anal
import PyXFocus.conicsolve as conic

r0, z0, length = 220., 8400., 100.

rin = conic.primrad(z0, r0, z0)
rout = conic.primrad(z0 + length, r0, z0)
rays = sources.annulus(rin, rout, 20000)
tran.transform(rays, 0, 0, -(z0 + length + 500.), 0, 0, 0)

surf.wolterprimary(rays, r0, z0)
tran.reflect(rays)
rays = tran.vignette(rays, ind=np.logical_and(rays[3] > z0,
                                              rays[3] < z0 + length))

surf.woltersecondary(rays, r0, z0)
tran.reflect(rays)
rays = tran.vignette(rays, ind=np.logical_and(rays[3] > z0 - length,
                                              rays[3] < z0))

surf.focusI(rays)
print('HPD [arcsec]:', anal.hpd(rays) / z0 * 180 / np.pi * 3600)
```

### Conventions

* Lengths are in **mm**, angles passed to `transform` are in **radians**.
* The Wolter focus is at the origin; `+z` points back toward the sky.
* The primary/secondary node is at `z = z0` with radius `r0`, so `z0` is the
  focal length.
* `transform` moves the *coordinate system*, not the rays; `itransform`
  undoes it.

## Repository layout

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
| `build_extensions.py` | Compiles the Fortran extensions |
| `test_smoke.py` | Import and physics checks (no Qt) |
| `test_gui_smoke.py` | Settings persistence and window restore (needs PyQt5) |
| `gui/config.py` | Versioned JSON configuration format |
| `gui/settings.py` | What the app remembers between runs |
| `gui/icon.py` | The app icon, drawn programmatically |
| `tools/make_icon.py` | Builds `resources/PyXFocus.icns` |
| `tools/make_launcher.py` | Builds the double-clickable macOS `.app` |

### A note on `examples/`

Most scripts under `examples/` predate the current package layout. They
import a module named `traces` (an older name for this package) or the
removed monolithic `PyTrace` API, and will not run as written. Treat them as
reference for how traces were assembled rather than as runnable code;
`examples/wolterSchwarzschildTest.py` uses the current `PyXFocus.*` imports.
