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

Alongside: HPD and RMS radius in arcseconds, surviving ray count, throughput, collecting area, and the best-focus position.

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
