#!/usr/bin/env python
"""Render docs/*.md into the HTML the in-app viewer displays.

    /opt/anaconda3/bin/python tools/build_docs.py            # build it
    /opt/anaconda3/bin/python tools/build_docs.py --check    # verify it is current

Why generate at all, when Qt can render Markdown itself: it cannot, here.
``QTextBrowser.setMarkdown`` arrived in Qt 5.14, and the interpreter that can
actually import this package -- the Python 3.8 the ``.so`` extensions were
built for, which is the one ``tools/make_launcher.py`` bakes into the app
bundle -- ships Qt 5.9.7. So the Markdown is converted ahead of time, by a
real parser, into the HTML4 subset Qt 5.9's rich-text engine does render.

The output is committed, like ``resources/PyXFocus.icns``: a build artifact
kept in the tree because the thing that consumes it cannot produce it.

Building needs the ``markdown`` package (``pip install markdown``), which is
a *developer* dependency -- you need it to change the documentation, not to
read it. ``--check`` deliberately does not need it, so the tripwire in
``test_smoke.py`` stays runnable in the dependency-free install check.
"""

from __future__ import print_function

import argparse
import io
import os
import re
import sys

#: This file lives in <PyXFocus>/tools/, so REPO's *parent* is what has to be
#: on sys.path -- the package is imported as PyXFocus.gui.docs_index.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.dirname(REPO) not in sys.path:
    sys.path.insert(0, os.path.dirname(REPO))

from PyXFocus.gui import docs_index


#: Bump when TEMPLATE or STYLE changes, so already-generated pages are
#: reported stale even though their source Markdown has not moved.
FORMAT_VERSION = 1

MARKER = '<!-- pyxfocus-docs v%d sha256:%s -->'
_MARKER_RE = re.compile(r'^<!-- pyxfocus-docs v(\d+) sha256:([0-9a-f]{64}) -->')

#: Qt 5.9's rich text is a subset of HTML4 / CSS2.1 -- no flexbox, no custom
#: properties, no web fonts. Everything here was checked against it: block
#: backgrounds on <pre> land, table borders land, and the monospace stack is
#: the same one show_script() uses so code looks the same in both places.
STYLE = """
body { font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
       font-size: 13px; line-height: 145%; margin: 12px; }
h1 { font-size: 21px; margin: 2px 0 10px 0; }
h2 { font-size: 17px; margin: 20px 0 6px 0; }
h3 { font-size: 14px; margin: 16px 0 4px 0; }
p, li { margin: 6px 0; }
a { color: #1a5fb4; }
code { font-family: Menlo, Consolas, monospace; font-size: 12px;
       background-color: #f0f0f0; }
pre { font-family: Menlo, Consolas, monospace; font-size: 12px;
      background-color: #f4f4f4; padding: 8px; margin: 8px 0; }
table { border-width: 1px; border-style: solid; border-color: #cccccc;
        margin: 10px 0; }
th { background-color: #f0f0f0; padding: 4px 8px; text-align: left; }
td { padding: 4px 8px; }
blockquote { color: #555555; margin: 8px 0 8px 16px; }
"""

#: The charset declaration is load-bearing, not boilerplate. QTextBrowser
#: decodes a local file it has no charset for as Latin-1, so every em dash in
#: these pages -- and there are many -- renders as "â€”". Qt looks for this
#: meta tag; without it the text is quietly mojibaked rather than failing.
TEMPLATE = """%(marker)s
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
<style type="text/css">%(style)s</style>
</head>
<body>
%(body)s
</body>
</html>
"""

#: Links in docs/*.md point at Markdown (so they also work when the folder is
#: browsed on github.com); the generated pages have to point at each other.
_HREF_MD = re.compile(r'(href=")([^":]+)\.md(#[^"]*)?(")')

#: Qt 5.9's scrollToAnchor matches <a name="...">, NOT id="..." -- the
#: markdown package's toc extension only emits the latter, so a #fragment
#: link would silently do nothing. This adds the anchor Qt actually looks for.
_HEADING_ID = re.compile(r'<h([1-6]) id="([^"]+)"')

#: A fenced block arrives as <pre><code>...</code></pre>. Qt paints the inner
#: <code>'s background per *text line* rather than over the block, so the
#: nesting renders as a run of grey stripes with ragged right edges instead
#: of one panel. Unwrapping to a bare <pre> leaves exactly one element with a
#: background, which Qt does paint as a block. The trailing newline goes too
#: -- inside <pre> it is a visible blank final line.
_PRE_CODE = re.compile(r'<pre><code[^>]*>(.*?)\s*</code></pre>', re.DOTALL)


def _to_html(key):
    """Convert one page's Markdown to the body HTML Qt will render."""
    try:
        import markdown
    except ImportError:
        print('error: the markdown package is required to build the docs.\n'
              '       pip install markdown\n'
              '       (--check does not need it.)', file=sys.stderr)
        raise SystemExit(1)

    with io.open(docs_index.source_path(key), encoding='utf-8') as fh:
        text = fh.read()

    # 'extra' brings in tables, which Architecture-and-Repository-Layout.md
    # needs, and fenced code, which most of the pages use.
    body = markdown.markdown(text, extensions=['extra', 'toc'])
    body = _HREF_MD.sub(r'\1\2.html\3\4', body)
    body = _HEADING_ID.sub(r'<a name="\2"></a><h\1 id="\2"', body)
    body = _PRE_CODE.sub(r'<pre>\1</pre>', body)
    return body


def build():
    """Regenerate every page. Returns the number written."""
    if not os.path.isdir(docs_index.HTML_DIR):
        os.makedirs(docs_index.HTML_DIR)

    for key in docs_index.keys():
        html = TEMPLATE % {
            'marker': MARKER % (FORMAT_VERSION, docs_index.source_digest(key)),
            'style': STYLE,
            'body': _to_html(key),
        }
        with io.open(docs_index.html_path(key), 'w', encoding='utf-8') as fh:
            fh.write(html)
        print('  %s -> %s' % (key + '.md', key + '.html'))
    return len(docs_index.keys())


def check():
    """
    Verify every page is present and generated from the current Markdown.

    Imports no Markdown parser: it re-hashes the sources and compares against
    the marker each generated file carries. Same shape as
    ``make_launcher.check`` -- collect problems, print them, return an exit
    code -- so it works both as a CLI and as a test assertion.
    """
    problems = []
    expected = set()

    for key in docs_index.keys():
        source = docs_index.source_path(key)
        target = docs_index.html_path(key)
        expected.add(os.path.basename(target))

        if not os.path.isfile(source):
            problems.append('%s.md is in PAGES but missing from docs/' % key)
            continue
        if not os.path.isfile(target):
            problems.append('%s.html has never been generated' % key)
            continue

        with open(target) as fh:
            first = fh.readline().strip()
        match = _MARKER_RE.match(first)
        if match is None:
            problems.append('%s.html has no marker -- regenerate it' % key)
            continue

        version, digest = int(match.group(1)), match.group(2)
        if version != FORMAT_VERSION:
            problems.append('%s.html was built by format v%d, this is v%d'
                            % (key, version, FORMAT_VERSION))
        elif digest != docs_index.source_digest(key):
            problems.append('%s.md has changed since %s.html was generated'
                            % (key, key))

    if os.path.isdir(docs_index.HTML_DIR):
        for name in sorted(os.listdir(docs_index.HTML_DIR)):
            if name.endswith('.html') and name not in expected:
                problems.append('%s is not in PAGES -- delete it' % name)

    for problem in problems:
        print('  PROBLEM: %s' % problem)
    if not problems:
        print('docs: %d pages, all current' % len(docs_index.keys()))
    else:
        print('\nRun `python tools/build_docs.py` to regenerate.')
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true',
                        help='verify the HTML is current without rebuilding')
    args = parser.parse_args()

    if args.check:
        return check()

    build()
    return check()


if __name__ == '__main__':
    sys.exit(main())
