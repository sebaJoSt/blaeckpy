"""Blaeck and its sub-devices: registration, tick(), built-in commands."""

from importlib.metadata import version as _version

# What the device list says this library is. Loggbok gates every protocol v2 capability on the
# name and version, so these are its own, never blaeck's. The golden tests set blaeck's in their
# place while they play a scenario, so the device list matches blaeck's byte for byte.
LIBRARY_NAME = "blaeckpy"
LIBRARY_VERSION = _version("blaeckpy")
