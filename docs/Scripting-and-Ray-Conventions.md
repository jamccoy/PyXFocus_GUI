# Scripting and Ray Conventions

## The one-call API

The GUI is a thin shell over `PyXFocus.gui.wolter`, which has no Qt dependency and is usable on its own:

```python
from PyXFocus.gui.wolter import WolterParams, trace

result = trace(WolterParams(r0=220., z0=8400., offaxis=1.0))
print(result.hpd_arcsec, result.num_surviving, result.collecting_area)
```

`sweep` (see **[Wolter-I Explorer GUI](Wolter-I-Explorer-GUI.md)** for the tolerancing use case) works the same way without a GUI in the loop.

## Driving the raytracer directly

Rays are a list of ten arrays, `[opd, x, y, z, l, m, n, ux, uy, uz]` — path-length delay, position, direction cosines, and the normal of the last surface hit:

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

## Conventions

* Lengths are in **mm**, angles passed to `transform` are in **radians**.
* The Wolter focus is at the origin; `+z` points back toward the sky.
* The primary/secondary node is at `z = z0` with radius `r0`, so `z0` is the focal length.
* The primary spans `z0 -> z0 + primary_length`; the secondary spans `z0 - secondary_length -> z0`.
* `transform` moves the *coordinate system*, not the rays; `itransform` undoes it.
* A ray carries **no wavelength, order, energy or intensity**. There is no eleventh array. Anything per-ray beyond the ten has to be kept in a parallel array of your own, and re-indexed at every cut — `tran.vignette` and any dead-ray drop return a *subsequence*, not a prefix, so an index array is the only thing that stays aligned. `tran.grat` takes its order and wavelength as such arrays; `tran.radgrat` takes them as scalars, so several orders means several calls with `ind=` masks.

## Tracing several diffraction orders

`gui.optics.Beam` is what keeps that bookkeeping honest inside the GUI's tracer: it holds `orders` and `waves` alongside `ids`, and `Beam.cut` re-indexes all of them together, so there is exactly one place a cut can be got wrong. A grating fans one incident ray into one per order at the moment it diffracts, which costs the copies only on the short leg after the grating rather than on the whole telescope.

Ray ids are handed out in **blocks**, one slot per order in flight, and a fan only ever fills slots inside its own block. Ids therefore stay strictly increasing, which everything downstream depends on — `merge`, `stack_paths` and `choose_paths` all recover a ray's row with `searchsorted`.

Metrics are measured on one **reference** order and never averaged across the fan. `Beam.focus_weights` returns a 0/1 weight array for `surf.focusI`, because an unweighted focus solve puts the image plane between the dispersed spots where none of them is in focus: measured at HPD 0.089 → 237 arcsec with seven orders in flight. `Show script` in the GUI writes all of this out as plain PyXFocus, including the parallel `ray_order` array and its re-indexing, which is the readable version of the whole scheme.

See **[Architecture and Repository Layout](Architecture-and-Repository-Layout.md)** for what each module (`sources`, `surfaces`, `transformations`, `analyses`, `conicsolve`, ...) is responsible for.
