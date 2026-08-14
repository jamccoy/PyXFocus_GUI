"""
The documentation viewer behind Help -> Documentation.

Reads the HTML that ``tools/build_docs.py`` generates from ``docs/*.md``.
Nothing here parses Markdown: see :mod:`PyXFocus.gui.docs_index` for why the
conversion happens ahead of time rather than in Qt.

Two deliberate departures from ``MainWindow.show_script``, which is otherwise
the dialog pattern this follows:

* **Non-modal.** ``show_script`` calls ``exec_()`` and blocks; documentation
  that freezes the application you are reading about is documentation you
  cannot follow along with. The consequence is that the caller has to keep a
  reference -- a non-modal QDialog that goes out of scope is garbage
  collected and vanishes -- which is what ``MainWindow._docs_window`` is for.
* **QTextBrowser, driven by setSource, not setHtml.** ``setSource`` gives
  back/forward history, relative link resolution and (later) images for free.
  ``setHtml`` gives none of them and would mean hand-rolling a history stack.
  Verified working on Qt 5.9.7, which is what the app actually runs on.

``setOpenLinks(False)`` is what makes the interception possible: without it
QTextBrowser follows internal links itself and hands external ones to
nothing at all, so an http link would appear simply not to work.
"""

import os

from PyQt5 import QtCore, QtGui, QtWidgets

from PyXFocus.gui import docs_index


class DocsWindow(QtWidgets.QDialog):
    """The bundled documentation, one page at a time."""

    #: Wide enough for the module table in Architecture-and-Repository-Layout
    #: without horizontal scrolling.
    DEFAULT_SIZE = (940, 700)

    def __init__(self, parent=None, open_external=None):
        super(DocsWindow, self).__init__(parent)
        #: Injected so the test can watch for the call without a browser
        #: opening on the developer's desktop -- the same seam as
        #: AppSettings(settings=...).
        self._open_external = (open_external if open_external is not None
                               else QtGui.QDesktopServices.openUrl)

        self.setWindowTitle('PyXFocus Documentation')
        self.resize(*self.DEFAULT_SIZE)

        self.contents = QtWidgets.QListWidget()
        self.contents.setMaximumWidth(230)
        for spec in docs_index.PAGES:
            item = QtWidgets.QListWidgetItem(spec.title)
            item.setData(QtCore.Qt.UserRole, spec.key)
            self.contents.addItem(item)
        self.contents.currentItemChanged.connect(self._on_contents_selected)

        self.browser = QtWidgets.QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setSearchPaths([docs_index.HTML_DIR])
        self.browser.anchorClicked.connect(self._on_anchor)
        self.browser.sourceChanged.connect(self._on_source_changed)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(self.contents)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(1, 1)

        self.back_button = QtWidgets.QPushButton('Back')
        self.back_button.clicked.connect(self.browser.backward)
        self.back_button.setEnabled(False)
        self.browser.backwardAvailable.connect(self.back_button.setEnabled)

        self.forward_button = QtWidgets.QPushButton('Forward')
        self.forward_button.clicked.connect(self.browser.forward)
        self.forward_button.setEnabled(False)
        self.browser.forwardAvailable.connect(self.forward_button.setEnabled)

        online = QtWidgets.QPushButton('View on the wiki')
        online.setToolTip(docs_index.WIKI_URL)
        online.clicked.connect(
            lambda: self._open_external(QtCore.QUrl(docs_index.WIKI_URL)))

        close = QtWidgets.QPushButton('Close')
        close.clicked.connect(self.accept)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.back_button)
        row.addWidget(self.forward_button)
        row.addStretch(1)
        row.addWidget(online)
        row.addWidget(close)

        box = QtWidgets.QVBoxLayout(self)
        box.addWidget(splitter, 1)
        box.addLayout(row)

        self.show_page(docs_index.PAGES[0].key)

    # -- navigation --------------------------------------------------------

    def show_page(self, key, fragment=''):
        """
        Display page ``key``, optionally scrolled to ``fragment``.

        Goes through setSource rather than setHtml so the page joins the
        browser's own history and Back keeps working.
        """
        path = docs_index.html_path(key)
        if not os.path.isfile(path):
            # Only reachable if the generated HTML was deleted but the app
            # was not rebuilt; build_docs.check() is what normally catches
            # this, long before anyone clicks Help.
            self.browser.setHtml(
                '<h1>Not built</h1><p>%s is missing. Run '
                '<code>python tools/build_docs.py</code>.</p>'
                % os.path.basename(path))
            return
        url = QtCore.QUrl.fromLocalFile(path)
        if fragment:
            url.setFragment(fragment)
        self.browser.setSource(url)

    def current_key(self):
        """The page being shown, or None before the first one loads."""
        return docs_index.key_for_href(self.browser.source().fileName())

    def _on_anchor(self, url):
        """
        Route a clicked link: ours goes in this window, the web goes out.

        Nothing falls through silently -- a link that resolves to no page is
        reported in the window, because a documentation link that does
        nothing when clicked is indistinguishable from a frozen app.
        """
        if url.scheme() in ('http', 'https'):
            self._open_external(url)
            return

        fragment = url.fragment()
        if not url.fileName():
            # A bare "#section" link inside the page being read.
            if fragment:
                self.browser.scrollToAnchor(fragment)
            return

        key = docs_index.key_for_href(url.fileName())
        if key is None:
            QtWidgets.QMessageBox.information(
                self, 'Unknown link',
                'This link points at %s, which is not one of the bundled '
                'pages.' % url.toString())
            return
        self.show_page(key, fragment)

    def _on_source_changed(self, url):
        """Keep the contents list in step with whatever the browser shows."""
        key = docs_index.key_for_href(url.fileName())
        if key is None:
            return
        self.contents.blockSignals(True)
        for row in range(self.contents.count()):
            item = self.contents.item(row)
            if item.data(QtCore.Qt.UserRole) == key:
                self.contents.setCurrentRow(row)
                break
        self.contents.blockSignals(False)

    def _on_contents_selected(self, current, previous):
        if current is not None:
            self.show_page(current.data(QtCore.Qt.UserRole))
