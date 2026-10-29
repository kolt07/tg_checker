import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, checks, sessions
from app.api.auth import close_pending_logins
from app.api.deps import require_api_key
from app.bootstrap import prepare_runtime
from app.db import get_engine
from app.logging_setup import setup_logging
from app.security import sanitize_validation_errors

logger = logging.getLogger("tgchecker.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    await prepare_runtime()
    yield
    await close_pending_logins()
    await get_engine().dispose()


app = FastAPI(title="Перевірка номерів Telegram", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
api.include_router(auth.router)
api.include_router(sessions.router)
api.include_router(checks.router)
app.include_router(api)


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": sanitize_validation_errors(exc.errors())})


@app.middleware("http")
async def hide_unexpected_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except (StarletteHTTPException, RequestValidationError):
        raise
    except Exception as exc:
        logger.info("unhandled %s %s", request.url.path, type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Внутрішня помилка"})
