import shutil
import uuid

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from app.api import routes_compile, routes_v2
from app.api.exception_handlers import register_exception_handlers
from app.core.config import settings
from app.core.logging import setup_logging

# ---------------------------------------------------------------------------
# Logging — configure once at import time so all loggers inherit settings
# ---------------------------------------------------------------------------
setup_logging()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
)

# Register exception handlers for v2 error schema
register_exception_handlers(app)


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique ``X-Request-Id`` header to every response.

    If the incoming request already carries the header (e.g. from a load
    balancer), that value is reused.  Otherwise a new UUID-4 is generated.

    The ID is also stashed on ``request.state.request_id`` so downstream
    handlers can access it for structured logging.
    """

    async def dispatch(self, request: Request, call_next) -> StarletteResponse:  # type: ignore[override]
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


app.add_middleware(RequestIDMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# v1 routes (backward compatible)
app.include_router(routes_compile.router)

# v2 routes
app.include_router(routes_v2.router)


# ---------------------------------------------------------------------------
# Root & Health
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    return {"message": "Welcome to the LaTeX API!"}


_ENGINE_CANDIDATES = ["pdflatex", "xelatex", "lualatex"]


@app.get("/health")
async def health_check():
    engines = [e for e in _ENGINE_CANDIDATES if shutil.which(e) is not None]
    return {
        "status": "ok",
        "version": settings.VERSION,
        "engines": engines,
    }
