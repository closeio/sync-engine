"""
Per-provider backend modules for sending mail.

A backend module *must* meet the following requirements:

1. Specify the provider it implements as the module-level `PROVIDER` variable.
For example, 'gmail', 'eas' etc.

2. Specify the name of the sendmail class as the module-level
`SENDMAIL_CLS` variable.

"""

from inbox.util.misc import register_backends

module_registry = register_backends(__name__, __path__)
