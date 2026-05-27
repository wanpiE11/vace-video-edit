#!/usr/bin/env python3
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from video_edit_service import (
    EngineStateSnapshot,
    JobOutput,
    JobRecord,
    JobStatus,
    VideoEditService,
    VideoEditServiceError,
    close_default_service,
    get_default_service,
)
from video_edit_logging import configure_logging, get_logger, log_context


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str
    reference_image_path: str
    prompt: str
    bbox: list[int]
    workspace_id: str = "default"
    output_name: str | None = None
    resolution: Literal["480p", "720p", "source"] = "720p"
    fps: int | None = None
    seed: int | None = None
    callback_url: str | None = None
    client_request_id: str | None = None


def create_app(service: VideoEditService | None = None) -> FastAPI:
    owns_service = service is None
    logger = get_logger("api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.video_edit_service = service or get_default_service()
        app.state.owns_service = owns_service
        configure_logging(app.state.video_edit_service.repo_root)
        logger.info("api lifespan started")
        try:
            yield
        finally:
            logger.info("api lifespan stopping")
            if app.state.owns_service:
                close_default_service()
            else:
                app.state.video_edit_service.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        job_id = _job_id_from_path(request.url.path)
        started = time.perf_counter()
        with log_context(request_id=request_id, job_id=job_id):
            logger.info(
                "request started method=%s path=%s client=%s",
                request.method,
                request.url.path,
                request.client.host if request.client else "-",
            )
            try:
                response = await call_next(request)
            except Exception:
                duration_ms = int((time.perf_counter() - started) * 1000)
                logger.exception(
                    "request failed method=%s path=%s duration_ms=%s",
                    request.method,
                    request.url.path,
                    duration_ms,
                )
                raise

            duration_ms = int((time.perf_counter() - started) * 1000)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request completed method=%s path=%s status_code=%s duration_ms=%s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        logger.warning(
            "api error method=%s path=%s status_code=%s code=%s message=%s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.code,
            exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(VideoEditServiceError)
    async def handle_service_error(request: Request, exc: VideoEditServiceError) -> JSONResponse:
        status_code = exc.status_code or _status_code_for_service_error(exc.code)
        logger.warning(
            "service error method=%s path=%s status_code=%s code=%s message=%s",
            request.method,
            request.url.path,
            status_code,
            exc.code,
            exc.message,
        )
        return JSONResponse(
            status_code=status_code,
            content={"ok": False, "error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(error.get("msg", "invalid request") for error in exc.errors()) or "invalid request"
        logger.warning(
            "request validation error method=%s path=%s message=%s",
            request.method,
            request.url.path,
            message,
        )
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": message}},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unexpected api error method=%s path=%s",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "internal server error"}},
        )

    @app.get("/healthz")
    async def healthz(edit_service: VideoEditService = Depends(_get_service)) -> dict[str, object]:
        snapshot = edit_service.get_engine_state()
        return {
            "ok": True,
            "data": {
                "status": "ok",
                "engine_state": snapshot.state,
                "engine": {
                    "model_name": snapshot.model_name,
                    "device_mode": snapshot.device_mode,
                    "current_job_id": snapshot.current_job_id,
                    "started_at": snapshot.started_at,
                    "phase": snapshot.phase,
                    "progress": snapshot.progress,
                },
                "queue": {
                    "pending": snapshot.pending_jobs,
                },
            },
        }

    @app.post("/api/v1/video-editing/engine/load")
    async def load_engine(
        response: Response,
        edit_service: VideoEditService = Depends(_get_service),
    ) -> dict[str, object]:
        result = edit_service.request_model_load()
        response.status_code = 202 if result.accepted else 200
        return {
            "ok": True,
            "data": {
                **_serialize_engine(result.snapshot),
                "status_url": _engine_status_url(),
            },
        }

    @app.get("/api/v1/video-editing/engine")
    async def get_engine(
        edit_service: VideoEditService = Depends(_get_service),
    ) -> dict[str, object]:
        return {"ok": True, "data": _serialize_engine(edit_service.get_engine_state())}

    @app.post("/api/v1/video-editing/jobs", status_code=202)
    async def create_job(
        payload: CreateJobRequest,
        edit_service: VideoEditService = Depends(_get_service),
    ) -> dict[str, object]:
        result = edit_service.submit_job(payload.model_dump(exclude_none=True))
        return {
            "ok": True,
            "data": {
                "job_id": result.job_id,
                "status": result.status,
                "queue_position": result.queue_position,
                "created_at": result.created_at,
                "status_url": _job_status_url(result.job_id),
                "results_url": _job_results_url(result.job_id),
                "output_download_url": _job_download_url(result.job_id),
            },
        }

    @app.get("/api/v1/video-editing/jobs/{job_id}")
    async def get_job_status(
        job_id: str,
        edit_service: VideoEditService = Depends(_get_service),
    ) -> dict[str, object]:
        job = edit_service.get_job(job_id)
        if job is None:
            raise ApiError(404, "JOB_NOT_FOUND", f"job not found: {job_id}")
        return {"ok": True, "data": _serialize_job(job, edit_service)}

    @app.get("/api/v1/video-editing/jobs/{job_id}/results")
    async def get_job_results(
        job_id: str,
        edit_service: VideoEditService = Depends(_get_service),
    ) -> dict[str, object]:
        job = edit_service.get_job(job_id)
        if job is None:
            raise ApiError(404, "JOB_NOT_FOUND", f"job not found: {job_id}")
        if job.status != JobStatus.DONE:
            raise ApiError(409, "JOB_NOT_COMPLETED", f"job status is {job.status}")
        return {
            "ok": True,
            "data": {
                "job_id": job.job_id,
                "status": job.status,
                "output": _serialize_output(job.job_id, job.output),
            },
        }

    @app.get("/api/v1/video-editing/jobs/{job_id}/output/download")
    async def download_output_video(
        job_id: str,
        edit_service: VideoEditService = Depends(_get_service),
    ) -> FileResponse:
        job = edit_service.get_job(job_id)
        if job is None:
            raise ApiError(404, "JOB_NOT_FOUND", f"job not found: {job_id}")
        output_path = job.output.output_video_path or job.output.out_video_path
        if not output_path:
            raise ApiError(404, "OUTPUT_NOT_AVAILABLE", "output video is not available")
        path = Path(output_path)
        if not path.exists():
            raise ApiError(404, "OUTPUT_NOT_AVAILABLE", "output video is not available")
        return FileResponse(path=path, media_type="video/mp4", filename=path.name)

    return app


def _get_service(request: Request) -> VideoEditService:
    return request.app.state.video_edit_service


def _serialize_job(job: JobRecord, service: VideoEditService) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "queue_position": service.get_queue_position(job.job_id),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "input": dict(job.input),
        "output": _serialize_output(job.job_id, job.output),
        "error": None if job.error is None else {"code": job.error.code, "message": job.error.message},
    }


def _serialize_engine(snapshot: EngineStateSnapshot) -> dict[str, object]:
    return snapshot.to_dict()


def _serialize_output(job_id: str, output: JobOutput) -> dict[str, object]:
    return {
        "output_dir": output.output_dir,
        "output_video_path": output.output_video_path or output.out_video_path,
        "src_video_path": output.src_video_path,
        "src_mask_path": output.src_mask_path,
        "src_ref_image_paths": output.src_ref_image_paths,
        "output_download_url": None if not (output.output_video_path or output.out_video_path) else _job_download_url(job_id),
    }


def _job_status_url(job_id: str) -> str:
    return f"/api/v1/video-editing/jobs/{job_id}"


def _job_results_url(job_id: str) -> str:
    return f"/api/v1/video-editing/jobs/{job_id}/results"


def _job_download_url(job_id: str) -> str:
    return f"/api/v1/video-editing/jobs/{job_id}/output/download"


def _engine_status_url() -> str:
    return "/api/v1/video-editing/engine"


def _job_id_from_path(path: str) -> str:
    marker = "/api/v1/video-editing/jobs/"
    if marker not in path:
        return "-"
    suffix = path.split(marker, 1)[1]
    job_id = suffix.split("/", 1)[0]
    return job_id or "-"


def _status_code_for_service_error(code: str) -> int:
    mapping = {
        "INVALID_ARGUMENT": 400,
        "BBOX_OUT_OF_RANGE": 400,
        "FILE_NOT_FOUND": 404,
        "UNSUPPORTED_MEDIA": 415,
    }
    return mapping.get(code, 500)


app = create_app()
