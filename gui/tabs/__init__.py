"""
The plot-area tabs.

Everything under this package imports Qt by definition. ``gui/wolter.py``
and ``gui/config.py`` must not, and ``test_qt_free_modules_stay_qt_free``
is what enforces that.

The matplotlib backend is selected here rather than in ``app.py`` because
a tab module can now be imported on its own, so ``app.py`` is no longer
guaranteed to run first. A package's ``__init__`` running before any of its
submodules is the one ordering Python does guarantee.
"""

import matplotlib
matplotlib.use('Qt5Agg')
