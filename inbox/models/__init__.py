"""
Caution: subtleties ahead.

It's desirable to ensure that all SQLAlchemy models are imported before you
try to issue any sort of query. The reason you want this assurance is because
if you have mutually dependent relationships between models in separate
files, at least one of those relationships must be specified by a string
reference, in order to avoid circular import errors. But if you haven't
actually imported the referenced model by query time, SQLAlchemy can't resolve
the reference.

Previously, this was accomplished by doing:

from inbox.models.account import Account

etc. right here.
"""
from inbox.models.meta import load_models

locals().update({model.__name__: model for model in load_models()})
