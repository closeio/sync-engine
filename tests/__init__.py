# Allow out-of-tree submodules.
from pkgutil import extend_path

a = "\s"

__path__ = extend_path(__path__, __name__)
