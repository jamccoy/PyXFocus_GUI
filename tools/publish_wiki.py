#!/usr/bin/env python
"""Copy docs/*.md into a GitHub wiki checkout.

    python tools/publish_wiki.py --check    # what would change
    python tools/publish_wiki.py            # write it

``docs/`` is the source of truth; the wiki is a mirror of it. Editing a page
in GitHub's web editor therefore writes into the mirror, and the next publish
overwrites it -- edit ``docs/`` instead.

The copying is endstation's ``endstation-publish-wiki``, which also drops the
``.md`` from links between pages, since the wiki serves ``Installation.md``
at ``/wiki/Installation``. This wrapper adds this project's defaults: the
wiki checkout beside the repository, a refusal to write into a folder that
is not a git checkout, and a ``--check`` that exits 1 when the wiki is out
of date.

This never commits and never pushes. It writes files into the checkout and
tells you what it changed; ``git -C <wiki> diff`` and a commit of your own
are the next step, deliberately, because publishing is a visible action.
"""

import argparse
import os
import sys

from endstation.tools.publish_wiki import publish

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Sibling of the repository, which is where `git clone` of the wiki lands
#: if you run it next to the working copy.
DEFAULT_WIKI = os.path.join(os.path.dirname(REPO), 'PyXFocus_GUI.wiki')


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--wiki', default=DEFAULT_WIKI,
                        help='the wiki checkout (default: %s)' % DEFAULT_WIKI)
    parser.add_argument('--check', action='store_true',
                        help='report what would change without writing')
    args = parser.parse_args()

    if not os.path.isdir(os.path.join(args.wiki, '.git')):
        print('error: %s is not a git checkout -- refusing to write into it.\n'
              '       git clone https://github.com/jamccoy/PyXFocus_GUI.wiki.git'
              % args.wiki, file=sys.stderr)
        return 1

    changed = publish(os.path.join(REPO, 'docs'), args.wiki,
                      dry_run=args.check)
    verb = 'would change' if args.check else 'wrote'
    for path in changed:
        print('  %s %s' % (verb, os.path.basename(str(path))))
    if not changed:
        print('%s is already up to date' % args.wiki)
    elif args.check:
        print('\n%d page(s) out of date in %s' % (len(changed), args.wiki))
    else:
        print('\n%d page(s) written. Review and commit:\n'
              '  git -C %s diff' % (len(changed), args.wiki))
    return 1 if (args.check and changed) else 0


if __name__ == '__main__':
    sys.exit(main())
