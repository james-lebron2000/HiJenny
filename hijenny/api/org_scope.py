"""Organization context supplied by the deployment gateway."""
import re

from fastapi import HTTPException, Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hijenny import config


def request_org(header: str | None) -> str | None:
    value = (header or "").strip()
    if not value and config.REQUIRE_ORG_HEADER:
        raise HTTPException(403, "项目访问需要由身份网关提供组织标识。")
    return value or None


def gateway_user(request: Request) -> str | None:
    ids = request.headers.getlist("X-User-Id")
    if len(ids) == 1 and re.fullmatch(r"[A-Za-z0-9@._:+|-]{1,128}", ids[0]):
        return ids[0]
    return None


def review_actor(request: Request, submitted: str) -> str:
    if not config.REQUIRE_REVIEW_ACTOR:
        return submitted
    user = gateway_user(request)
    if user is None:
        raise HTTPException(403, "复核操作需要由身份网关提供有效的用户标识。")
    return user


def project_grants(header: str | None) -> set[str] | None:
    """None denotes organization-wide scope. An absent grant never confers it in strict mode."""
    if not config.REQUIRE_PROJECT_GRANTS:
        return None
    value = (header or "").strip()
    if value == "*":
        return None
    ids = {part.strip() for part in value.split(",")}
    if not all(re.fullmatch(r"prj_[A-Za-z0-9_-]+", item) for item in ids):
        raise HTTPException(403, "项目访问需要有效的项目授权名单。")
    return ids


def require_project(grants: set[str] | None, project_id: str):
    if grants is not None and project_id not in grants:
        raise HTTPException(404, "Project resource not found")


def check_project_role(request: Request):
    if not config.REQUIRE_PROJECT_ROLE:
        return
    request_org(request.headers.get("X-Org-Id"))
    role = request.headers.get("X-Project-Role", "")
    if role == "editor" or (role == "reader" and request.method in {"GET", "HEAD"}):
        return
    raise HTTPException(403, "当前项目角色无权执行此操作。")


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
