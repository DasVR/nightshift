from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel

from server import __version__
from server.config import Settings, get_settings
from server.notify import router as notify_router
from server.routes import sessions_router
from server.runs import ClaudeTmuxRunner, RunRecord, RunStatus, RunStore, RunWorker
from server.sessions import InvalidSessionId, SessionNotFound, find_session


class RunCreate(BaseModel):
    """Request body to enqueue a headless resume run."""

    session_id: str
    instruction: str
    cwd: str | None = None


def create_app(
    settings: Settings | None = None,
    runner: object | None = None,
) -> FastAPI:
    """Application factory for nightshift."""
    settings_obj = settings if settings is not None else get_settings()
    settings_obj.ensure_dirs()

    run_store = RunStore(settings_obj.runs_dir)
    run_runner = runner if runner is not None else ClaudeTmuxRunner(settings_obj)
    run_worker = RunWorker(run_store, run_runner, settings_obj)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run_worker.start()
        yield
        run_worker.stop()

    app = FastAPI(
        title="nightshift",
        version=__version__,
        description="session handoff server",
        lifespan=lifespan,
    )
    app.state.settings = settings_obj
    app.state.run_store = run_store
    app.state.run_runner = run_runner
    app.state.run_worker = run_worker
    app.include_router(notify_router)
    app.include_router(sessions_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Health check; does not touch the filesystem."""
        return {
            "status": "ok",
            "service": "nightshift",
            "version": __version__,
        }

    @app.post("/runs", status_code=status.HTTP_201_CREATED)
    def create_run(body: RunCreate) -> RunRecord:
        """Queue a headless resume run for a known Claude session."""
        if not body.instruction.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="instruction is required",
            )
        try:
            session = find_session(body.session_id, settings_obj.claude_projects_dir)
        except InvalidSessionId as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except SessionNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

        cwd = body.cwd if body.cwd else session.cwd
        record = run_store.create(
            session_id=body.session_id,
            instruction=body.instruction,
            cwd=cwd,
            project_dir=str(session.project_dir),
        )
        return record

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> RunRecord:
        """Fetch a single run record by id."""
        record = run_store.get(run_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="run not found",
            )
        return record

    @app.get("/runs")
    def list_runs(status_filter: str | None = Query(None, alias="status")) -> dict[str, list[RunRecord]]:
        """List runs, newest first. Optional ?status=queued filter."""
        if status_filter is not None and status_filter not in {s.value for s in RunStatus}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid status: {status_filter}",
            )
        return {"runs": run_store.list(status=status_filter)}

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
