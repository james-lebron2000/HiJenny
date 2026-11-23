"""FastAPI application package for HiJenny (app-dev owned)."""

from hijenny.api.main import app, create_app  # noqa: F401

__all__ = ["app", "create_app"]
