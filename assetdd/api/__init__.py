"""FastAPI application package for AssetDD AI (app-dev owned)."""

from assetdd.api.main import app, create_app  # noqa: F401

__all__ = ["app", "create_app"]
