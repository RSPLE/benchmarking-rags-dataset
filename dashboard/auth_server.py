from __future__ import annotations

import hmac
import html
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from dashboard.auth import authenticate, bootstrap_account
from dashboard.config import Settings
from dashboard.sessions import (
    COOKIE_NAME,
    create_session,
    csrf_token,
    revoke_session,
    session_identity,
)

CSRF_COOKIE = "rag_login_csrf"
TEMPLATE = Path(__file__).with_name("login.html").read_text()


def secure_request(request):
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def login_page(request, error="", status=200):
    nonce = secrets.token_urlsafe(32)
    content = TEMPLATE.replace("{{csrf}}", nonce).replace("{{error}}", html.escape(error))
    response = HTMLResponse(content, status_code=status)
    response.set_cookie(
        CSRF_COOKIE,
        nonce,
        max_age=600,
        httponly=True,
        secure=secure_request(request),
        samesite="strict",
    )
    return response


async def form_values(request):
    origin = request.headers.get("origin")
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    if origin and origin != f"{scheme}://{host}":
        raise ValueError("Invalid origin")
    if (
        request.headers.get("content-type", "").split(";", 1)[0]
        != "application/x-www-form-urlencoded"
    ):
        raise ValueError("Invalid form")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > 8192:
            raise ValueError("Form too large")
    values = parse_qs(body.decode(), max_num_fields=4, strict_parsing=True)
    if any(len(value) != 1 for value in values.values()):
        raise ValueError("Duplicate form field")
    return {key: value[0] for key, value in values.items()}


def create_app(settings=None):
    settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(
            bootstrap_account, settings.database, settings.username, settings.password
        )
        yield

    async def health(request):
        return PlainTextResponse("ok")

    async def login(request: Request):
        if request.method == "GET":
            if await run_in_threadpool(
                session_identity, settings.database, request.cookies.get(COOKIE_NAME)
            ):
                return RedirectResponse("/", status_code=303)
            return login_page(request)
        try:
            values = await form_values(request)
            provided, expected = values.get("csrf", ""), request.cookies.get(CSRF_COOKIE, "")
            if not expected or not hmac.compare_digest(provided, expected):
                raise ValueError("Invalid CSRF token")
        except (ValueError, UnicodeError):
            return login_page(request, "O formulário expirou. Tente novamente.", 403)
        identity = await run_in_threadpool(
            authenticate, settings.database, values.get("username", ""), values.get("password", "")
        )
        if identity is None:
            return login_page(
                request,
                "Não foi possível entrar. Confira os dados ou aguarde cinco minutos após várias tentativas.",
                401,
            )
        token = await run_in_threadpool(
            create_session, settings.database, identity, settings.session_seconds
        )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=settings.session_seconds,
            httponly=True,
            secure=secure_request(request),
            samesite="lax",
        )
        response.delete_cookie(
            CSRF_COOKIE, secure=secure_request(request), httponly=True, samesite="strict"
        )
        return response

    async def verify(request):
        identity = await run_in_threadpool(
            session_identity, settings.database, request.cookies.get(COOKIE_NAME)
        )
        if not identity:
            return RedirectResponse("/auth/login", status_code=303)
        return PlainTextResponse("ok")

    async def logout(request):
        token = request.cookies.get(COOKIE_NAME, "")
        try:
            values = await form_values(request)
            if not token or not hmac.compare_digest(values.get("csrf", ""), csrf_token(token)):
                raise ValueError("Invalid CSRF token")
        except (ValueError, UnicodeError):
            return PlainTextResponse("Solicitação inválida. Atualize a página.", status_code=403)
        await run_in_threadpool(revoke_session, settings.database, token)
        response = RedirectResponse("/auth/login", status_code=303)
        response.delete_cookie(
            COOKIE_NAME, secure=secure_request(request), httponly=True, samesite="lax"
        )
        return response

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/_health", health),
            Route("/auth/login", login, methods=["GET", "POST"]),
            Route("/auth/verify", verify),
            Route("/auth/logout", logout, methods=["POST"]),
        ],
    )

    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=headers)
    return app


if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, proxy_headers=False, access_log=False)
