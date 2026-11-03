"""LegacyNexus AI — FastAPI application entry point."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, update

from app.config import get_settings
from app.database import SessionLocal, init_all
from app.models import Project, User
from app.routers import analysis, auth, doc_qa, projects, qa, users
from app.routers import settings as settings_router
from app.security import hash_password
from app.services import env_config
from app.services.platform import deployment_report

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("legacynexus")


def _bootstrap() -> None:
    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    init_all()  # Document Q&A database, then the main database
    with SessionLocal() as db:
        for item in env_config.apply(db):
            log.info("Configured %s from the environment", item)
        if db.scalar(select(func.count(User.id))) == 0:
            if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
                db.add(User(email=settings.bootstrap_admin_email.strip().lower(), full_name=settings.bootstrap_admin_name,
                            password_hash=hash_password(settings.bootstrap_admin_password), role="admin", preferences={}))
                db.commit()
                log.info("Created bootstrap administrator %s", settings.bootstrap_admin_email)
            else:
                log.warning("No users exist. Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD to create one.")
        # Analyses interrupted by a restart cannot resume; surface them so users can re-run.
        db.execute(update(Project).where(Project.status == "analyzing")
                   .values(status="failed", stage="Interrupted", error="The server restarted during analysis. Run it again."))
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _bootstrap()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan,
                  docs_url=f"{settings.api_prefix}/docs", openapi_url=f"{settings.api_prefix}/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    for r in (auth.router, projects.router, analysis.router, qa.router, doc_qa.router, settings_router.router,
              users.router):
        app.include_router(r, prefix=settings.api_prefix)

    @app.get(f"{settings.api_prefix}/health", tags=["system"])
    def health() -> dict:
        """Liveness: the process is up. In development it also names the demo account (never the password)."""
        demo = settings.bootstrap_admin_email if settings.environment == "development" else None
        return {"status": "ok", "app": settings.app_name, "environment": settings.environment,
                "profile": settings.deployment_profile, "demo_login": demo}

    @app.get(f"{settings.api_prefix}/health/ready", tags=["system"])
    def ready(response: Response) -> dict:
        """Readiness: every backing service this deployment uses, and whether it answers."""
        report = deployment_report()
        if not all(c["ok"] for c in report["components"].values()):
            response.status_code = 503
        return report

    # Optional: serve the built frontend from the same origin (single-container deployments).
    dist = os.getenv("FRONTEND_DIST")
    if dist and Path(dist).is_dir():
        dist_path = Path(dist).resolve()
        app.mount("/assets", StaticFiles(directory=dist_path / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            candidate = (dist_path / full_path).resolve()
            if full_path and dist_path in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist_path / "index.html")

    return app


app = create_app()
