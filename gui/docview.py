"""
The documentation viewer behind Help -> Documentation.

Reads ``docs/*.md`` and renders each page as it is opened, with endstation's
markdown renderer, into endstation's reading pane: a reading font, a column of
readable width, and tables in the theme's colours. See
:mod:`PyXFocus.gui.docs_index` for which pages there are.

Three things it does that a bare QTextBrowser would not:

* **Non-modal.** ``MainWindow.show_script`` calls ``exec()`` and blocks;
  documentation that freezes the application you are reading about is
  documentation you cannot follow along with. The caller keeps a reference,
  because a non-modal QDialog that goes out of scope is collected and
  vanishes; that is what ``MainWindow._docs_window`` is for.
* **Its own history.** A page is set as HTML, which gives the browser no
  history of its own, so Back and Forward walk two lists of page keys here.
* **Its own links.** ``setOpenLinks(False)``: a link to another page opens
  in this window, and a web link goes to the system browser. Left to itself,
  QTextBrowser would load the web page into the pane as a blank document.

A theme change re-renders the page in place, keeping the reader's position,
because the tables' colours are fixed when a page is set.
"""

from endstation.qt import theming
from endstation.qt.reading import ReadingBrowser, render_into, text_color
from PySide6 import QtCore, QtGui, QtWidgets

from PyXFocus.gui import docs_index


class DocsWindow(QtWidgets.QDialog):
    """The bundled documentation, one page at a time."""

    #: Wide enough for the module table in Architecture-and-Repository-Layout
    #: at the reading pane's column width.
    DEFAULT_SIZE = (1000, 720)

    def __init__(self, parent=None, open_external=None):
        super(DocsWindow, self).__init__(parent)
        #: Injected so the test can watch for the call without a browser
        #: opening on the developer's desktop -- the same seam as
        #: AppSettings(settings=...).
        self._open_external = (open_external if open_external is not None
                               else QtGui.QDesktopServices.openUrl)
        self._pages = docs_index.pages()
        #: Page keys behind and ahead of the one shown.
        self._back = []
        self._forward = []
        self._key = None

        self.setWindowTitle('PyXFocus Documentation')
        self.resize(*self.DEFAULT_SIZE)

        self.contents = QtWidgets.QListWidget()
        self.contents.setMaximumWidth(230)
        for spec in docs_index.PAGES:
            item = QtWidgets.QListWidgetItem(spec.title)
            item.setData(QtCore.Qt.UserRole, spec.key)
            self.contents.addItem(item)
        self.contents.currentItemChanged.connect(self._on_contents_selected)

        self.browser = ReadingBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._on_anchor)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(self.contents)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(1, 1)

        self.back_button = QtWidgets.QPushButton('Back')
        self.back_button.clicked.connect(self.go_back)
        self.forward_button = QtWidgets.QPushButton('Forward')
        self.forward_button.clicked.connect(self.go_forward)

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
        theming.subscribe(self._on_theme)

    # -- navigation --------------------------------------------------------

    def show_page(self, key, fragment=''):
        """Display page ``key``, adding the page left behind to Back."""
        if key == self._key:
            if fragment:
                self.browser.scrollToAnchor(fragment)
            return
        if self._key is not None:
            self._back.append(self._key)
            del self._forward[:]
        self._display(key)
        if fragment:
            self.browser.scrollToAnchor(fragment)

    def go_back(self):
        if self._back:
            self._forward.append(self._key)
            self._display(self._back.pop())

    def go_forward(self):
        if self._forward:
            self._back.append(self._key)
            self._display(self._forward.pop())

    def current_key(self):
        """The page being shown, or None before the first one loads."""
        return self._key

    def _display(self, key, keep_position=False):
        """Render ``key`` into the pane and bring everything else into step."""
        scroll = self.browser.verticalScrollBar()
        position = scroll.value()
        if key in self._pages:
            source = self._pages[key].read()
        else:
            source = ('# %s\n\n`docs/%s.md` is missing from this copy.\n'
                      % (docs_index.title_for(key), key))
        render_into(self.browser, source,
                    device_pixel_ratio=self.devicePixelRatioF(),
                    text_color=text_color(self))
        scroll.setValue(position if keep_position else 0)
        self._key = key
        self.back_button.setEnabled(bool(self._back))
        self.forward_button.setEnabled(bool(self._forward))
        self._select_in_contents(key)

    def _on_theme(self, _theme):
        """Re-render in the new colours; the reader keeps their place."""
        if self._key is not None:
            self._display(self._key, keep_position=True)

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
        if key is None or key not in self._pages:
            QtWidgets.QMessageBox.information(
                self, 'Unknown link',
                'This link points at %s, which is not one of the bundled '
                'pages.' % url.toString())
            return
        self.show_page(key, fragment)

    def _select_in_contents(self, key):
        """Keep the contents list in step with whatever the pane shows."""
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
