# Wolter-I Explorer (GUI)

A graphical front end for the package's core use case — designing and misaligning a Wolter-I grazing-incidence telescope. See **[Installation](Installation.md)** first if you haven't built the Fortran extensions yet.

```bash
python -m PyXFocus.gui.app
```

Set the shell radius, focal length, mirror lengths, source off-axis angle, and secondary misalignment in the left-hand panel. The trace re-runs automatically and reports:

* **Spot Diagram** — the focal-plane spot, in arcseconds, with the half-power diameter circled.
* **Telescope Layout** — the system in profile with rays converging on the focus, plus a zoom inset on the mirrors themselves (a Wolter-I is ~8 m long but only ~20 cm in radius, so the mirrors need their own scale).
* **Encircled Energy** — enclosed fraction vs. radius from the centroid.
* **Parameter Sweep** — vary one parameter and plot how performance responds.
* **3D Layout** — the same system rotatable, which is the view that shows azimuth, decentre and tilt. A profile plot cannot: a secondary tilted by 5 arcmin looks, in profile, exactly like a secondary.

Alongside: HPD and RMS radius in arcseconds, surviving ray count, throughput, collecting area, and the best-focus position.

## The 3D layout

Drawn by the GPU where `pyqtgraph` and `PyOpenGL` are installed and by matplotlib where they are not, from one shared scene description (`gui/scene3d.py`) so the two renderers cannot disagree about the telescope. `PYXFOCUS_3D_BACKEND=opengl` or `=matplotlib` forces one.

Drag to orbit, scroll to zoom, and use **Iso / Down axis / Side x–z / Side y–z** to return to a known viewpoint. **Down axis** is the one a nested design most wants: it shows the shells as concentric annuli and a grating's dispersion as a displacement across them. Re-tracing after a parameter change leaves the camera where you put it.

The status line under the view carries what the picture cannot say for itself. Above all the **z compression**: a Wolter-I is roughly 8 m long and 20 cm in radius, so drawn to scale it is an invisible thread. The z axis is squashed and the factor is stated, because an unlabelled 38:1 squash makes a Wolter-I look like a Cassegrain. x and y are never squashed against each other — azimuth, tilt and decentre all live in that plane and are the whole reason for the view.

The checkboxes:

* **Mirrors only** zooms from the whole system to the optics, and trims the rays where they leave that span.
* **Solid shells** shades the surfaces. Under OpenGL these are whole shells: the depth test runs per fragment while depth *writes* are disabled on surfaces, so a shell is transparent to what is inside it and still occludes correctly among the rays. The matplotlib fallback draws half a shell instead, because its 3D axes depth-sort each surface as a single unit — a closed opaque shell would swallow its own rays, differently at every camera angle.
* **Grooves** draws the grating grooves and the dispersion arrow.
* **Colour by order** colours each ray by the diffraction order it left the grating in.

## Gratings

Tick **Grating fitted** to put one in the converging beam.

**Linear** gratings have straight, evenly spaced grooves along y, so the dispersion is along x — which means **Side x–z** shows the fan and **Side y–z** looks straight down it. `Groove period` is in nm.

**Radial** gratings have grooves converging on a hub, which is the arrangement an X-ray spectrometer actually uses behind a Wolter, the beam being converging rather than collimated. A radial grating is specified by `Period gradient` in **nm per mm** of distance from the hub — the local period is that times the radius. That is a different quantity from the linear `Groove period` despite both describing groove spacing, which is why they are separate fields rather than one reused one. `Hub offset` is how far off-axis the grooves converge, and wants to be well outside the beam: near it the local period runs towards zero and the dispersion varies sharply across the aperture. Measured on the default design: 0.4 arcsec at a hub of 8400 mm, 6 arcsec at 500 mm, 233 arcsec at 0.

`Order` is the **reference** order. HPD, RMS, best focus, throughput and the surviving-ray count are all measured on it and on nothing else. `Extra orders ±` puts more orders in flight so the dispersion can be seen — they are drawn, never measured, so raising it cannot move a number. The status readout names both counts once a fan is in flight, since "rays surviving" would otherwise quietly change meaning.

Two honest caveats. The grooves drawn on the grating are a direction cue and not a depiction: a 240 mm grating at a 200 nm period carries over a million of them, and the status line says as much. And every order is drawn at equal weight, because PyXFocus has no groove-efficiency model — a real grating puts very different amounts of light into different orders.

## Parameter sweep (tolerancing)

Pick a parameter, give it a range and a step count, and the sweep re-traces at each point and plots HPD against it, with throughput on a second axis. This is the alignment-budget question — how far can a mirror shift before image quality goes out of spec. A trace takes about 0.01 s, so a 30-point sweep is effectively instantaneous. Results export to CSV.

Read HPD and throughput *together*. Past a large enough misalignment, HPD can start improving again purely because vignetting has thrown away every ray except those near the sweet spot — the telescope is getting worse while its headline number gets better. The throughput curve is what exposes that.

Points that cannot be traced (a misalignment beyond the guard — see **[Known Limitations](Known-Limitations.md)** — or a geometry that vignettes everything) are marked "not traceable" and skipped rather than aborting the sweep.

The same thing from a script:

```python
from PyXFocus.gui.wolter import WolterParams, sweep

result = sweep(WolterParams(), 'sec_dy', 0., 1.0, 30)
result.to_csv('tolerance_dy.csv')
```

## A caveat on collecting area

The reported collecting area is the **geometric** entrance aperture times the vignetting fraction. It contains no mirror reflectivity, because PyXFocus ships no reflectivity model — so it is an upper bound, not a true effective area. A real Wolter-I loses roughly 10–20% per bounce with a good coating, twice over, and considerably more as photon energy rises.

Making it a genuine effective area needs a coating reflectivity table indexed by graze angle and photon energy; `analyses.grazeAngle` already provides the per-ray graze angles to feed it.

**Show script** prints the plain PyXFocus script equivalent to the current settings, so the GUI can be used to find a configuration and then hand it back to you as code.

## What it remembers

The explorer reopens where you left it: window size and position, the splitter, the active tab, the auto-trace setting, and the parameters you were working on. Nothing else is stored, and nothing leaves your machine.

Settings live in the platform's usual place — on macOS, `~/Library/Preferences/com.pyxfocus.WolterExplorer.plist`. To start from a clean slate:

```bash
python -m PyXFocus.gui.app --reset-settings
```

Use the flag rather than deleting the file by hand: on macOS the preferences daemon caches it in memory and rewrites it from that cache, so removing the file appears to work and then the old settings come back.

If a remembered value no longer fits the panel's range — because a limit changed between versions — it is clamped and the adjustment is reported in the status bar alongside the first trace, never as a dialog.

Configuration files (saved/loaded from the GUI) are versioned JSON — see **[Architecture and Repository Layout](Architecture-and-Repository-Layout.md)** for the format and why it's designed the way it is.

See also: **[Known Limitations](Known-Limitations.md)** for the misalignment caps, and **[Scripting and Ray Conventions](Scripting-and-Ray-Conventions.md)** to drive the same trace from code.
