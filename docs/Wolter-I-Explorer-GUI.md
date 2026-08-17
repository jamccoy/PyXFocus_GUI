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

Drag to orbit, scroll to zoom **toward the pointer**, ctrl-drag or middle-drag to pan, and use **Iso / Down axis / Side x–z / Side y–z** to return to a known viewpoint. Shift-drag a rectangle — or arm the **Zoom box** button — to frame exactly that region in one gesture. The drag is reversible: **left to right** means "look at this box" and closes in on it, **right to left** means "shrink what I have into this box" and pulls back out. They are exact inverses, so drawing the same rectangle the other way returns you precisely where you were, which makes the reverse drag an undo you can aim rather than a second guess. (Left-to-right in, right-to-left out is AutoCAD's convention for a drag that means two things.)

On a Mac trackpad, **pinch to zoom** — it zooms about the fingers, like the wheel zooms about the pointer. Two-finger scroll still zooms as well.

Zoom is not limited: the only floor is a guard against a degenerate projection. That matters because the interesting features are three to six orders of magnitude smaller than the instrument. A seven-order fan lands across about 30 mm at the focal plane, 5 mm per order, and a single order's spot is roughly 4 microns — so inspecting one means going from metres to microns in the same view. The status line reads the width out the whole way down, which is what keeps that trustworthy once the axis triad is off screen.

**Down axis** is the preset a nested design most wants: it shows the shells as concentric annuli and a grating's dispersion as a displacement across them. Re-tracing after a parameter change leaves the camera where you put it.

The status line under the view carries what the picture cannot say for itself. Above all the **z compression**: a Wolter-I is roughly 8 m long and 20 cm in radius, so drawn to scale it is an invisible thread. The z axis is squashed and the factor is stated, because an unlabelled 38:1 squash makes a Wolter-I look like a Cassegrain. x and y are never squashed against each other — azimuth, tilt and decentre all live in that plane and are the whole reason for the view.

The checkboxes:

* **Mirrors only** zooms from the whole system to the optics, and trims the rays where they leave that span.
* **Solid shells** shades the surfaces. Under OpenGL these are whole shells: the depth test runs per fragment while depth *writes* are disabled on surfaces, so a shell is transparent to what is inside it and still occludes correctly among the rays. The matplotlib fallback draws half a shell instead, because its 3D axes depth-sort each surface as a single unit — a closed opaque shell would swallow its own rays, differently at every camera angle.
* **Grooves** draws the grating grooves and the dispersion arrow.
* **Colour by order** colours each ray by the diffraction order it left the grating in.
* **True scale (1:1)** gives z the same scale as x and y. Everything else in this view compresses z by 9 to 38 times so an 8 m telescope fits a window, which is honest about lengths but exaggerates every angle along the axis. With 1:1 on, a convergence angle measured on screen is the real one — and the whole system becomes an invisible thread, which is exactly why it is off by default. It is for looking closely at one part, not at everything.

## Gratings

Tick **Grating fitted** to put one in the converging beam.

**Linear** gratings have straight, evenly spaced grooves along y, so the dispersion is along x — which means **Side x–z** shows the fan and **Side y–z** looks straight down it. `Groove period` is in nm.

**Radial** gratings have grooves converging on a hub, which is the arrangement an X-ray spectrometer actually uses behind a Wolter, the beam being converging rather than collimated. A radial grating is specified by `Period gradient` in **nm per mm** of distance from the hub — the local period is that times the radius. That is a different quantity from the linear `Groove period` despite both describing groove spacing, which is why they are separate fields rather than one reused one. `Hub offset` is how far off-axis the grooves converge, and wants to be well outside the beam: near it the local period runs towards zero and the dispersion varies sharply across the aperture. Measured on the default design: 0.4 arcsec at a hub of 8400 mm, 6 arcsec at 500 mm, 233 arcsec at 0.

`Order` is the **reference** order. HPD, RMS, best focus, throughput and the surviving-ray count are all measured on it and on nothing else. `Extra orders ±` puts more orders in flight so the dispersion can be seen — they are drawn, never measured, so raising it cannot move a number. The status readout names both counts once a fan is in flight, since "rays surviving" would otherwise quietly change meaning.

## The detector

Tick **Detector placed** to put the image surface where the design says, instead of at best focus.

**`Detector half-width`** is new and defaults to 0, meaning unbounded — which is what this has always silently been. A detector with no size catches every ray however far off it lands, and draws nothing in the 3D view. Give it a size and it does both.

**Two tilts.** `Detector tilt about x` has always existed; `Detector tilt about y` is the one that leans the detector *along* the dispersion, since the dispersion runs in x. The x tilt rotates perpendicular to it and cannot follow a dispersed focus.

**`Detector shape` — Flat or Cylindrical.** This is the one that matters for a spectrometer. A grating in a converging beam does not bring its orders to a single plane: on the default design with orders ±3 in flight, the outer orders focus 0.40 mm ahead of the reference order, so on a flat detector they land 0.62 arcsec across against an aberration floor of 0.38. Bending the detector along the dispersion — what a Rowland circle is for — recovers most of that:

| order | flat | curved (R = 200 mm) |
|---|---|---|
| m = ±1 | 0.098″ | 0.090″ |
| m = ±2 | 0.274″ | 0.210″ |
| m = ±3 | 0.622″ | 0.373″ |

In resolving power, which is the number that matters: **R at m = ±3 goes from 592 to 985, a 66% gain.** Order 0 sits on the apex where the two surfaces touch and does not move at all.

`Detector radius` defaults to 200 mm, which is the measured optimum for the default design. It is not a universal constant — the best radius follows `grating_z`, the period and the wavelength, so it is worth sweeping for any design you care about. Below about 175 mm it degrades sharply.

## Resolving power

The Spot Diagram legend reports **R = λ/Δλ** per order once a fan is in flight — how close two wavelengths can be and still be told apart.

It is measured rather than assumed. Because the landing position goes as `m·λ`, `dx/dλ` is `(m/λ)·dx/dm`, and `dx/dm` is just the spacing between adjacent orders that the fan has already put on the detector. So R needs no extra trace, assumes no particular groove law, and works for a radial grating as readily as a linear one:

> R = m × (spacing between adjacent orders) ÷ (spot width)

It agrees with the direct `λ·(dx/dλ)/w` to about 0.2%, and the small gap is real rather than noise: the direct form uses the paraxial `dx/dλ = m·L/d`, while the measured spacing carries the `tan` nonlinearity of where a ray actually lands. The measured one is the more correct of the two.

Order 0 gets no R — it does not disperse, so it resolves nothing. Neither does a single order in flight, since there is no spacing to measure; you get nothing rather than a fabricated number.

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
