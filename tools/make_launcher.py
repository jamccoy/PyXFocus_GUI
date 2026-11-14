#!/usr/bin/env python
"""Make the Wolter-I Explorer launchable without a terminal.

    .venv/bin/python tools/make_launcher.py            # build it
    .venv/bin/python tools/make_launcher.py --check    # verify an existing one

Run it with the Python of the project's own virtualenv (see
requirements.txt): that interpreter is the one the launcher runs, and the
compiled Fortran extensions are built per interpreter, so "whichever python
is on PATH" is not a thing this application can be launched with.

What it does:

* **Every platform:** writes ``PyXFocus_GUI.pth`` into the virtualenv's
  site-packages, naming the folder that holds this checkout. That virtualenv
  can then ``import PyXFocus`` from any working directory, which is what
  this project has instead of package metadata: the package is imported by
  its folder name, and that folder's *parent* has to be on ``sys.path``.
* **macOS:** writes ``<venv>/bin/wolter-explorer``, a two-line script that
  runs ``python -m PyXFocus.gui.app``, and builds ``PyXFocus.app`` around it
  with ``endstation-app``'s ``build_app``. The bundle logs to
  ``~/Library/Logs/PyXFocus.log`` and raises a dialog naming that log if the
  explorer exits with an error, rather than bouncing the Dock icon once.
* **Windows:** draws the icon and writes a Desktop shortcut with
  ``endstation-shortcut``'s functions. It runs ``pythonw.exe -m
  PyXFocus.gui.app``, so no console window opens.
* **Linux:** the ``.pth`` is all there is; ``python -m PyXFocus.gui.app``
  then works from anywhere.

The bundle and the shortcut are build artifacts outside the repository, with
this machine's paths in them. Rebuild if the repository or the virtualenv
moves; ``--check`` says when one has gone stale.
"""

import argparse
import os
import sys
import subprocess
import sysconfig
from pathlib import Path

#: This file lives in <PyXFocus>/tools/. REPO_PARENT is what has to be on
#: sys.path: the parent of the PyXFocus folder, not the folder itself.
PYXFOCUS_DIR = Path(__file__).resolve().parent.parent
REPO_PARENT = PYXFOCUS_DIR.parent

DISPLAY_NAME = 'Wolter-I Explorer'
#: The bundle's executable and icon name, and so the log's: PyXFocus.log.
STEM = 'PyXFocus'
BUNDLE_ID = 'com.pyxfocus.WolterExplorer'
MODULE = 'PyXFocus.gui.app'
ICON_MODULE = 'PyXFocus.gui.icon'
PTH_NAME = 'PyXFocus_GUI.pth'
SCRIPT_NAME = 'wolter-explorer'
DEFAULT_APP_DIR = Path('~/Documents/software_development').expanduser()

LAUNCH_SCRIPT = '''#!/bin/sh
# Written by PyXFocus tools/make_launcher.py -- edit that, not this.
exec "{python}" -m {module} "$@"
'''


# --------------------------------------------------------------------------
# The parts every platform shares
# --------------------------------------------------------------------------

def in_virtualenv():
    """True when this interpreter is a virtualenv's, not a system Python."""
    return sys.prefix != getattr(sys, 'base_prefix', sys.prefix)


def site_packages():
    """Where this interpreter looks for ``.pth`` files."""
    return Path(sysconfig.get_path('purelib'))


def write_pth(directory, repo_parent=REPO_PARENT):
    """Write the ``.pth`` naming ``repo_parent`` into ``directory``."""
    path = Path(directory) / PTH_NAME
    path.write_text(str(repo_parent) + '\n', encoding='utf-8')
    return path


def check_pth(directory, repo_parent=REPO_PARENT):
    """Problems with the ``.pth`` in ``directory``; empty when it is right."""
    path = Path(directory) / PTH_NAME
    if not path.is_file():
        return ['%s is missing from %s' % (PTH_NAME, directory)]
    named = path.read_text(encoding='utf-8').strip()
    if Path(named) != Path(repo_parent):
        return ['%s names %s, but this checkout is in %s'
                % (PTH_NAME, named, repo_parent)]
    return []


def can_run_app(python):
    """
    Whether ``python`` imports the app from a neutral folder, and why not.

    Run from the filesystem root, so it is the ``.pth`` that finds PyXFocus
    and not a working directory that happens to be the right one.
    """
    probe = subprocess.run(
        [str(python), '-c', 'import PySide6.QtWidgets, %s' % MODULE],
        cwd=os.path.abspath(os.sep), capture_output=True, text=True,
        check=False)
    return probe.returncode == 0, probe.stderr.strip()


def launch_script(python, module=MODULE):
    """The macOS launcher script's text."""
    return LAUNCH_SCRIPT.format(python=python, module=module)


# --------------------------------------------------------------------------
# Per platform
# --------------------------------------------------------------------------

def build_mac(dest):
    from endstation.tools.make_app import App, build_app

    script = Path(sys.executable).parent / SCRIPT_NAME
    script.write_text(launch_script(sys.executable), encoding='utf-8')
    script.chmod(0o755)
    icon = PYXFOCUS_DIR / 'resources' / 'PyXFocus.icns'
    app = App(name=DISPLAY_NAME, launcher=script, bundle_id=BUNDLE_ID,
              stem=STEM, icon=icon if icon.is_file() else None,
              version='0.1.0')
    if app.icon is None:
        print('  no resources/PyXFocus.icns -- run tools/make_icon.py first '
              'for an icon')
    bundle = build_app(app, dest)
    print('built %s' % bundle)
    print('  runs:  %s' % script)
    print('  logs:  ~/Library/Logs/%s.log' % STEM)
    return bundle


def check_mac(bundle):
    from endstation.tools.make_app import check_app

    problems = check_app(bundle)
    script = Path(sys.executable).parent / SCRIPT_NAME
    if script.is_file() and script.read_text(encoding='utf-8') != \
            launch_script(sys.executable):
        problems.append('%s does not run this interpreter -- rebuild it'
                        % script)
    return problems


def build_windows(dest):
    from endstation.tools.make_shortcut import (Shortcut, build_ico,
                                                special_folder, write_shortcut)

    pythonw = Path(sys.executable).with_name('pythonw.exe')
    # Beside the virtualenv, as endstation-shortcut puts it.
    icon = build_ico(ICON_MODULE,
                     Path(sys.prefix).parent / ('%s.ico' % DISPLAY_NAME))
    shortcut = Shortcut(name=DISPLAY_NAME, python=pythonw, module=MODULE,
                        icon=icon, description='PyXFocus ' + DISPLAY_NAME)
    lnk = write_shortcut(shortcut, dest or special_folder('desktop'))
    print('wrote %s' % lnk)
    print('  runs: %s %s' % (pythonw, shortcut.arguments))
    return lnk


def check_windows(lnk):
    from endstation.tools.make_shortcut import check_shortcut
    return check_shortcut(lnk)


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------

def _where(args):
    """The bundle or shortcut this run builds or checks."""
    if sys.platform == 'darwin':
        return (args.dest or DEFAULT_APP_DIR) / ('%s.app' % STEM)
    if sys.platform == 'win32':
        from endstation.tools.make_shortcut import special_folder
        folder = args.dest or special_folder('desktop')
        return folder / ('%s.lnk' % DISPLAY_NAME)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dest', type=Path, default=None,
                        help='folder for the .app (default: %s) or the '
                             'shortcut (default: the Desktop)' % DEFAULT_APP_DIR)
    parser.add_argument('--check', action='store_true',
                        help='verify an existing launcher instead of building')
    args = parser.parse_args(argv)

    if not in_virtualenv():
        print('error: run this with the project\'s virtualenv Python, e.g.\n'
              '    .venv/bin/python tools/make_launcher.py\n'
              'It writes a .pth file into that environment, and will not '
              'write one into %s.' % sys.prefix, file=sys.stderr)
        return 1

    where = _where(args)
    if args.check:
        problems = check_pth(site_packages())
        if sys.platform == 'darwin':
            problems += check_mac(where)
        elif sys.platform == 'win32':
            problems += check_windows(where)
        for problem in problems:
            print('  PROBLEM: %s' % problem)
        if not problems:
            print('%s: ok' % (where or site_packages() / PTH_NAME))
        return 1 if problems else 0

    pth = write_pth(site_packages())
    print('wrote %s' % pth)
    ok, why = can_run_app(sys.executable)
    if not ok:
        print('\nerror: %s still cannot import the app:\n%s\n\n'
              'Refusing to build a launcher for it. The usual cause is '
              'extensions built for another interpreter:\n'
              '    %s build_extensions.py'
              % (sys.executable, why, sys.executable), file=sys.stderr)
        return 1

    if sys.platform == 'darwin':
        build_mac(where.parent)
    elif sys.platform == 'win32':
        build_windows(where.parent)
    else:
        print('`python -m %s` now works from any folder in this virtualenv.'
              % MODULE)
    return 0


if __name__ == '__main__':
    sys.exit(main())
