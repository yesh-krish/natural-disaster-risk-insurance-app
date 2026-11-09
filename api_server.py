"""Standalone HTTP API. Reuses v1 calculations without exposing project files."""

import logging
import os
from threading import BoundedSemaphore

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from api_spec import openapi_document
from api_v1 import dispatch, error_response

logger = logging.getLogger(__name__)


def create_app(*, allowed_origins=None, max_lookups=4):
    if max_lookups < 1:
        raise ValueError("max_lookups must be positive")
    origins = allowed_origins if allowed_origins is not None else [
        value.strip() for value in os.environ.get("NDAPP_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    ]
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
        for path in spec["paths"].values():
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
                           allow_methods=["GET"], allow_headers=["Accept"],
                           expose_headers=["Retry-After"], allow_credentials=False)
    return app


app = create_app()


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    uvicorn.run(app, host=args.host, port=args.port)
