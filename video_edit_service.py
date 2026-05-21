#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import cv2


DEFAULT_SOCKET_PATH = "/tmp/vace_wan_infer.sock"
DEFAULT_MODEL_NAME = "vace-14B"
DEFAULT_CKPT_DIR = "/root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B"
DEFAULT_REPO_ROOT = Path("/root/data/gzn/vace-video-edit")
DEFAULT_CUDA_VISIBLE_DEVICES = "0,1,2,3"
DEFAULT_RESOLUTION = "720p"
DEFAULT_TASK = "swap_anything"
DEFAULT_MODE = "bboxtrack,salient"
DEFAULT_OUTPUT_NAME = "out_video.mp4"
DEFAULT_WORKSPACE_ID = "default"
DEVICE_MODE = "4gpu"
ENGINE_START_TIMEOUT_SECONDS = 900.0
ENGINE_POLL_INTERVAL_SECONDS = 1.0
ALLOWED_RESOLUTIONS = {"480p", "720p", "source"}
PROGRESS_QUEUED = 0.0
PROGRESS_RUNNING = 0.5
PROGRESS_FINISHED = 1.0


class EngineState:
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    FAILED = "failed"


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class VideoEditServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class JobError:
    code: str
    message: str
    traceback: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class JobOutput:
    output_dir: str | None = None
    out_video_path: str | None = None
    output_video_path: str | None = None
    src_video_path: str | None = None
    src_mask_path: str | None = None
    src_ref_image_paths: list[str] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class JobRecord:
    job_id: str
    status: str
    progress: float
    created_at: str
    engine_state_at_submit: str
    input: dict[str, object]
    output: JobOutput = field(default_factory=JobOutput)
    error: JobError | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at,
            "engine_state_at_submit": self.engine_state_at_submit,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "input": dict(self.input),
            "output": self.output.to_dict(),
            "error": None if self.error is None else self.error.to_dict(),
        }


@dataclass
class SubmitJobResult:
    job_id: str
    status: str
    engine_state: str
    created_at: str
    queue_position: int | None
    deduplicated: bool = False


@dataclass
class EngineStateSnapshot:
    state: str
    current_job_id: str | None
    pending_jobs: int
    last_error: JobError | None
    started_at: str | None
    model_name: str
    device_mode: str

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "current_job_id": self.current_job_id,
            "pending_jobs": self.pending_jobs,
            "last_error": None if self.last_error is None else self.last_error.to_dict(),
            "started_at": self.started_at,
            "model_name": self.model_name,
            "device_mode": self.device_mode,
        }


@dataclass
class JobExecutionResult:
    output_dir: str
    out_video_path: str
    output_video_path: str
    src_video_path: str | None = None
    src_mask_path: str | None = None
    src_ref_image_paths: list[str] | None = None


class VideoEditService:
    def __init__(
        self,
        repo_root: Path | str = DEFAULT_REPO_ROOT,
        socket_path: str = DEFAULT_SOCKET_PATH,
        model_name: str = DEFAULT_MODEL_NAME,
        ckpt_dir: str = DEFAULT_CKPT_DIR,
        workspace_root: Path | str | None = None,
        startup_timeout_seconds: float = ENGINE_START_TIMEOUT_SECONDS,
        poll_interval_seconds: float = ENGINE_POLL_INTERVAL_SECONDS,
        daemon_popen_factory: Callable[..., subprocess.Popen] | None = None,
        socket_probe: Callable[[str], bool] | None = None,
        job_executor: Callable[[JobRecord], JobExecutionResult] | None = None,
        video_info_getter: Callable[[str], tuple[int, int]] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else self.repo_root / "workspace" / "jobs"
        self.socket_path = socket_path
        self.model_name = model_name
        self.ckpt_dir = ckpt_dir
        self.startup_timeout_seconds = startup_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.daemon_popen_factory = daemon_popen_factory or subprocess.Popen
        self.socket_probe = socket_probe or self._probe_socket
        self.job_executor = job_executor or self._execute_job
        self.video_info_getter = video_info_getter or self._get_video_dimensions

        self._jobs: dict[str, JobRecord] = {}
        self._job_request_signatures: dict[str, str] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._pending_job_ids: list[str] = []
        self._condition = threading.Condition()
        self._engine_state = EngineState.STOPPED
        self._engine_error: JobError | None = None
        self._engine_started_at: str | None = None
        self._current_job_id: str | None = None
        self._daemon_process: subprocess.Popen | None = None
        self._daemon_log_handle = None
        self._closed = False

        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._worker_thread = threading.Thread(target=self._worker_loop, name="video-edit-worker", daemon=True)
        self._worker_thread.start()

    def submit_job(self, payload: dict[str, object]) -> SubmitJobResult:
        normalized_input = self._validate_payload(payload)
        request_signature = self._build_request_signature(normalized_input)
        client_request_id = normalized_input.get("client_request_id")

        with self._condition:
            self._ensure_open()
            if isinstance(client_request_id, str) and client_request_id:
                existing_job_id = self._job_request_signatures.get(client_request_id)
                if existing_job_id is not None:
                    existing_job = self._jobs[existing_job_id]
                    if self._build_request_signature(existing_job.input) != request_signature:
                        raise VideoEditServiceError(
                            "INVALID_ARGUMENT",
                            "client_request_id is already bound to a different request.",
                            status_code=409,
                        )
                    return SubmitJobResult(
                        job_id=existing_job.job_id,
                        status=existing_job.status,
                        engine_state=self._engine_state,
                        created_at=existing_job.created_at,
                        queue_position=self._get_queue_position_unlocked(existing_job.job_id),
                        deduplicated=True,
                    )

            job_id = self._new_job_id()
            job = JobRecord(
                job_id=job_id,
                status=JobStatus.QUEUED,
                progress=PROGRESS_QUEUED,
                created_at=self._utc_now(),
                engine_state_at_submit=self._engine_state,
                input=normalized_input,
            )
            self._jobs[job_id] = job
            self._pending_job_ids.append(job_id)
            if isinstance(client_request_id, str) and client_request_id:
                self._job_request_signatures[client_request_id] = job_id

        self._ensure_engine_started()
        self._queue.put(job_id)

        with self._condition:
            engine_state = self._engine_state
            queue_position = self._get_queue_position_unlocked(job_id)
        return SubmitJobResult(
            job_id=job_id,
            status=JobStatus.QUEUED,
            engine_state=engine_state,
            created_at=job.created_at,
            queue_position=queue_position,
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return self._copy_job(job)

    def get_queue_position(self, job_id: str) -> int | None:
        with self._condition:
            if job_id not in self._jobs:
                return None
            return self._get_queue_position_unlocked(job_id)

    def get_engine_state(self) -> EngineStateSnapshot:
        with self._condition:
            return EngineStateSnapshot(
                state=self._engine_state,
                current_job_id=self._current_job_id,
                pending_jobs=len(self._pending_job_ids),
                last_error=self._engine_error,
                started_at=self._engine_started_at,
                model_name=self.model_name,
                device_mode=DEVICE_MODE,
            )

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        self._queue.put(None)
        self._worker_thread.join(timeout=5.0)
        self._stop_daemon_process()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("VideoEditService is closed.")

    def _ensure_engine_started(self) -> None:
        spawn_daemon = False
        with self._condition:
            if self._engine_state in {EngineState.STARTING, EngineState.READY, EngineState.BUSY}:
                return
            self._engine_state = EngineState.STARTING
            self._engine_error = None
            self._condition.notify_all()
            spawn_daemon = True

        if not spawn_daemon:
            return

        try:
            process, log_handle = self._spawn_daemon_process()
        except Exception as exc:
            self._set_engine_failed(
                code="ENGINE_START_FAILED",
                message=f"Failed to spawn resident daemon: {exc}",
                trace=traceback.format_exc(),
            )
            return

        with self._condition:
            self._daemon_process = process
            self._daemon_log_handle = log_handle
            self._condition.notify_all()

        watcher = threading.Thread(target=self._watch_engine_startup, name="video-edit-engine-startup", daemon=True)
        watcher.start()

    def _spawn_daemon_process(self) -> tuple[subprocess.Popen, object]:
        logs_dir = self.repo_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "video_edit_engine.log"
        log_handle = log_path.open("a", encoding="utf-8")

        cmd = [
            sys.executable,
            str((self.repo_root / "run_edit_video_server.py").resolve()),
            "--socket-path",
            self.socket_path,
            "--model_name",
            self.model_name,
            "--ckpt_dir",
            self.ckpt_dir,
            "--nproc-per-node",
            "4",
            "--ulysses_size",
            "4",
            "--ring_size",
            "1",
        ]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = self.daemon_popen_factory(
            cmd,
            cwd=str(self.repo_root),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return process, log_handle

    def _watch_engine_startup(self) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            with self._condition:
                if self._closed:
                    return
                process = self._daemon_process
                state = self._engine_state

            if process is None or state != EngineState.STARTING:
                return

            return_code = process.poll()
            if return_code is not None:
                self._set_engine_failed(
                    code="ENGINE_START_FAILED",
                    message=f"Resident daemon exited before ready with code {return_code}.",
                    trace="Resident daemon terminated during startup.",
                )
                return

            if self.socket_probe(self.socket_path):
                with self._condition:
                    if self._engine_state == EngineState.STARTING:
                        self._engine_state = EngineState.READY
                        self._engine_started_at = self._utc_now()
                        self._condition.notify_all()
                return

            time.sleep(self.poll_interval_seconds)

        self._set_engine_failed(
            code="ENGINE_START_FAILED",
            message=f"Resident daemon did not become ready within {self.startup_timeout_seconds:.0f} seconds.",
            trace="Socket probe timed out while waiting for resident daemon readiness.",
        )

    def _set_engine_failed(self, code: str, message: str, trace: str) -> None:
        with self._condition:
            self._engine_state = EngineState.FAILED
            self._engine_error = JobError(code=code, message=message, traceback=trace)
            self._condition.notify_all()
        self._stop_daemon_process()

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return

            job = self._get_job_ref(job_id)
            if job is None:
                continue

            if not self._wait_until_engine_ready(job):
                continue

            self._mark_job_running(job)
            try:
                result = self.job_executor(job)
            except Exception as exc:
                self._mark_job_failed(
                    job,
                    code="ENGINE_EXECUTION_FAILED",
                    message=str(exc) or "Resident job execution failed.",
                    trace=traceback.format_exc(),
                )
            else:
                self._mark_job_done(job, result)
            finally:
                with self._condition:
                    self._current_job_id = None
                    process_alive = self._daemon_process is not None and self._daemon_process.poll() is None
                    self._engine_state = EngineState.READY if process_alive else EngineState.FAILED
                    if not process_alive and self._engine_error is None:
                        self._engine_error = JobError(
                            code="ENGINE_NOT_READY",
                            message="Resident daemon is no longer running.",
                            traceback="Resident daemon exited after job execution.",
                        )
                    self._condition.notify_all()

    def _wait_until_engine_ready(self, job: JobRecord) -> bool:
        while True:
            with self._condition:
                state = self._engine_state
                engine_error = self._engine_error

            if state == EngineState.READY:
                return True
            if state == EngineState.BUSY:
                time.sleep(self.poll_interval_seconds)
                continue
            if state == EngineState.STARTING:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._engine_state != EngineState.STARTING or self._closed,
                        timeout=self.poll_interval_seconds,
                    )
                continue
            if state == EngineState.STOPPED:
                self._ensure_engine_started()
                continue

            error = engine_error or JobError(
                code="ENGINE_NOT_READY",
                message="Resident daemon is not ready.",
                traceback="Engine entered failed state before job execution.",
            )
            self._mark_job_failed(job, error.code, error.message, error.traceback)
            return False

    def _mark_job_running(self, job: JobRecord) -> None:
        with self._condition:
            job.status = JobStatus.RUNNING
            job.progress = PROGRESS_RUNNING
            job.started_at = self._utc_now()
            self._remove_pending_job_unlocked(job.job_id)
            self._current_job_id = job.job_id
            self._engine_state = EngineState.BUSY
            self._condition.notify_all()

    def _mark_job_done(self, job: JobRecord, result: JobExecutionResult) -> None:
        with self._condition:
            job.status = JobStatus.DONE
            job.progress = PROGRESS_FINISHED
            job.finished_at = self._utc_now()
            job.output = JobOutput(
                output_dir=result.output_dir,
                out_video_path=result.out_video_path,
                output_video_path=result.output_video_path,
                src_video_path=result.src_video_path,
                src_mask_path=result.src_mask_path,
                src_ref_image_paths=result.src_ref_image_paths,
            )
            job.error = None
            self._condition.notify_all()

    def _mark_job_failed(self, job: JobRecord, code: str, message: str, trace: str) -> None:
        with self._condition:
            job.status = JobStatus.FAILED
            job.progress = PROGRESS_FINISHED
            job.finished_at = self._utc_now()
            if job.started_at is None:
                job.started_at = self._utc_now()
            self._remove_pending_job_unlocked(job.job_id)
            job.error = JobError(code=code, message=message, traceback=trace)
            self._condition.notify_all()

    def _get_job_ref(self, job_id: str) -> JobRecord | None:
        with self._condition:
            return self._jobs.get(job_id)

    def _execute_job(self, job: JobRecord) -> JobExecutionResult:
        job_dir = self.workspace_root / job.job_id
        processed_dir = job_dir / "processed"
        results_dir = job_dir / "results"
        processed_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)

        output_name = str(job.input.get("output_name") or DEFAULT_OUTPUT_NAME)
        save_file = results_dir / output_name

        cmd = [
            sys.executable,
            str((self.repo_root / "run_edit_video.py").resolve()),
            "--server-socket",
            self.socket_path,
            "--nproc-per-node",
            "4",
            "--cuda-visible-devices",
            DEFAULT_CUDA_VISIBLE_DEVICES,
            "--dit_fsdp",
            "--t5_fsdp",
            "--task",
            DEFAULT_TASK,
            "--video",
            str(job.input["video_path"]),
            "--image",
            str(job.input["reference_image_path"]),
            "--mode",
            DEFAULT_MODE,
            "--bbox",
            self._format_bbox(job.input["bbox"]),
            "--size",
            str(job.input.get("resolution", DEFAULT_RESOLUTION)),
            "--model_name",
            self.model_name,
            "--ckpt_dir",
            self.ckpt_dir,
            "--prompt",
            str(job.input["prompt"]),
            "--pre_save_dir",
            str(processed_dir),
            "--save_dir",
            str(results_dir),
            "--save_file",
            str(save_file),
        ]

        seed = job.input.get("seed")
        if seed is not None:
            cmd.extend(["--base_seed", str(seed)])
        fps = job.input.get("fps")
        if fps is not None:
            cmd.extend(["--save_fps", str(fps)])

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        completed = subprocess.run(
            cmd,
            cwd=str(self.repo_root),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(self._build_process_failure_message(completed))

        parsed_result = self._parse_resident_result(completed.stdout)
        out_video_path = parsed_result.get("out_video")
        if not isinstance(out_video_path, str) or not out_video_path:
            raise RuntimeError("Resident service result did not include out_video.")

        ref_images = self._collect_ref_images(parsed_result)
        return JobExecutionResult(
            output_dir=str(results_dir),
            out_video_path=out_video_path,
            output_video_path=out_video_path,
            src_video_path=self._optional_str(parsed_result.get("src_video")),
            src_mask_path=self._optional_str(parsed_result.get("src_mask")),
            src_ref_image_paths=ref_images or None,
        )

    def _build_process_failure_message(self, completed: subprocess.CompletedProcess) -> str:
        parts = [f"run_edit_video.py exited with code {completed.returncode}."]
        if completed.stdout:
            parts.append(f"stdout: {completed.stdout.strip()[-4000:]}")
        if completed.stderr:
            parts.append(f"stderr: {completed.stderr.strip()[-4000:]}")
        return "\n".join(parts)

    def _parse_resident_result(self, stdout: str) -> dict[str, object]:
        for line in reversed(stdout.splitlines()):
            if "Resident service result:" not in line:
                continue
            payload = line.split("Resident service result:", 1)[1].strip()
            parsed = ast.literal_eval(payload)
            if not isinstance(parsed, dict):
                raise RuntimeError("Resident service result was not a mapping.")
            return parsed
        raise RuntimeError("run_edit_video.py did not print a resident service result.")

    def _collect_ref_images(self, parsed_result: dict[str, object]) -> list[str]:
        refs: list[tuple[int, str]] = []
        for key, value in parsed_result.items():
            if not key.startswith("src_ref_image_") or not isinstance(value, str):
                continue
            try:
                index = int(key.split("_")[-1])
            except ValueError:
                continue
            refs.append((index, value))
        refs.sort(key=lambda item: item[0])
        return [path for _, path in refs]

    def _probe_socket(self, socket_path: str) -> bool:
        path = Path(socket_path)
        if not path.exists():
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            try:
                client.connect(socket_path)
            except OSError:
                return False
        return True

    def _stop_daemon_process(self) -> None:
        process = self._daemon_process
        log_handle = self._daemon_log_handle
        self._daemon_process = None
        self._daemon_log_handle = None

        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)

        if log_handle is not None:
            log_handle.close()

    def _validate_payload(self, payload: dict[str, object]) -> dict[str, object]:
        normalized: dict[str, object] = {}
        video_path = self._require_path(payload.get("video_path"), "video_path")
        reference_image_path = self._require_path(payload.get("reference_image_path"), "reference_image_path")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise VideoEditServiceError("INVALID_ARGUMENT", "prompt must be a non-empty string.")

        bbox = self._validate_bbox(payload.get("bbox"), video_path)

        normalized["video_path"] = video_path
        normalized["reference_image_path"] = reference_image_path
        normalized["prompt"] = prompt.strip()
        normalized["bbox"] = bbox

        workspace_id = payload.get("workspace_id", DEFAULT_WORKSPACE_ID)
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise VideoEditServiceError("INVALID_ARGUMENT", "workspace_id must be a non-empty string.")
        normalized["workspace_id"] = workspace_id.strip()

        resolution = payload.get("resolution") or DEFAULT_RESOLUTION
        if not isinstance(resolution, str) or resolution not in ALLOWED_RESOLUTIONS:
            raise VideoEditServiceError("INVALID_ARGUMENT", "resolution must be one of 480p, 720p, source.")
        normalized["resolution"] = resolution

        seed = payload.get("seed")
        if seed is not None:
            if not isinstance(seed, int):
                raise VideoEditServiceError("INVALID_ARGUMENT", "seed must be an integer.")
            normalized["seed"] = seed

        fps = payload.get("fps")
        if fps is not None:
            if not isinstance(fps, int) or fps <= 0:
                raise VideoEditServiceError("INVALID_ARGUMENT", "fps must be a positive integer.")
            normalized["fps"] = fps

        output_name = payload.get("output_name") or DEFAULT_OUTPUT_NAME
        if not isinstance(output_name, str):
            raise VideoEditServiceError("INVALID_ARGUMENT", "output_name must be a string.")
        if not output_name.endswith(".mp4"):
            raise VideoEditServiceError("INVALID_ARGUMENT", "output_name must end with .mp4.")
        if "/" in output_name or "\\" in output_name or ".." in output_name:
            raise VideoEditServiceError("INVALID_ARGUMENT", "output_name must be a plain filename.")
        normalized["output_name"] = output_name

        callback_url = payload.get("callback_url")
        if callback_url is not None:
            if not isinstance(callback_url, str) or not callback_url.strip():
                raise VideoEditServiceError("INVALID_ARGUMENT", "callback_url must be a non-empty string.")
            normalized["callback_url"] = callback_url.strip()

        client_request_id = payload.get("client_request_id")
        if client_request_id is not None:
            if not isinstance(client_request_id, str) or not client_request_id.strip():
                raise VideoEditServiceError("INVALID_ARGUMENT", "client_request_id must be a non-empty string.")
            normalized["client_request_id"] = client_request_id.strip()

        return normalized

    def _require_path(self, value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value:
            raise VideoEditServiceError("INVALID_ARGUMENT", f"{field_name} must be a non-empty string.")
        path = Path(value)
        if not path.is_absolute():
            raise VideoEditServiceError("INVALID_ARGUMENT", f"{field_name} must be an absolute path.")
        if not path.exists():
            raise VideoEditServiceError("FILE_NOT_FOUND", f"{field_name} does not exist: {value}")
        return str(path)

    def _validate_bbox(self, value: object, video_path: str) -> list[int]:
        if not isinstance(value, list) or len(value) != 4:
            raise VideoEditServiceError("INVALID_ARGUMENT", "bbox must be a list of four integers.")
        if any(not isinstance(item, int) for item in value):
            raise VideoEditServiceError("INVALID_ARGUMENT", "bbox must contain integers only.")
        x1, y1, x2, y2 = value
        if x1 >= x2 or y1 >= y2:
            raise VideoEditServiceError("INVALID_ARGUMENT", "bbox must satisfy x1 < x2 and y1 < y2.")
        if min(value) < 0:
            raise VideoEditServiceError("INVALID_ARGUMENT", "bbox values must be non-negative.")

        width, height = self.video_info_getter(video_path)
        if x2 > width or y2 > height:
            raise VideoEditServiceError(
                "BBOX_OUT_OF_RANGE",
                f"bbox exceeds video frame bounds {width}x{height}.",
            )
        return [x1, y1, x2, y2]

    def _get_video_dimensions(self, video_path: str) -> tuple[int, int]:
        capture = cv2.VideoCapture(video_path)
        try:
            if not capture.isOpened():
                raise VideoEditServiceError("UNSUPPORTED_MEDIA", f"Unable to open video: {video_path}")

            ok, frame = capture.read()
            if ok and frame is not None:
                height, width = frame.shape[:2]
            else:
                width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

            if width <= 0 or height <= 0:
                raise VideoEditServiceError("UNSUPPORTED_MEDIA", f"Unable to read video dimensions: {video_path}")
            return width, height
        finally:
            capture.release()

    def _copy_job(self, job: JobRecord) -> JobRecord:
        return JobRecord(
            job_id=job.job_id,
            status=job.status,
            progress=job.progress,
            created_at=job.created_at,
            engine_state_at_submit=job.engine_state_at_submit,
            input=dict(job.input),
            output=JobOutput(**job.output.to_dict()),
            error=None if job.error is None else JobError(**job.error.to_dict()),
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    def _build_request_signature(self, payload: dict[str, object]) -> str:
        comparable = dict(payload)
        comparable.pop("client_request_id", None)
        return json.dumps(comparable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def _get_queue_position_unlocked(self, job_id: str) -> int | None:
        if self._current_job_id == job_id:
            return 0
        try:
            return self._pending_job_ids.index(job_id) + 1
        except ValueError:
            return None

    def _remove_pending_job_unlocked(self, job_id: str) -> None:
        try:
            self._pending_job_ids.remove(job_id)
        except ValueError:
            return

    def _new_job_id(self) -> str:
        return f"edit_job_{uuid.uuid4().hex[:16]}"

    def _utc_now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _format_bbox(self, bbox: object) -> str:
        if not isinstance(bbox, list):
            raise ValueError("bbox must be a list.")
        return ",".join(str(item) for item in bbox)

    def _optional_str(self, value: object) -> str | None:
        return value if isinstance(value, str) and value else None


_default_service_lock = threading.Lock()
_default_service: VideoEditService | None = None


def get_default_service() -> VideoEditService:
    global _default_service
    with _default_service_lock:
        if _default_service is None:
            _default_service = VideoEditService()
        return _default_service


def submit_job(payload: dict[str, object]) -> SubmitJobResult:
    return get_default_service().submit_job(payload)


def get_job(job_id: str) -> JobRecord | None:
    return get_default_service().get_job(job_id)


def get_engine_state() -> EngineStateSnapshot:
    return get_default_service().get_engine_state()


def close_default_service() -> None:
    global _default_service
    with _default_service_lock:
        if _default_service is not None:
            _default_service.close()
            _default_service = None
