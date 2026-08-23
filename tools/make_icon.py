#!/usr/bin/env python
"""Render the app icon into ``resources/PyXFocus.icns``.

    .venv/bin/python tools/make_icon.py

The artwork stays here -- ``PyXFocus.gui.icon`` draws a Wolter-I in profile with
matplotlib, which is why there is no binary asset in this repository. The
``sips``/``iconutil`` dance that turns it into the ten-variant iconset macOS
wants now comes from :mod:`endstation.tools.make_icon`, shared with the other
projects here.

Three copies of that dance existed, identical down to the ten-entry variant
table, which ``iconutil`` is fussy about and which fails without naming the
entry that is wrong. This is what is left of ours.

The ``.icns`` is committed, since the app bundle needs it and rendering it needs
matplotlib; re-run this only when the artwork changes.
"""

from __future__ import annotations

import os
import sys

# This file lives in <PyXFocus>/tools/, so REPO is the PyXFocus folder itself,
# and REPO's *parent* has to be on sys.path -- the package is imported as
# PyXFocus.gui.icon, and the folder is named PyXFocus.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(REPO))

from endstation.tools.make_icon import build_icns  # noqa: E402

OUTPUT = os.path.join(REPO, 'resources', 'PyXFocus.icns')


def main():
    icns = build_icns('PyXFocus.gui.icon', OUTPUT)
    size = os.path.getsize(icns)
    print('wrote %s (%d bytes)' % (icns, size))
    print('wrote %s' % str(icns).replace('.icns', '.png'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
