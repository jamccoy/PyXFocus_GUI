# Installation

## The folder name matters

**The folder must be named `PyXFocus`, not `PyXFocus_GUI`.** The package is imported as `import PyXFocus.surfaces`, so the directory Python sees has to be called `PyXFocus`, and its **parent** must be on your Python path. Since this repository is named `PyXFocus_GUI`, a plain `git clone` would produce a `PyXFocus_GUI/` folder and every import would fail. Clone into an explicit target instead:

```bash
git clone https://github.com/jamccoy/PyXFocus_GUI.git PyXFocus
cd PyXFocus
```

(The proper fix is to make the project `pip install`-able so the folder name stops mattering; until then, the explicit clone target is the workaround.)

## 1. Requirements

* Python 3.9 or newer (3.12 is what `requirements.txt` pins against)
* `gfortran` (macOS: `brew install gcc`; Debian/Ubuntu: `apt install gfortran`)
* Everything else is in `requirements.txt`

### Use a virtual environment owned by this project

Not optional advice. This repository has no package metadata, so
`import PyXFocus` resolves by folder name and `sys.path`. On a machine with
several Pythons that is genuinely ambiguous, and the Fortran extensions make
it worse: they are compiled **per interpreter**, so a `.so` built by one
Python is invisible to another. Installing a dependency into the wrong
interpreter is silent — the application keeps working, quietly without it.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Then use `.venv/bin/python` for everything below, and point the launcher at
it (see §4). One environment, named once.

`pyqtgraph` and `PyOpenGL` are in there but are needed only by the GPU-drawn
3D layout tab; without them that tab still works, drawn by matplotlib
instead. `PYXFOCUS_3D_BACKEND=opengl` or `=matplotlib` forces a renderer, and
this prints which one you would get:

```bash
.venv/bin/python -c "from PyXFocus.gui import tabs; print(tabs.opengl_available())"
```

Run that from the directory *containing* `PyXFocus`, and run it with the same
interpreter the application uses — that is exactly the check whose absence
let a release ship with the GPU renderer silently disabled.

## 2. Build the Fortran extensions

The repository ships pre-built **Windows** `.dll` files only. On macOS and Linux you must compile the Fortran modules once:

```bash
python build_extensions.py
```

This builds six extension modules (`surfacesf`, `woltsurf`, `zernsurf`, `transformationsf`, `reconstruct`, `specialfunctions`). It calls f2py through `python -m numpy.f2py`, which guarantees they are built for the same interpreter that will import them — if you build with one Python and import with another, the modules will appear to be missing. Run it as `.venv/bin/python build_extensions.py` for the same reason.

On Python 3.12 and newer this needs `meson` and `ninja`, which `requirements.txt` installs: 3.12 removed `distutils`, so f2py falls back to its meson backend. That backend compiles in a temporary directory, where the `include 'specialFunctions.f95'` at the top of three of the sources cannot be resolved — so `build_extensions.py` puts the source directory on the Fortran include path via `FFLAGS`. (`--f90flags` does not work here; the meson backend ignores it.)

## 3. Check it worked

From the directory *containing* the `PyXFocus` folder:

```bash
python -m PyXFocus.test_smoke
```

All checks should pass. These verify the package imports and that the physics is still right (an on-axis Wolter-I focuses to a point, off-axis coma grows with field angle, and so on). This suite imports no Qt, so it works as an install check whether or not you want the GUI.

If you are using the GUI there is a second suite covering the Qt layer:

```bash
python -m PyXFocus.test_gui_smoke
```

## Optional dependency

A few wavefront-fitting routines (`OPDtoZernike`, `OPDtoLegendre`, `wavefront`, and the `zernsurf` surfaces) need the external `utilities` imaging package:

```bash
pip install git+https://github.com/rallured/utilities.git
```

It is imported lazily, so **everything else works without it**. If you call a function that needs it, you get an `ImportError` naming the package and the install command.

## A double-clickable app (macOS)

To launch the GUI from Finder or the Dock instead of a terminal, build a launcher once:

```bash
python tools/make_icon.py       # only needed again if the artwork changes
python tools/make_launcher.py
```

This creates a small `.app` bundle whose launch script has this machine's interpreter path and this repository's location baked in. It is a **local build artifact**, not something that can be committed or shared: rebuild it if the repository moves or you switch interpreters, and `python tools/make_launcher.py --check` will tell you if it has gone stale.

If it fails to start, the failure shows up as a dialog rather than a bounced Dock icon, and the details land in `~/Library/Logs/PyXFocus.log`.

Next: **[Wolter-I Explorer GUI](Wolter-I-Explorer-GUI.md)** or **[Scripting and Ray Conventions](Scripting-and-Ray-Conventions.md)**.
