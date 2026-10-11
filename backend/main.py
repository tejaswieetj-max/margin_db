import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from backend.auth import router as auth_router
from backend.routers.analyst import router as analyst_router
from backend.routers.engineer import router as engineer_router

load_dotenv()

REQUIRED_ENV_VARS = [
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "ADMIN_USER",
    "ADMIN_PASSWORD",
    "SVC_TELEMETRY_USER",
    "SVC_TELEMETRY_PASSWORD",
    "SEC_USER_AGENT",
    "JWT_SECRET",
]


def check_environment():
    """Verify all required environment variables are set."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    # Verify not running as root on Unix
    if hasattr(os, "getuid") and os.getuid() == 0:
        raise RuntimeError("Refusing to run backend application as root user")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup validation
    check_environment()
    yield
    # Teardown logic if needed


def create_app() -> FastAPI:
    """FastAPI application factory."""
    check_environment()

    app = FastAPI(
        title="Margin DB API",
        description="Database intelligence and financial margin decomposition API",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Router registrations
    app.include_router(auth_router)
    app.include_router(analyst_router)
    app.include_router(engineer_router)

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
