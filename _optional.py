"""
Lazy loading for optional third-party dependencies.

Some PyXFocus functions (Zernike/Legendre wavefront fitting, wavefront
padding) rely on the external ``utilities`` imaging package.  Importing it
at module scope meant a missing ``utilities`` broke *every* function in
``analyses`` and ``surfaces``, including the core raytrace routines that
never touch it.

``optional_module`` defers the import until an attribute is actually used,
so the package imports cleanly without ``utilities`` installed and only the
functions that genuinely need it raise -- with a message saying what to
install.

The implementation now comes from :mod:`endstation.optional`, shared with the
other projects here.  This wrapper keeps PyXFocus's own three-positional
signature -- ``(name, purpose, install)`` -- so the call sites in ``analyses``
and ``surfaces`` read exactly as they did, and so ``test_smoke`` keeps testing
the contract this package actually offers.

``utilities`` is worth noting as the reason the shared version grew an explicit
install command: it is installed from a git URL and is not an extra of anything,
so a message offering ``pip install '.[something]'`` would have been wrong.
"""

from endstation.optional import optional_module as _optional_module

__all__ = ['optional_module', 'UTILITIES_INSTALL']


def optional_module(name, purpose, install):
    """
    Return a proxy for ``name`` that imports on first use.

    Parameters
    ----------
    name : str
        Fully qualified module name, e.g. ``'utilities.imaging.man'``.
    purpose : str
        Short description of what needs it, used in the error message.
    install : str
        Command that installs the dependency.

    Returns
    -------
    proxy
        Object forwarding attribute access to the real module, importing it
        on the first access and raising an ``ImportError`` naming ``install``
        if it is not there.
    """
    return _optional_module(name, purpose=purpose, install=install)


#: Install hint for the ``utilities`` imaging package.
UTILITIES_INSTALL = 'pip install git+https://github.com/rallured/utilities.git'
