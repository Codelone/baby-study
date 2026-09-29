"""Server-side model protocols, independent of catalog business logic."""

from .client import MimoClient, ModelAPIError

__all__ = ["MimoClient", "ModelAPIError"]
