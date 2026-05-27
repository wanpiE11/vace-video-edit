import tempfile
import threading
import time
import unittest
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from video_edit_api import create_app
from video_edit_service import EnginePhase, JobExecutionResult, JobStatus, VideoEditService


class FakeDaemonProcess:
    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout=None) -> int:
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class VideoEditApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.video_path = self.root / "input.mp4"
        self.image_path = self.root / "ref.png"
        self.output_dir = self.root / "results"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_dir / "edited.mp4"
        self._write_test_video(self.video_path)
        self.image_path.write_bytes(b"image")
        self._write_test_video(self.output_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_test_video(self, path: Path, width: int = 64, height: int = 48) -> None:
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 8.0, (width, height))
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        for _ in range(3):
            writer.write(frame)
        writer.release()

    def _payload(self) -> dict[str, object]:
        return {
            "workspace_id": "my_project",
            "video_path": str(self.video_path.resolve()),
            "reference_image_path": str(self.image_path.resolve()),
            "prompt": "replace subject",
            "bbox": [10, 20, 30, 40],
            "output_name": "edited.mp4",
            "client_request_id": "req-1",
        }

    def _make_service(self, execute_delay: threading.Event | None = None) -> VideoEditService:
        ready_event = threading.Event()
        ready_event.set()

        def fake_popen(*args, **kwargs):
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        def fake_execute(job):
            if execute_delay is not None:
                execute_delay.wait(timeout=2.0)
            return JobExecutionResult(
                output_dir=str(self.output_dir),
                out_video_path=str(self.output_path),
                output_video_path=str(self.output_path),
                src_video_path=str(self.root / job.job_id / "processed" / "src_video.mp4"),
                src_mask_path=str(self.root / job.job_id / "processed" / "src_mask.mp4"),
                src_ref_image_paths=[str(self.root / job.job_id / "processed" / "src_ref_image_0.png")],
            )

        return VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=fake_execute,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )

    def _wait_for_status(self, service: VideoEditService, job_id: str, expected: str, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = service.get_job(job_id)
            if job is not None and job.status == expected:
                return job
            time.sleep(0.02)
        job = service.get_job(job_id)
        self.fail(f"job {job_id} did not reach status {expected}; last state={None if job is None else job.status}")

    def test_healthz_reports_engine_and_queue(self) -> None:
        block = threading.Event()
        service = self._make_service(execute_delay=block)
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                first = client.post("/api/v1/video-editing/jobs", json=self._payload())
                self.assertEqual(first.status_code, 202)
                second_payload = dict(self._payload())
                second_payload["client_request_id"] = "req-2"
                second_payload["output_name"] = "edited2.mp4"
                second = client.post("/api/v1/video-editing/jobs", json=second_payload)
                self.assertEqual(second.status_code, 202)

                job_id = first.json()["data"]["job_id"]
                self._wait_for_status(service, job_id, JobStatus.RUNNING)

                response = client.get("/healthz")
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertEqual(data["status"], "ok")
                self.assertEqual(data["engine_state"], "busy")
                self.assertEqual(data["engine"]["current_job_id"], job_id)
                self.assertEqual(data["queue"]["pending"], 1)
                self.assertEqual(data["engine"]["model_name"], "vace-14B")
                self.assertEqual(data["engine"]["device_mode"], "4gpu")
                self.assertIsNotNone(data["engine"]["started_at"])
                self.assertEqual(data["engine"]["phase"], "running_job")
                self.assertEqual(data["engine"]["progress"], 1.0)
        finally:
            block.set()
            service.close()

    def test_api_request_logging_writes_rotating_log_file(self) -> None:
        service = self._make_service()
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                response = client.get("/healthz")
                self.assertEqual(response.status_code, 200)
                self.assertIn("X-Request-ID", response.headers)

            log_path = self.root / "logs" / "video_edit_api.log"
            self.assertTrue(log_path.exists())
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("request started method=GET path=/healthz", log_text)
            self.assertIn("request completed method=GET path=/healthz status_code=200", log_text)
        finally:
            service.close()

    def test_engine_load_endpoint_is_idempotent(self) -> None:
        ready_event = threading.Event()

        def fake_popen(*args, **kwargs):
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=lambda _job: None,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                first = client.post("/api/v1/video-editing/engine/load")
                self.assertEqual(first.status_code, 202)
                self.assertEqual(first.json()["data"]["state"], "starting")
                self.assertEqual(first.json()["data"]["phase"], "spawning_daemon")
                self.assertEqual(first.json()["data"]["progress"], 0.2)
                self.assertEqual(first.json()["data"]["status_url"], "/api/v1/video-editing/engine")

                second = client.post("/api/v1/video-editing/engine/load")
                self.assertEqual(second.status_code, 200)
                self.assertEqual(second.json()["data"]["state"], "starting")

                ready_event.set()
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    engine = client.get("/api/v1/video-editing/engine").json()["data"]
                    if engine["state"] == "ready":
                        break
                    time.sleep(0.02)
                else:
                    self.fail("engine did not become ready")

                self.assertEqual(engine["phase"], "ready")
                self.assertEqual(engine["progress"], 1.0)
                self.assertIsNotNone(engine["ready_at"])
                self.assertEqual(engine["started_at"], engine["ready_at"])
        finally:
            service.close()

    def test_get_engine_returns_complete_snapshot(self) -> None:
        service = self._make_service()
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                response = client.get("/api/v1/video-editing/engine")
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertEqual(data["state"], "stopped")
                self.assertEqual(data["phase"], EnginePhase.NOT_INITIALIZED)
                self.assertEqual(data["progress"], 0.0)
                self.assertEqual(data["model_name"], "vace-14B")
                self.assertEqual(data["device_mode"], "4gpu")
                self.assertIsNone(data["current_job_id"])
                self.assertEqual(data["pending_jobs"], 0)
                self.assertIsNone(data["load_requested_at"])
                self.assertIsNone(data["ready_at"])
                self.assertIsNone(data["last_error"])
        finally:
            service.close()

    def test_create_job_supports_idempotency(self) -> None:
        service = self._make_service()
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                first = client.post("/api/v1/video-editing/jobs", json=self._payload())
                second = client.post("/api/v1/video-editing/jobs", json=self._payload())

                self.assertEqual(first.status_code, 202)
                self.assertEqual(second.status_code, 202)
                self.assertEqual(first.json()["data"]["job_id"], second.json()["data"]["job_id"])

                conflict_payload = dict(self._payload())
                conflict_payload["prompt"] = "different"
                conflict = client.post("/api/v1/video-editing/jobs", json=conflict_payload)
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(conflict.json()["error"]["code"], "INVALID_ARGUMENT")
        finally:
            service.close()

    def test_get_job_status_and_results(self) -> None:
        service = self._make_service()
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                created = client.post("/api/v1/video-editing/jobs", json=self._payload())
                job_id = created.json()["data"]["job_id"]
                self._wait_for_status(service, job_id, JobStatus.DONE)

                status_response = client.get(f"/api/v1/video-editing/jobs/{job_id}")
                self.assertEqual(status_response.status_code, 200)
                status_data = status_response.json()["data"]
                self.assertEqual(status_data["status"], "done")
                self.assertEqual(status_data["progress"], 1.0)
                self.assertIsNone(status_data["queue_position"])
                self.assertEqual(status_data["output"]["output_video_path"], str(self.output_path))
                self.assertEqual(
                    status_data["output"]["output_download_url"],
                    f"/api/v1/video-editing/jobs/{job_id}/output/download",
                )

                results_response = client.get(f"/api/v1/video-editing/jobs/{job_id}/results")
                self.assertEqual(results_response.status_code, 200)
                self.assertEqual(results_response.json()["data"]["status"], "done")
        finally:
            service.close()

    def test_results_endpoint_rejects_incomplete_job(self) -> None:
        block = threading.Event()
        service = self._make_service(execute_delay=block)
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                created = client.post("/api/v1/video-editing/jobs", json=self._payload())
                job_id = created.json()["data"]["job_id"]
                self._wait_for_status(service, job_id, JobStatus.RUNNING)

                response = client.get(f"/api/v1/video-editing/jobs/{job_id}/results")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], "JOB_NOT_COMPLETED")
        finally:
            block.set()
            service.close()

    def test_download_output_video(self) -> None:
        service = self._make_service()
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                created = client.post("/api/v1/video-editing/jobs", json=self._payload())
                job_id = created.json()["data"]["job_id"]
                self._wait_for_status(service, job_id, JobStatus.DONE)

                response = client.get(f"/api/v1/video-editing/jobs/{job_id}/output/download")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "video/mp4")
                self.assertGreater(len(response.content), 0)
        finally:
            service.close()

    def test_validation_errors_are_mapped(self) -> None:
        service = self._make_service()
        try:
            app = create_app(service=service)
            with TestClient(app) as client:
                invalid_path = dict(self._payload())
                invalid_path["video_path"] = "relative.mp4"
                response = client.post("/api/v1/video-editing/jobs", json=invalid_path)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "INVALID_ARGUMENT")

                missing = dict(self._payload())
                missing["video_path"] = str(self.root / "missing.mp4")
                response = client.post("/api/v1/video-editing/jobs", json=missing)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "FILE_NOT_FOUND")

                bad_bbox = dict(self._payload())
                bad_bbox["bbox"] = [0, 0, 1000, 1000]
                response = client.post("/api/v1/video-editing/jobs", json=bad_bbox)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "BBOX_OUT_OF_RANGE")

                bad_resolution = dict(self._payload())
                bad_resolution["resolution"] = "1080p"
                response = client.post("/api/v1/video-editing/jobs", json=bad_resolution)
                self.assertEqual(response.status_code, 422 if False else 400)
                self.assertEqual(response.json()["error"]["code"], "INVALID_ARGUMENT")
        finally:
            service.close()
