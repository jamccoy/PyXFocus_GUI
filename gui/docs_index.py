"""
Where the documentation lives, and what order it reads in.

Qt-free on purpose, like :mod:`PyXFocus.gui.wolter` and
:mod:`PyXFocus.gui.config`: ``test_smoke.py`` imports it to check the pages
and their links, and ``tools/publish_wiki.py`` to know the wiki's address.
Neither should need PySide6 installed, and
``test_qt_free_modules_stay_qt_free`` is what enforces that.

``docs/`` at the repository root holds the Markdown and is the only copy. It
is what you edit, what ``git diff`` shows, what Help -> Documentation renders
when a page is opened, and what ``tools/publish_wiki.py`` copies to the
GitHub wiki.
"""

import collections
import os

from endstation.docs import load_pages


PageSpec = collections.namedtuple('PageSpec', 'key title')

#: Every page in the documentation viewer, in reading order.
#:
#: NOT append-only -- and that is the difference from
#: :data:`PyXFocus.gui.tabs.TABS`, which is. No page index is persisted
#: anywhere: the viewer opens on ``PAGES[0]`` every time, nothing in
#: QSettings or a saved configuration refers to a page by position. So
#: reorder and insert freely; only the ``key`` is load-bearing.
#:
#: ``key`` must equal the Markdown basename. Link resolution
#: (:func:`key_for_href`) and the wiki publish both key off it, and the wiki
#: page name is derived from it too -- renaming a key renames a published
#: page and breaks anyone's bookmark.
#:
#: ``title`` is what the viewer's contents list says. It is not read from the
#: page, because the Home page's heading is the project's name.
PAGES = (
    PageSpec('Home', 'Home'),
    PageSpec('Installation', 'Installation'),
    PageSpec('Wolter-I-Explorer-GUI', 'Wolter-I Explorer (GUI)'),
    PageSpec('Scripting-and-Ray-Conventions', 'Scripting and Ray Conventions'),
    PageSpec('Architecture-and-Repository-Layout',
             'Architecture and Repository Layout'),
    PageSpec('Known-Limitations', 'Known Limitations'),
)

#: The wiki these pages are published to, and the Help menu's "view online".
WIKI_URL = 'https://github.com/jamccoy/PyXFocus_GUI/wiki'

_GUI_DIR = os.path.dirname(os.path.abspath(__file__))

#: Markdown, hand-written, the only copy.
SOURCE_DIR = os.path.join(os.path.dirname(_GUI_DIR), 'docs')


def source_path(key):
    """The Markdown file for ``key``."""
    return os.path.join(SOURCE_DIR, key + '.md')


def keys():
    """Every page key, in reading order."""
    return [spec.key for spec in PAGES]


def pages():
    """The pages as endstation's :class:`~endstation.docs.PageIndex`, in order."""
    return load_pages(
        SOURCE_DIR, tuple(keys()),
        missing_message='The docs/ folder is missing from this copy of '
                        'PyXFocus_GUI. The pages are also on the wiki: '
                        + WIKI_URL)


def title_for(key):
    """The display title for ``key``, or the key itself if it is not ours."""
    for spec in PAGES:
        if spec.key == key:
            return spec.title
    return key


def key_for_href(href):
    """
    The page key a link target refers to, or None if it is not one of ours.

    The pages link to each other as ``Installation.md``, so the links also
    work when the folder is browsed on github.com. Anything else, including
    an http(s) URL, is not ours to resolve.
    """
    name = os.path.basename(href)
    if not name.endswith('.md'):
        return None
    name = name[:-len('.md')]
    return name if name in set(keys()) else None
