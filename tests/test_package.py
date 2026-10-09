import blaeckpy
from blaeckpy import _device


def test_version_comes_from_the_package_metadata():
    assert blaeckpy.__version__ == "1.0.0.dev0"


def test_the_device_list_names_blaeckpy_and_its_own_version():
    # Loggbok knows blaeckpy from 1.0.0 on and reads the leading x.y.z of the version.
    assert _device.LIBRARY_NAME == "blaeckpy"
    assert _device.LIBRARY_VERSION == blaeckpy.__version__
    assert _device.LIBRARY_VERSION.startswith("1.")
