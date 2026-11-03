from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile
from sqlalchemy import select

from app.config import get_settings
from app.deps import DB, CurrentUser, ProjectDep, WriterUser
from app.models import Project, SourceFile
from app.schemas import ProjectCreate, ProjectOut
from app.services import analysis_runner, artifacts, audit, jobs, semantic, storage
from app.services.project_data import purge_project_rows

router = APIRouter(prefix="/projects", tags=["projects"])

SAMPLE_NAME = "banking-core"


@router.get("", response_model=list[ProjectOut])
def list_projects(user: CurrentUser, db: DB) -> list[Project]:
    q = select(Project).order_by(Project.updated_at.desc())
    if user.role != "admin":
        q = q.where(Project.owner_id == user.id)
    return list(db.scalars(q))


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, user: WriterUser, db: DB, request: Request) -> Project:
    project = Project(name=body.name.strip(), description=body.description.strip(), owner_id=user.id, stats={})
    db.add(project)
    db.commit()
    audit.record(db, "project_created", user.id, f"project:{project.id}", {"name": project.name}, request)
    return project


@router.post("/sample", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_sample(user: WriterUser, db: DB, background: BackgroundTasks, request: Request) -> Project:
    source = get_settings().sample_data_dir / SAMPLE_NAME
    if not source.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The sample application is not installed on this server")
    project = Project(name="Core Banking (sample)", owner_id=user.id, stats={},
                      description="Sample COBOL/DB2 banking application: customers, accounts, interest, payments and Report 12.")
    db.add(project)
    db.commit()
    storage.copy_tree(source, project.id)
    _queue_analysis(project, db, background)
    audit.record(db, "sample_loaded", user.id, f"project:{project.id}", request=request)
    return project


def _queue_analysis(project: Project, db, background: BackgroundTasks) -> None:
    """Mark the project as queued and hand the analysis to the job backend (in-process or Service Bus)."""
    project.status, project.progress, project.stage, project.error = "analyzing", 0, "Queued", None
    db.commit()
    try:
        jobs.submit(jobs.ANALYZE, project.id, background)
    except jobs.JobSubmitError as exc:
        project.status, project.stage, project.error = "failed", "Could not queue the analysis", str(exc)
        db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: ProjectDep) -> Project:
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project: ProjectDep, user: WriterUser, db: DB, request: Request) -> None:
    if analysis_runner.is_busy(project) or semantic.is_building(project.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the running analysis or indexing to finish before deleting")
    pid = project.id
    semantic.delete_index(db, pid)
    purge_project_rows(db, pid)  # SQLite ignores ON DELETE CASCADE and reuses ids
    db.delete(project)
    db.commit()
    storage.delete_project_files(pid)
    artifacts.invalidate(pid)
    audit.record(db, "project_deleted", user.id, f"project:{pid}", request=request)


_UPLOAD_SCHEMA = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["files"],
    "properties": {"files": {"type": "array", "items": {"type": "string", "format": "binary"}}}}}}}}


@router.post("/{project_id}/files", response_model=ProjectOut, openapi_extra=_UPLOAD_SCHEMA)
async def upload_files(project: ProjectDep, user: WriterUser, db: DB, request: Request) -> Project:
    settings = get_settings()
    # The form is parsed here rather than with File(...): FastAPI's default parser stops at 1,000 files,
    # which a whole-folder upload of a real repository easily exceeds.
    per_request = settings.max_files_per_request or float("inf")
    form = await request.form(max_files=per_request, max_fields=per_request)
    try:
        files = [v for v in form.getlist("files") if isinstance(v, StarletteUploadFile)]
        if not files:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose one or more files or a .zip to upload")
        budget = settings.mb_limit(settings.max_upload_mb)
        result = storage.StoreResult()
        total = 0
        for f in files:
            try:
                staged, size = await storage.stream_to_staging(project.id, f, None if budget is None else budget - total)
                total += size
                result.merge(await run_in_threadpool(storage.store_staged, project.id, f.filename or "upload.bin", staged))
            except storage.UploadTooLarge as exc:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc))
            except storage.UploadRejected as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    finally:
        await form.close()
    if not result.stored:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"No analysable source files found ({result.skipped} file(s) skipped). "
                            "Upload COBOL, copybooks, JCL, SQL, Java, C#, Python, JavaScript/TypeScript, "
                            "shell scripts, YAML config, OpenAPI specs or notes.")
    project.status = "uploaded"
    project.stage = f"{len(result.stored)} source file(s) uploaded"
    if result.skipped:
        project.stage += f", {result.skipped} non-source file(s) skipped"
    db.commit()
    audit.record(db, "files_uploaded", user.id, f"project:{project.id}",
                 {"files": len(result.stored), "skipped": result.skipped, "bytes": total}, request)
    return project


@router.post("/{project_id}/analyze", response_model=ProjectOut, status_code=status.HTTP_202_ACCEPTED)
def analyze(project: ProjectDep, user: WriterUser, db: DB, background: BackgroundTasks, request: Request) -> Project:
    if analysis_runner.is_busy(project):
        raise HTTPException(status.HTTP_409_CONFLICT, "Analysis is already running")
    if not any(storage.project_dir(project.id).rglob("*")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload source files before running the analysis")
    _queue_analysis(project, db, background)
    audit.record(db, "analysis_started", user.id, f"project:{project.id}", request=request)
    return project


@router.get("/{project_id}/files")
def list_files(project: ProjectDep, db: DB) -> list[dict]:
    rows = db.scalars(select(SourceFile).where(SourceFile.project_id == project.id).order_by(SourceFile.path)).all()
    if rows:
        return [{"path": r.path, "language": r.language, "size_bytes": r.size_bytes, "line_count": r.line_count} for r in rows]
    root = storage.project_dir(project.id)
    return [{"path": p.relative_to(root).as_posix(), "language": None, "size_bytes": p.stat().st_size, "line_count": None}
            for p in sorted(root.rglob("*")) if p.is_file()]


@router.get("/{project_id}/files/content")
def file_content(project: ProjectDep, path: str = Query(..., max_length=1024)) -> dict:
    try:
        lines = storage.read_source_lines(project.id, path)
    except (FileNotFoundError, storage.UploadRejected):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return {"path": path, "lines": lines, "line_count": len(lines)}
