"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router
from .config import Settings, get_settings
from .deps import lifespan

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Company Research Tool",
        description="Pre-meeting company briefings for sales reps.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    # Every error the frontend sees has the same shape: {"message": "..."}.
    # One code path in the client, and never a stack trace in front of a user.
    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"message": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"message": _first_message(exc)}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).exception("Unhandled error", exc_info=exc)
        return JSONResponse({"message": "Something went wrong on our side."}, status_code=500)

    app.include_router(router)
    return app


def _first_message(exc: RequestValidationError) -> str:
    for error in exc.errors():
        message = str(error.get("msg", ""))
        # Pydantic prefixes custom validator messages; the rep does not need that.
        return message.removeprefix("Value error, ") or "That input is not valid."
    return "That input is not valid."


app = create_app()
