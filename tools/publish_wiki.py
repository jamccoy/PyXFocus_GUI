#!/usr/bin/env python
"""Copy docs/*.md into a GitHub wiki checkout.

    python tools/publish_wiki.py --check    # what would change
    python tools/publish_wiki.py            # write it

``docs/`` is the source of truth; the wiki is a mirror of it. Editing a page
in GitHub's web editor therefore writes into the mirror, and the next publish
overwrites it -- edit ``docs/`` instead.

The one transformation is link style. ``docs/*.md`` links to
``Installation.md`` so the links work when the folder is browsed on
github.com; the wiki serves that page at ``/wiki/Installation`` and wants
``](Installation)``.

This never commits and never pushes. It writes files into the checkout and
tells you what it changed; ``git -C <wiki> diff`` and a commit of your own
are the next step, deliberately, because publishing is a visible action.
"""

from __future__ import print_function

import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.dirname(REPO) not in sys.path:
    sys.path.insert(0, os.path.dirname(REPO))

from PyXFocus.gui import docs_index


#: Sibling of the repository, which is where `git clone` of the wiki lands
#: if you run it next to the working copy.
DEFAULT_WIKI = os.path.join(os.path.dirname(REPO), 'PyXFocus_GUI.wiki')

_HREF_MD = re.compile(r'\]\(([^)/:#]+)\.md(#[^)]*)?\)')


def _for_wiki(text):
    """Strip the .md suffix from links to our own pages."""
    def strip(match):
        target, frag = match.group(1), match.group(2) or ''
        if target in set(docs_index.keys()):
            return '](%s%s)' % (target, frag)
        return match.group(0)
    return _HREF_MD.sub(strip, text)


def publish(wiki_dir, dry_run):
    """Write every page into ``wiki_dir``. Returns an exit code."""
    if not os.path.isdir(wiki_dir):
        print('error: %s is not a directory.\n'
              '       git clone https://github.com/jamccoy/PyXFocus_GUI.wiki.git'
              % wiki_dir, file=sys.stderr)
        return 1
    if not os.path.isdir(os.path.join(wiki_dir, '.git')):
        print('error: %s is not a git checkout -- refusing to write into it'
              % wiki_dir, file=sys.stderr)
        return 1

    changed = []
    for key in docs_index.keys():
        with open(docs_index.source_path(key)) as fh:
            wanted = _for_wiki(fh.read())

        target = os.path.join(wiki_dir, key + '.md')
        current = None
        if os.path.isfile(target):
            with open(target) as fh:
                current = fh.read()

        if current == wanted:
            continue
        changed.append(key)
        if not dry_run:
            with open(target, 'w') as fh:
                fh.write(wanted)

    verb = 'would change' if dry_run else 'wrote'
    if changed:
        for key in changed:
            print('  %s %s.md' % (verb, key))
        if dry_run:
            print('\n%d page(s) out of date in %s' % (len(changed), wiki_dir))
        else:
            print('\n%d page(s) written. Review and commit:\n'
                  '  git -C %s diff' % (len(changed), wiki_dir))
    else:
        print('%s is already up to date (%d pages)'
              % (wiki_dir, len(docs_index.keys())))
    return 1 if (dry_run and changed) else 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--wiki', default=DEFAULT_WIKI,
                        help='the wiki checkout (default: %s)' % DEFAULT_WIKI)
    parser.add_argument('--check', action='store_true',
                        help='report what would change without writing')
    args = parser.parse_args()
    return publish(args.wiki, args.check)


if __name__ == '__main__':
    sys.exit(main())
