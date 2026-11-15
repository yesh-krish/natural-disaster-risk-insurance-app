"""Standalone HTTP API. Reuses v1 calculations without exposing project files.

Sharing with another machine: set NDAPP_API_KEYS (comma-separated; make one with
`python api_server.py --new-key`), then run `python api_server.py --host 0.0.0.0`.
Clients send the key in the X-API-Key header. Health and docs stay public.
"""

import hmac
import ipaddress
import logging
import os
import secrets
from threading import BoundedSemaphore

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from api_spec import openapi_document
from api_v1 import dispatch, error_response

logger = logging.getLogger(__name__)


MIN_KEY_LENGTH = 20


def configured_keys(raw=None):
    """API keys from NDAPP_API_KEYS. Short keys are rejected so a typo cannot open the API."""
    raw = os.environ.get("NDAPP_API_KEYS", "") if raw is None else raw
    keys = [value.strip() for value in raw.split(",") if value.strip()]
    if any(len(key) < MIN_KEY_LENGTH for key in keys):
        raise ValueError(f"Each API key must be at least {MIN_KEY_LENGTH} characters; use --new-key.")
    return keys


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_app(*, allowed_origins=None, max_lookups=4, api_keys=None):
    if max_lookups < 1:
        raise ValueError("max_lookups must be positive")
    origins = allowed_origins if allowed_origins is not None else [
        value.strip() for value in os.environ.get("NDAPP_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    ]
    keys = configured_keys() if api_keys is None else configured_keys(",".join(api_keys))

    def authorized(request):
        if not keys:
            return True
        supplied = request.headers.get("x-api-key", "")
        # Compare against every key in constant time so timing does not reveal a partial match.
        return any(hmac.compare_digest(supplied.encode(), key.encode()) for key in keys) if supplied else False
    app = FastAPI(docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/v1/openapi.json")
    slots = BoundedSemaphore(max_lookups)

    def schema():
        spec = openapi_document()
        spec["info"]["description"] = (
            "NDApp research API. Frequencies are percentages, not fractions. "
            "Check status before using data: missing coverage is never zero risk. "
            "Sources, units and scientific assumptions are retained in each result. "
            "This service does not issue binding quotes or payouts."
        )
        if keys:
            spec["components"]["securitySchemes"] = {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
            spec["info"]["description"] += " Data requests need an X-API-Key header; /api/v1/health is public."
        for route, path in spec["paths"].items():
            if keys and route != "/api/v1/health":
                path["get"]["security"] = [{"ApiKey": []}]
                path["get"]["responses"]["401"] = {
                    "description": "Missing or invalid X-API-Key header.",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
            path["get"]["responses"]["503"] = {
                "description": "All lookup slots are busy; retry later.",
                "headers": {"Retry-After": {"schema": {"type": "integer"}}},
                "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Error"}}},
            }
        return spec

    app.openapi = schema

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/api/docs")

    @app.api_route("/api/v1", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], include_in_schema=False)
    @app.api_route("/api/v1/{resource:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], include_in_schema=False)
    def lookup(request: Request, resource: str = ""):
        headers = {}
        if request.method != "GET":
            code, payload = error_response(405, "method_not_allowed", "Use GET requests.")
            headers["Allow"] = "GET"
        elif resource == "health":
            code, payload = dispatch("/api/v1/health", request.url.query)
        elif not authorized(request):
            code, payload = error_response(401, "unauthorized", "Send a valid API key in the X-API-Key header.")
            headers["WWW-Authenticate"] = "ApiKey"
        elif not slots.acquire(blocking=False):
            code, payload = error_response(503, "service_busy", "All lookup slots are busy. Retry later.")
            headers["Retry-After"] = "2"
        else:
            try:
                code, payload = dispatch("/api/v1/" + resource, request.url.query)
            except Exception:
                logger.exception("Unexpected API lookup failure")
                code, payload = error_response(500, "internal_error", "The lookup could not complete. Retry later.")
            finally:
                slots.release()
        return JSONResponse(payload, status_code=code, headers=headers)

    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins,
                           allow_methods=["GET"], allow_headers=["Accept", "X-API-Key"],
                           expose_headers=["Retry-After"], allow_credentials=False)
    return app


app = create_app()


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--new-key", action="store_true", help="Print a new random API key and exit.")
    args = parser.parse_args()
    if args.new_key:
        print(secrets.token_urlsafe(32))
        raise SystemExit(0)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        keys = configured_keys()
    except ValueError as exc:
        parser.error(str(exc))
    if not is_loopback(args.host) and not keys and os.environ.get("NDAPP_ALLOW_OPEN") != "1":
        parser.error("Refusing to listen on the network without API keys. Set NDAPP_API_KEYS "
                     "(see --new-key), or NDAPP_ALLOW_OPEN=1 behind a host that controls access.")
    print(f"NDApp API on {args.host}:{args.port} · API keys {'required' if keys else 'not required'} · docs at /api/docs",
          flush=True)
    uvicorn.run(app, host=args.host, port=args.port)
