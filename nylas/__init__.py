# NOTE: This is copied straight from the nylas python bindings' top-level
# __init__.py, with some modifications to make it work if the client SDK
# isn't installed. Don't change it unless you want to introduce import bugs
# based on install ordering.
from pkgutil import extend_path

# Allow out-of-tree submodules.
__path__ = extend_path(__path__, __name__)

try:
    from nylas.client.client import APIClient

    __all__ = ["APIClient"]
except ImportError:
    pass
