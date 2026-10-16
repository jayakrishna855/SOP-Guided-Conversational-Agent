from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import STATIC_DIR, Settings, get_settings
from app.model.client import OpenAIModel
from app.services import build_services
from app.workflow.engine import Engine
from app.workflow.store import SessionStore

log = logging.getLogger("app")

INVALID_REQUEST = "That request wasn't valid. Please refresh the page and try again."
SERVER_ERROR = "Something went wrong on our side. Please try again."


def configure_logging() -> None:
    """Sends app.* logs (phase, actions, guard and grounding events, latency; never
    message text or identity values, §8) to stderr alongside uvicorn's."""
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = build_services(settings)
        app.state.services = services
        app.state.store = SessionStore(settings.session_ttl_minutes)
        app.state.engine = Engine(services, settings)
        # The server's own key is optional: without it, callers bring their own.
        app.state.server_model = (OpenAIModel(settings, settings.openai_api_key.get_secret_value())
                                  if settings.model_configured else None)
        log.info("started server_key=%s model=%s", settings.model_configured, settings.openai_model)
        yield

    app = FastAPI(title="Insurance SOP Agent (demo)", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.settings = settings

    # Safe error responses : a plain message, never the request body or internals.
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        log.warning("invalid_request path=%s", request.url.path)
        return JSONResponse(status_code=422, content={"detail": INVALID_REQUEST})

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("server_error path=%s type=%s", request.url.path, type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": SERVER_ERROR})

    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
