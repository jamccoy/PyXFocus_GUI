# Known Limitations

## Misalignment limits

The secondary misalignment fields (in the GUI and in `PyXFocus.gui.wolter.WolterParams`) are capped at **±20 mm** and **±15 arcmin**. This is not a physics limit — it guards a real defect in the Fortran secondary solver (`woltsurf.f95`): once the secondary is displaced far enough that rays no longer intersect the hyperboloid, the Newton iteration never converges and hangs with no error.

Measured empirically: translations hang somewhere between 80 and 100 mm, rotations between 20 and 40 arcmin. The caps sit well inside that margin. They're still enormous next to real Wolter-I alignment tolerances (microns and arcseconds) — past roughly a millimetre, so few rays survive vignetting that the numbers stop meaning much anyway, so the caps are generous in practice, not restrictive.

`PyXFocus.gui.wolter.trace` (via `check_misalignment`) enforces the same limits with a clear `ValueError`, so a script hits a readable error instead of the same hang.

## Collecting area has no reflectivity model

The collecting area reported by the GUI and by `trace()` is the **geometric** entrance aperture times the vignetting fraction. It contains no mirror reflectivity, because PyXFocus ships no reflectivity model — so it is an upper bound, not a true effective area.

A real Wolter-I loses roughly 10–20% per bounce with a good coating, twice over (primary and secondary), and considerably more as photon energy rises. Turning this into a genuine effective area needs a coating reflectivity table indexed by graze angle and photon energy; `analyses.grazeAngle` already computes the per-ray graze angles that such a table would need as input — the missing piece is the table itself, not the plumbing.

## Gratings have no efficiency model

Tracing several diffraction orders puts equal numbers of rays into each one, because PyXFocus models a grating's geometry and not its groove profile. A real grating concentrates light into a blaze order and gives very little to the rest, so a multi-order picture shows *where* the orders go and says nothing whatever about how bright they are. `grating.py` has the blaze and Littrow analytics for designing a groove profile, but nothing in the trace consumes them.

For the same reason the metrics are measured on the reference order alone. Averaging over orders would otherwise be averaging over a weighting that is not physical.

## A radial grating's dispersion varies across the aperture

`tran.radgrat` defines a radial grating by a period *gradient*: the local period is `dpermm` times the distance from the hub. The dispersion therefore varies across the beam, and diverges as the beam approaches the hub. On the default design that is 0.4 arcsec with the hub 8400 mm off-axis, 6 arcsec at 500 mm, and 233 arcsec with the hub on the optical axis — the last being a grating sitting on top of its own convergence point, which is a real configuration and a useless one.

## `tran.radgratW` is unsafe for a converging beam

`transformationsf.f95` has two radial-grating kernels. `radgrat` takes a scalar wavelength and recovers the outgoing sign of `n` from the sign of `n` (line 216). `radgratW` takes a wavelength *array*, which would be the natural way to trace several orders or a spectral band at once — but it takes that sign from the sign of **y** instead (line 255). Rays travelling in −z at +y therefore come back out travelling in +z: half a converging beam is sent back up the telescope.

The GUI consequently never calls `radgratW`. A fanned radial grating loops over orders with `ind=` masks, one `radgrat` call each. The Fortran is left alone deliberately: fixing it is a change to the raytracing engine and wants its own test, not a drive-by edit.

`transformations.radgratcenter` is separately dead — it calls a `tran.radgratcenter` symbol that does not exist in the compiled extension, so it raises `AttributeError` for a scalar wavelength and silently falls through to `radgratW` (ignoring `hubdist` entirely) for an array one. Nothing in the repository calls it.

## No CI

There's currently no `.github/workflows` — `build_extensions.py`, `test_smoke.py`, and `test_gui_smoke.py` are run locally rather than on push/PR.

## Stale examples

See the note on `examples/` in **[Architecture and Repository Layout](Architecture-and-Repository-Layout.md)** — most of that folder predates the current API and won't run as-is.
