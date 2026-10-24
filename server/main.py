from __future__ import annotations

from fastapi import FastAPI

from server import __version__
from server.config import Settings, get_settings
from server.routes import sessions_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory for nightshift."""
    app = FastAPI(
        title="nightshift",
        version=__version__,
        description="session handoff server",
    )
    app.state.settings = settings if settings is not None else get_settings()
    app.include_router(sessions_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Health check; does not touch the filesystem."""
        return {
            "status": "ok",
            "service": "nightshift",
            "version": __version__,
        }

    return app


app = create_app()


def run() -> None:
    """Plain uvicorn launcher used by the `nightshift` console script."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )
