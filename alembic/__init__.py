"""Application-owned migrations sharing the installed Alembic namespace."""

from importlib.metadata import version
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from . import context, op
from .runtime import plugins

__version__ = version("alembic")

__all__ = ["context", "op", "plugins"]
