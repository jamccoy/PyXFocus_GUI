# PyXFocus_GUI

A PyQt5 graphical front end for **PyXFocus**, the general-purpose raytracing package for X-ray telescope design, plus the cross-platform build and test tooling needed to run it outside Windows.

This is a fork of [kbuffo/PyXFocus](https://github.com/kbuffo/PyXFocus). The raytracing engine — the Fortran kernels, surfaces, sources, and analysis routines — is the original work of Ryan Allured and contributors, under the MIT licence in `LICENSE`. This fork adds the `gui/` package (the "Wolter-I Explorer"), the cross-platform build script, the test suite, and fixes to make the package import off Windows.

## Wiki contents

* **[Installation](Installation.md)** — cloning gotchas, requirements, building the Fortran extensions, smoke tests
* **[Wolter-I Explorer GUI](Wolter-I-Explorer-GUI.md)** — using the graphical front end: tracing, the five views, the 3D layout, gratings and diffraction orders, parameter sweeps, what it remembers
* **[Scripting and Ray Conventions](Scripting-and-Ray-Conventions.md)** — driving PyXFocus from code, the ray-array format, units and coordinate conventions
* **[Architecture and Repository Layout](Architecture-and-Repository-Layout.md)** — how the package is organized and why, module by module
* **[Known Limitations](Known-Limitations.md)** — the misalignment solver's hang, the collecting-area caveat, what a grating model does and does not include

For the fastest path to a working install and a one-paragraph overview, [README.md](https://github.com/jamccoy/PyXFocus_GUI/blob/master/README.md) in the repository is still the canonical quick reference; these pages expand on it.

## Upstream acknowledgement

Use of this software for academic and professional optical design work is permitted and encouraged.

Any publications resulting from use of this software shall include an acknowledgement of PyXFocus. The suggested sentence for the acknowledgements section is:

> This work makes use of PyXFocus, an open source Python-based raytracing package.
