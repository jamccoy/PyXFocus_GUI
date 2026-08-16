# PyXFocus_GUI

A PyQt5 graphical front end for **PyXFocus**, the general purpose raytracing
package for X-ray telescope design, plus the cross-platform build and test
tooling needed to run it outside Windows.

This is a fork of [kbuffo/PyXFocus](https://github.com/kbuffo/PyXFocus).
The raytracing engine — the Fortran kernels, surfaces, sources and analysis
routines — is the original work of Ryan Allured and contributors, under the
MIT licence in `LICENSE`. This fork adds the `gui/` package, the build
script, the test suite, and fixes to make the package import off Windows.

Fuller docs, including an architecture walkthrough, live in `docs/` — in the
app under **Help → Documentation**, and online in the
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

* Python 3.9 or newer (3.12 is what `requirements.txt` pins against)
* `gfortran` (macOS: `brew install gcc`; Debian/Ubuntu: `apt install gfortran`)
* Everything else is in `requirements.txt`

**Use a virtual environment owned by this project.** The package has no
metadata, so `import PyXFocus` resolves by folder name and `sys.path`, and the
Fortran extensions are compiled per interpreter — so on a machine with several
Pythons, installing a dependency into the wrong one is silent, and the
application keeps working quietly without it.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Use `.venv/bin/python` for everything below, and point the launcher at it.
`pyqtgraph` and `PyOpenGL` are needed only by the GPU-drawn 3D layout tab;
without them it still works, drawn by matplotlib. `PYXFOCUS_3D_BACKEND=opengl`
or `=matplotlib` forces a renderer.

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

Set the shell radius, focal length, mirror lengths, shell count, source
off-axis angle and secondary misalignment in the left-hand panel. The
**Grating** and **Detector** groups are optional — tick the group to fit that
part. The trace re-runs automatically and reports:

* **Spot Diagram** — the focal-plane spot, in arcseconds, with the
  half-power diameter circled.
* **Telescope Layout** — the system in profile with rays converging on the
  focus, plus a zoom inset on the mirrors themselves (a Wolter-I is ~8 m long
  but only ~20 cm in radius, so the mirrors need their own scale).
* **Encircled Energy** — enclosed fraction vs. radius from the centroid.
* **Parameter Sweep** — vary one parameter and plot how performance responds.
* **3D Layout** — the same system rotatable, which is the view that shows
  azimuth, decentre and tilt. A profile plot cannot: a secondary tilted by
  5 arcmin looks, in profile, exactly like a secondary.

Alongside: HPD and RMS radius in arcseconds, surviving ray count, throughput,
collecting area, and the best-focus position.

### Reading the 3D view

The view is drawn by the GPU where `pyqtgraph` and `PyOpenGL` are installed,
and by matplotlib where they are not. Both draw the same scene — the geometry,
the framing and the z compression are decided once, in `gui/scene3d.py` — so
the two cannot disagree about the telescope. Set `PYXFOCUS_3D_BACKEND` to
`opengl` or `matplotlib` to force one.

Drag to orbit, scroll to zoom **toward the pointer**, ctrl-drag or middle-drag
to pan, and use the **Iso / Down axis / Side** buttons to get back to a known
viewpoint. Shift-drag a rectangle (or arm **Zoom box**) to frame exactly that
region in one gesture — **left-to-right zooms in on the box, right-to-left
zooms back out through it**, and the two are exact opposites, so the reverse
drag undoes the first. On a trackpad, **pinch to zoom**. Changing a parameter
and re-tracing leaves the camera where you put it.

There is no zoom limit worth speaking of. The status line reads out how wide
the view is the whole way down — metres to nanometres — which is what makes
deep zoom trustworthy once the axis triad is off screen. For scale: a
seven-order fan lands across about 30 mm at the focal plane, 5 mm per order,
and one order's own spot is roughly 4 microns.

The controls, each there for a reason worth knowing:

* **Mirrors only** zooms from the whole 8 m system to the 20 cm of optics,
  and trims the rays where they leave that span. Away from that zoom the z
  axis is compressed, and the status line says by how much — an unlabelled
  38:1 squash makes a Wolter-I look like a Cassegrain.
* **Solid shells** shades the mirrors instead of drawing them as wireframes.
  Under OpenGL these are whole shells: the depth test runs per fragment while
  depth *writes* are disabled on surfaces, so a shell is transparent to the
  rays inside it and still occludes correctly among them. The matplotlib
  fallback has to draw half a shell instead, because its 3D axes depth-sort
  each surface as a single unit and a closed opaque shell would swallow its
  own rays, differently at every camera angle.
* **True scale (1:1)** gives z the same scale as x and y, so nothing is
  distorted and a convergence angle measured on screen is the real one. The
  whole system then reads as a thread, which is why it is off by default —
  it is for looking closely at one part, not at everything.
* **Grooves** draws the grating's grooves and an arrow along the direction
  its orders disperse in. The grooves are schematic and the status line says
  so: a 240 mm grating at a 200 nm period has over a million of them.
* **Colour by order** colours each ray by the diffraction order it left the
  grating in — warm for positive, cool for negative, grey for the
  undiffracted beam. The spot tab uses the same colours, so an order can be
  followed from the grating to where it lands.

x and y are always to the same scale. Only z is ever compressed, because
azimuth, tilt and decentre all live in the x–y plane and that is the whole
reason for the view.

### Gratings

Tick **Grating fitted** to put one in the converging beam. Two types:

* **Linear** — straight, evenly spaced grooves along y, so the dispersion is
  along x. `Groove period` is in nm.
* **Radial** — grooves converging on a hub, which is what an X-ray
  spectrometer actually uses behind a Wolter, the beam being converging
  rather than collimated. Specified by `Period gradient` in **nm per mm** of
  distance from the hub, so the local period is that times the radius — a
  different quantity from the linear period despite both describing groove
  spacing, which is why they are separate fields. `Hub offset` is how far
  off-axis the grooves converge; keep it well outside the beam, because a hub
  near the beam is a region where the local period runs towards zero and the
  image smears (0.4 arcsec at a hub of 8400 mm, 6 at 500 mm, 233 at 0).

`Order` is the **reference** order: HPD, RMS, best focus, throughput and the
surviving-ray count are all measured on it alone. `Extra orders ±` puts more
orders in flight so the dispersion can be seen; they are drawn and never
measured, so raising it cannot move a number. All orders are drawn at equal
weight — PyXFocus has no groove-efficiency model, which a real grating very
much does.

### Nested shells

`Number of shells` close-packs concentric shells outward from `r₀`, each one
starting `Shell wall + gap` outside the last. Collecting area grows with the
nest while on-axis resolution does not degrade, since every shell shares one
focus.

The ray budget is the *total* across the nest, split between shells by
collecting area, so a twenty-shell design costs about what one shell costs
(≈20 ms) and the spot keeps a constant number of points. Adding an outer
shell therefore changes the image because of optics, not because of
sampling — each shell is seeded independently so the inner shells' ray
patterns do not reshuffle when you add another.

Rays are not traced between shells. Each shell is an independent bundle;
modelling a ray that leaves one shell and strikes its neighbour would need a
branching tracer.

### Gratings and a placed detector

The **Grating** group puts a linear diffraction grating in the converging
beam at a given height above the focus, with its own groove period, order,
wavelength and half-width. Grooves run along y, so dispersion is along x, and
order 0 reproduces the system without it. Rays outside the grating are
vignetted, and evanescent orders are counted as lost rather than silently
turning the focus into NaN.

The **Detector** group replaces "find best focus from the rays" with "put the
image plane where the design says". Defocus and detector tilt then show up in
the spot and the HPD as real degradations — an autofocus quietly absorbs
both. Arcsecond conversions follow the detector: the lever arm is node to
image plane, which stops being `z₀` as soon as the detector moves.

Sweeping `det_z` over a few millimetres is a through-focus scan.

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

## Documentation

The docs live in `docs/` as Markdown, and that folder is the source of truth.
They reach you two ways: **Help → Documentation** in the app, reading HTML
generated from them, and the [wiki](https://github.com/jamccoy/PyXFocus_GUI/wiki),
which is a published mirror. Edit `docs/`, never the wiki's web editor — the
next publish overwrites anything typed there.

The app reads generated HTML rather than the Markdown itself because it
cannot read Markdown. `QTextBrowser.setMarkdown` arrived in Qt 5.14, and the
interpreter these extensions are built for ships Qt 5.9, so the conversion
happens ahead of time and the result is committed, the same way
`resources/PyXFocus.icns` is. After editing any page:

```bash
pip install markdown        # once; only needed to build, not to read
python tools/build_docs.py
```

`python tools/build_docs.py --check` reports whether the committed HTML still
matches the Markdown, and `test_smoke.py` runs that check, so forgetting to
rebuild fails the suite instead of silently shipping the previous wording.
The check itself needs no `markdown` install.

To update the wiki afterwards:

```bash
python tools/publish_wiki.py --check    # what would change
python tools/publish_wiki.py            # write it, then commit in the wiki checkout
```

It writes files and stops; committing and pushing the wiki stays a separate,
deliberate step.

## Repository layout

| Module | Purpose |
| --- | --- |
| `sources.py` | Ray sources (point, annulus, converging beam, …) |
| `surfaces.py` | Surfaces to trace to (Wolter, conic, sphere, cylinder, …) |
| `transformations.py` | Coordinate transforms, reflection, refraction, gratings, vignetting |
| `analyses.py` | Centroid, RMS, HPD, wavefront and OPD fitting |
| `conicsolve.py` | Wolter-I prescription maths (radii, focus, sag) |
| `lenses.py` | Singlet and doublet lenses |
| `gui/optics.py` | Optical systems as lists of elements, and the tracer that walks them |
| `gui/wolter.py` | Wolter-I expressed in those elements, plus parameter sweeps |
| `gui/app.py` | PyQt5 Wolter-I Explorer |
| `gui/tabs/` | One module per plot tab, registered in `tabs/__init__.py` |
| `build_extensions.py` | Compiles the Fortran extensions |
| `test_smoke.py` | Import and physics checks (no Qt) |
| `test_gui_smoke.py` | Settings persistence and window restore (needs PyQt5) |
| `gui/config.py` | Versioned JSON configuration format |
| `gui/settings.py` | What the app remembers between runs |
| `gui/icon.py` | The app icon, drawn programmatically |
| `gui/docs_index.py` | Which documentation pages exist, and where they live |
| `gui/docview.py` | The Help → Documentation viewer |
| `docs/` | The documentation itself, in Markdown — the source of truth |
| `gui/docs/` | HTML generated from `docs/`, committed, what the viewer reads |
| `tools/build_docs.py` | Renders `docs/*.md` → `gui/docs/*.html` |
| `tools/publish_wiki.py` | Copies `docs/*.md` into a GitHub wiki checkout |
| `tools/make_icon.py` | Builds `resources/PyXFocus.icns` |
| `tools/make_launcher.py` | Builds the double-clickable macOS `.app` |

### Adding an optic

`gui/optics.py` holds the general form: an optical system is an ordered list
of elements, and an element answers to exactly two audiences.

* `trace_system` applies it — `trace_to` (the surface call), `placement`,
  `aperture`, `terminal`.
* A viewer draws it — `profile()` for a 2D view, `patches()` for a 3D one,
  and `kind` so the viewer can style it without knowing what it is.

Everything about pushing and popping coordinate frames, cutting vignetted
rays and keeping ray identities aligned lives in `Element.apply`, so a new
element cannot get that wrong. A surface of revolution needs only a
`radius_at(z)`; both its profile and its 3D mesh follow from it, which is what
keeps the 2D and 3D views from growing two different ideas of where a mirror
is.

`surfaces.py` already ships Wolter–Schwarzschild, silicon pore optics,
ellipsoid–hyperboloid, general conics, paraxial lenses and flats, none of
which the GUI reaches yet. Each is an element class away.

`wolter.build_system` is the only code that knows what a `WolterParams` is.
Anything that can produce a `System` — a different telescope family, or a
per-element editor — needs no change to the tracer.

### A note on `examples/`

Most scripts under `examples/` predate the current package layout. They
import a module named `traces` (an older name for this package) or the
removed monolithic `PyTrace` API, and will not run as written. Treat them as
reference for how traces were assembled rather than as runnable code;
`examples/wolterSchwarzschildTest.py` uses the current `PyXFocus.*` imports.
