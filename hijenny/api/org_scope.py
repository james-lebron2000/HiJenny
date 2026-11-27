"""Organization context supplied by the deployment gateway."""
from fastapi import HTTPException
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hijenny import config


def request_org(header: str | None) -> str | None:
    value = (header or "").strip()
    if not value and config.REQUIRE_ORG_HEADER:
        raise HTTPException(403, "项目访问需要由身份网关提供组织标识。")
    return value or None

class PrivateProjectCachePolicy:
    """Set cache directives on private API responses, including files and scoped errors."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        async def send_response(message: Message):
            if message["type"] == "http.response.start":
                path = getattr(scope.get("route"), "path", "")
                private = path.startswith(("/api/projects/", "/api/files/", "/api/documents/", "/api/evidence/",
                                           "/api/questions/", "/api/findings/", "/api/runs/"))
                if private or path in {"/api/projects", "/api/discovery/projects"}:
                    headers = MutableHeaders(scope=message)
                    headers["Cache-Control"] = "private, no-store"
                    headers.add_vary_header("X-Org-Id")
            await send(message)

        await self.app(scope, receive, send_response)
