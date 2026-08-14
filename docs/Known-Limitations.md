# Known Limitations

## Misalignment limits

The secondary misalignment fields (in the GUI and in `PyXFocus.gui.wolter.WolterParams`) are capped at **±20 mm** and **±15 arcmin**. This is not a physics limit — it guards a real defect in the Fortran secondary solver (`woltsurf.f95`): once the secondary is displaced far enough that rays no longer intersect the hyperboloid, the Newton iteration never converges and hangs with no error.

Measured empirically: translations hang somewhere between 80 and 100 mm, rotations between 20 and 40 arcmin. The caps sit well inside that margin. They're still enormous next to real Wolter-I alignment tolerances (microns and arcseconds) — past roughly a millimetre, so few rays survive vignetting that the numbers stop meaning much anyway, so the caps are generous in practice, not restrictive.

`PyXFocus.gui.wolter.trace` (via `check_misalignment`) enforces the same limits with a clear `ValueError`, so a script hits a readable error instead of the same hang.

## Collecting area has no reflectivity model

The collecting area reported by the GUI and by `trace()` is the **geometric** entrance aperture times the vignetting fraction. It contains no mirror reflectivity, because PyXFocus ships no reflectivity model — so it is an upper bound, not a true effective area.

A real Wolter-I loses roughly 10–20% per bounce with a good coating, twice over (primary and secondary), and considerably more as photon energy rises. Turning this into a genuine effective area needs a coating reflectivity table indexed by graze angle and photon energy; `analyses.grazeAngle` already computes the per-ray graze angles that such a table would need as input — the missing piece is the table itself, not the plumbing.

## No CI

There's currently no `.github/workflows` — `build_extensions.py`, `test_smoke.py`, and `test_gui_smoke.py` are run locally rather than on push/PR.

## Stale examples

See the note on `examples/` in **[Architecture and Repository Layout](Architecture-and-Repository-Layout.md)** — most of that folder predates the current API and won't run as-is.
