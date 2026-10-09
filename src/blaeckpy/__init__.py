"""blaeckpy: the Python counterpart of the blaeck Arduino library.

Speaks protocol v2 as blaeck 7 does, so a host such as Loggbok cannot tell the two apart.
Not usable yet: the public API (Blaeck and the types it returns) arrives with the device core.
"""

from importlib.metadata import version as _version

__version__ = _version("blaeckpy")

__all__ = ["__version__"]
