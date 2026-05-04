"""ASGI middleware: external collection ids <-> internal ``core_rag_`` prefix."""

from __future__ import annotations

import json
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.datastructures import MutableHeaders

from graph.utils.collection_id_scope import (
    apply_inbound_collection_ids,
    apply_outbound_collection_ids,
    to_internal_collection_id,
)


def _rewrite_collections_path(scope: dict) -> None:
    """Rewrite ``/api/collections/{id}`` path segment to internal id (GET/DELETE)."""
    method = str(scope.get("method", "GET")).upper()
    path = scope.get("path") or ""
    if method == "POST" and path == "/api/collections/get-or-create":
        return
    prefix = "/api/collections/"
    if not path.startswith(prefix):
        return
    rest = path[len(prefix) :]
    if not rest or "/" in rest:
        return
    internal = to_internal_collection_id(rest)
    if internal == rest:
        return
    new_path = prefix + internal
    scope["path"] = new_path
    scope["raw_path"] = new_path.encode("utf-8")


class CollectionIdMiddleware(BaseHTTPMiddleware):
    """Inbound: JSON ``collection_id`` + path param under ``/api/collections/``.
    Outbound: JSON responses strip internal prefix from ``collection_id`` values.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        scope = dict(request.scope)
        _rewrite_collections_path(scope)

        body = await request.body()
        content_type = request.headers.get("content-type", "")
        if body and "application/json" in content_type.lower():
            try:
                data = json.loads(body.decode("utf-8"))
                apply_inbound_collection_ids(data)
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(scope, receive)
        response = await call_next(request)

        ct = response.headers.get("content-type", "")
        if "application/json" not in ct.lower():
            return response

        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk

        try:
            payload = json.loads(resp_body.decode("utf-8"))
            apply_outbound_collection_ids(payload)
            new_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError):
            new_body = resp_body

        headers = MutableHeaders(response.headers)
        if "content-length" in headers:
            del headers["content-length"]

        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=dict(headers),
            media_type="application/json",
        )
