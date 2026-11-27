"""Organization context supplied by the deployment gateway."""
from fastapi import HTTPException

from hijenny import config


def request_org(header: str | None) -> str | None:
    value = (header or "").strip()
    if not value and config.REQUIRE_ORG_HEADER:
        raise HTTPException(403, "项目访问需要由身份网关提供组织标识。")
    return value or None
