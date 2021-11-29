"""
Per-provider table modules.
"""
from inbox.util.misc import register_backends

module_registry = register_backends(__name__, __path__)
