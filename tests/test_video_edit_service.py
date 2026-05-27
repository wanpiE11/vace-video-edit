import tempfile
import threading
import time
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from video_edit_service import (
    EnginePhase,
    EngineState,
    JobExecutionResult,
    JobRecord,
    JobStatus,
    VideoEditService,
    VideoEditServiceError,
)


class FakeDaemonProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False
        self.pid = 12345

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None) -> int:
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class VideoEditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.video_path = self.root / "input.mp4"
        self.image_path = self.root / "ref.png"
        self._write_test_video(self.video_path)
        self.image_path.write_bytes(b"image")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _payload(self) -> dict[str, object]:
        return {
            "video_path": str(self.video_path.resolve()),
            "reference_image_path": str(self.image_path.resolve()),
            "prompt": "replace subject",
            "bbox": [10, 20, 30, 40],
            "output_name": "edited.mp4",
        }

    def _write_test_video(self, path: Path, width: int = 64, height: int = 48) -> None:
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 8.0, (width, height))
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        for _ in range(3):
            writer.write(frame)
        writer.release()

    def _wait_for_status(self, service: VideoEditService, job_id: str, expected: str, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = service.get_job(job_id)
            if job is not None and job.status == expected:
                return job
            time.sleep(0.02)
        job = service.get_job(job_id)
        self.fail(f"job {job_id} did not reach status {expected}; last state={None if job is None else job.status}")

    def _wait_for_engine_state(self, service: VideoEditService, expected: str, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = service.get_engine_state()
            if snapshot.state == expected:
                return snapshot
            time.sleep(0.02)
        snapshot = service.get_engine_state()
        self.fail(f"engine did not reach state {expected}; last state={snapshot.state}")

    def _wait_for_engine_phase(self, service: VideoEditService, expected: str, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = service.get_engine_state()
            if snapshot.phase == expected:
                return snapshot
            time.sleep(0.02)
        snapshot = service.get_engine_state()
        self.fail(f"engine did not reach phase {expected}; last phase={snapshot.phase}")

    def test_request_model_load_is_idempotent_until_ready(self) -> None:
        spawn_count = 0
        ready_event = threading.Event()

        def fake_popen(*args, **kwargs):
            nonlocal spawn_count
            spawn_count += 1
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
            first = service.request_model_load()
            second = service.request_model_load()

            self.assertTrue(first.accepted)
            self.assertFalse(second.accepted)
            self.assertEqual(spawn_count, 1)

            snapshot = service.get_engine_state()
            self.assertEqual(snapshot.state, EngineState.STARTING)
            self.assertEqual(snapshot.phase, EnginePhase.SPAWNING_DAEMON)
            self.assertEqual(snapshot.progress, 0.2)
            self.assertIsNotNone(snapshot.load_requested_at)
            self.assertIsNone(snapshot.ready_at)

            ready_event.set()
            ready_snapshot = self._wait_for_engine_state(service, EngineState.READY)
            self.assertEqual(ready_snapshot.phase, EnginePhase.READY)
            self.assertEqual(ready_snapshot.progress, 1.0)
            self.assertIsNotNone(ready_snapshot.ready_at)
            self.assertEqual(ready_snapshot.started_at, ready_snapshot.ready_at)
        finally:
            service.close()

    def test_log_observer_updates_model_initialized_phase(self) -> None:
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
            service.request_model_load()
            log_path = self.root / "logs" / "video_edit_engine.log"
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write("Inference model is initialized\n")
                log_file.flush()

            initialized_snapshot = self._wait_for_engine_phase(service, EnginePhase.MODEL_INITIALIZED)
            self.assertEqual(initialized_snapshot.progress, 0.8)
            self.assertEqual(initialized_snapshot.state, EngineState.STARTING)

            ready_event.set()
            ready_snapshot = self._wait_for_engine_state(service, EngineState.READY)
            self.assertEqual(ready_snapshot.phase, EnginePhase.READY)
            self.assertEqual(ready_snapshot.progress, 1.0)
        finally:
            service.close()

    def test_failed_load_can_be_retried(self) -> None:
        spawn_count = 0
        ready_event = threading.Event()

        def fake_popen(*args, **kwargs):
            nonlocal spawn_count
            spawn_count += 1
            process = FakeDaemonProcess()
            if spawn_count == 1:
                process.returncode = 3
            return process

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=lambda _socket_path: ready_event.is_set(),
            job_executor=lambda _job: None,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            first = service.request_model_load()
            failed_snapshot = self._wait_for_engine_state(service, EngineState.FAILED)

            self.assertTrue(first.accepted)
            self.assertEqual(failed_snapshot.phase, EnginePhase.FAILED)
            self.assertIsNotNone(failed_snapshot.last_error)
            self.assertEqual(spawn_count, 1)

            second = service.request_model_load()
            self.assertTrue(second.accepted)
            self.assertEqual(spawn_count, 2)

            retry_snapshot = self._wait_for_engine_phase(service, EnginePhase.SPAWNING_DAEMON)
            self.assertEqual(retry_snapshot.state, EngineState.STARTING)
            self.assertEqual(retry_snapshot.progress, 0.2)

            ready_event.set()
            ready_snapshot = self._wait_for_engine_state(service, EngineState.READY)
            self.assertEqual(ready_snapshot.phase, EnginePhase.READY)
        finally:
            service.close()

    def test_first_job_starts_engine_and_second_reuses_it(self) -> None:
        spawn_count = 0
        ready_event = threading.Event()
        executed_jobs: list[str] = []

        def fake_popen(*args, **kwargs):
            nonlocal spawn_count
            spawn_count += 1
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        def fake_execute(job):
            executed_jobs.append(job.job_id)
            out_dir = str(self.root / job.job_id / "results")
            out_path = str(self.root / job.job_id / "results" / "edited.mp4")
            return JobExecutionResult(output_dir=out_dir, out_video_path=out_path, output_video_path=out_path)

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=fake_execute,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            ready_event.set()
            first = service.submit_job(self._payload())
            first_job = self._wait_for_status(service, first.job_id, JobStatus.DONE)

            second = service.submit_job(self._payload())
            second_job = self._wait_for_status(service, second.job_id, JobStatus.DONE)

            self.assertEqual(spawn_count, 1)
            self.assertEqual(executed_jobs, [first.job_id, second.job_id])
            self.assertEqual(first_job.progress, 1.0)
            self.assertTrue(first_job.output.out_video_path.endswith("edited.mp4"))
            self.assertTrue(second_job.output.out_video_path.endswith("edited.mp4"))
        finally:
            service.close()

    def test_second_submit_during_starting_waits_without_spawning_second_daemon(self) -> None:
        spawn_count = 0
        ready_event = threading.Event()
        execute_started = threading.Event()
        executed_jobs: list[str] = []

        def fake_popen(*args, **kwargs):
            nonlocal spawn_count
            spawn_count += 1
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        def fake_execute(job):
            execute_started.set()
            executed_jobs.append(job.job_id)
            out_dir = str(self.root / job.job_id / "results")
            out_path = str(self.root / job.job_id / "results" / "edited.mp4")
            return JobExecutionResult(output_dir=out_dir, out_video_path=out_path, output_video_path=out_path)

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=fake_execute,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            first = service.submit_job(self._payload())
            second = service.submit_job(self._payload())

            time.sleep(0.05)
            self.assertEqual(spawn_count, 1)
            self.assertEqual(service.get_engine_state().state, EngineState.STARTING)
            self.assertFalse(execute_started.is_set())
            self.assertEqual(service.get_job(first.job_id).status, JobStatus.QUEUED)
            self.assertEqual(service.get_job(second.job_id).status, JobStatus.QUEUED)

            ready_event.set()

            self._wait_for_status(service, first.job_id, JobStatus.DONE)
            self._wait_for_status(service, second.job_id, JobStatus.DONE)

            self.assertEqual(spawn_count, 1)
            self.assertEqual(executed_jobs, [first.job_id, second.job_id])
        finally:
            service.close()

    def test_completed_job_exposes_out_video_path(self) -> None:
        ready_event = threading.Event()

        def fake_popen(*args, **kwargs):
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        def fake_execute(job):
            out_dir = str(self.root / job.job_id / "results")
            out_path = str(self.root / job.job_id / "results" / "edited.mp4")
            return JobExecutionResult(
                output_dir=out_dir,
                out_video_path=out_path,
                output_video_path=out_path,
                src_video_path=str(self.root / job.job_id / "processed" / "src_video.mp4"),
            )

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=fake_execute,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            ready_event.set()
            result = service.submit_job(self._payload())
            job = self._wait_for_status(service, result.job_id, JobStatus.DONE)
            self.assertIsNotNone(job.output.out_video_path)
            self.assertEqual(job.output.out_video_path, job.output.output_video_path)
            self.assertTrue(job.output.out_video_path.endswith("edited.mp4"))
            job_log = self.root / "jobs" / result.job_id / "logs" / "job.log"
            self.assertTrue(job_log.exists())
            log_text = job_log.read_text(encoding="utf-8")
            self.assertIn("Job started.", log_text)
            self.assertIn("Job completed. output_video_path=", log_text)
        finally:
            service.close()

    def test_failed_job_persists_error_status_and_message(self) -> None:
        ready_event = threading.Event()

        def fake_popen(*args, **kwargs):
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        def fake_execute(job):
            raise RuntimeError("intentional failure")

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=fake_execute,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            ready_event.set()
            result = service.submit_job(self._payload())
            job = self._wait_for_status(service, result.job_id, JobStatus.FAILED)
            self.assertEqual(job.status, JobStatus.FAILED)
            self.assertIsNotNone(job.error)
            self.assertEqual(job.error.code, "ENGINE_EXECUTION_FAILED")
            self.assertEqual(job.progress, 1.0)
            self.assertIn("intentional failure", job.error.message)
            self.assertIn("RuntimeError", job.error.traceback)
            job_log = self.root / "jobs" / result.job_id / "logs" / "job.log"
            self.assertTrue(job_log.exists())
            log_text = job_log.read_text(encoding="utf-8")
            self.assertIn("Job started.", log_text)
            self.assertIn("Job failed. code=ENGINE_EXECUTION_FAILED", log_text)
            self.assertIn("intentional failure", log_text)
        finally:
            service.close()

    def test_client_request_id_returns_existing_job_for_same_payload(self) -> None:
        ready_event = threading.Event()
        executed_jobs: list[str] = []

        def fake_popen(*args, **kwargs):
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        def fake_execute(job):
            executed_jobs.append(job.job_id)
            out_dir = str(self.root / job.job_id / "results")
            out_path = str(self.root / job.job_id / "results" / "edited.mp4")
            return JobExecutionResult(output_dir=out_dir, out_video_path=out_path, output_video_path=out_path)

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=fake_execute,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            ready_event.set()
            payload = self._payload() | {"client_request_id": "req-1"}
            first = service.submit_job(payload)
            second = service.submit_job(payload)

            self.assertEqual(first.job_id, second.job_id)
            self.assertTrue(second.deduplicated)

            self._wait_for_status(service, first.job_id, JobStatus.DONE)
            self.assertEqual(executed_jobs, [first.job_id])
        finally:
            service.close()

    def test_client_request_id_rejects_different_payload(self) -> None:
        ready_event = threading.Event()

        def fake_popen(*args, **kwargs):
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        def fake_execute(job):
            out_dir = str(self.root / job.job_id / "results")
            out_path = str(self.root / job.job_id / "results" / "edited.mp4")
            return JobExecutionResult(output_dir=out_dir, out_video_path=out_path, output_video_path=out_path)

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=fake_execute,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            ready_event.set()
            payload = self._payload() | {"client_request_id": "req-1"}
            service.submit_job(payload)
            with self.assertRaises(VideoEditServiceError) as ctx:
                service.submit_job(payload | {"prompt": "different"})
            self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")
        finally:
            service.close()

    def test_queue_position_and_engine_metadata_are_exposed(self) -> None:
        spawn_count = 0
        ready_event = threading.Event()
        release_execution = threading.Event()

        def fake_popen(*args, **kwargs):
            nonlocal spawn_count
            spawn_count += 1
            return FakeDaemonProcess()

        def fake_probe(_socket_path: str) -> bool:
            return ready_event.is_set()

        def fake_execute(job):
            release_execution.wait(timeout=2.0)
            out_dir = str(self.root / job.job_id / "results")
            out_path = str(self.root / job.job_id / "results" / "edited.mp4")
            return JobExecutionResult(output_dir=out_dir, out_video_path=out_path, output_video_path=out_path)

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=fake_probe,
            job_executor=fake_execute,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            ready_event.set()
            first = service.submit_job(self._payload())
            second = service.submit_job(self._payload() | {"output_name": "edited2.mp4"})
            first_running = self._wait_for_status(service, first.job_id, JobStatus.RUNNING)

            self.assertEqual(first_running.progress, 0.5)
            self.assertEqual(service.get_queue_position(first.job_id), 0)
            self.assertEqual(service.get_queue_position(second.job_id), 1)

            engine_state = service.get_engine_state()
            self.assertEqual(engine_state.state, EngineState.BUSY)
            self.assertEqual(engine_state.current_job_id, first.job_id)
            self.assertEqual(engine_state.pending_jobs, 1)
            self.assertEqual(engine_state.model_name, "vace-14B")
            self.assertEqual(engine_state.device_mode, "4gpu")
            self.assertIsNotNone(engine_state.started_at)
            self.assertEqual(spawn_count, 1)

            release_execution.set()
            self._wait_for_status(service, first.job_id, JobStatus.DONE)
            self._wait_for_status(service, second.job_id, JobStatus.DONE)
        finally:
            service.close()

    def test_bbox_out_of_range_is_rejected(self) -> None:
        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=lambda *args, **kwargs: FakeDaemonProcess(),
            socket_probe=lambda _socket_path: True,
            job_executor=lambda job: JobExecutionResult(
                output_dir=str(self.root),
                out_video_path=str(self.root / "edited.mp4"),
                output_video_path=str(self.root / "edited.mp4"),
            ),
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            with self.assertRaises(VideoEditServiceError) as ctx:
                service.submit_job(self._payload() | {"bbox": [0, 0, 1000, 1000]})
            self.assertEqual(ctx.exception.code, "BBOX_OUT_OF_RANGE")
        finally:
            service.close()

    def test_execute_job_writes_process_output_to_job_log(self) -> None:
        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=lambda *args, **kwargs: FakeDaemonProcess(),
            socket_probe=lambda _socket_path: True,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=1.0,
        )
        try:
            job = JobRecord(
                job_id="edit_job_logtest",
                status=JobStatus.RUNNING,
                progress=0.5,
                created_at=service._utc_now(),
                engine_state_at_submit=EngineState.READY,
                input=self._payload(),
            )
            stdout = (
                "preprocess ok\n"
                "Resident service result: {'out_video': '/tmp/out.mp4', 'src_video': '/tmp/src.mp4'}\n"
            )
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="runtime warning\n")

            with mock.patch("video_edit_service.subprocess.run", return_value=completed):
                result = service._execute_job(job)

            self.assertEqual(result.out_video_path, "/tmp/out.mp4")
            job_log = self.root / "jobs" / job.job_id / "logs" / "job.log"
            log_text = job_log.read_text(encoding="utf-8")
            self.assertIn("Running command:", log_text)
            self.assertIn("run_edit_video.py exited with code 0.", log_text)
            self.assertIn("preprocess ok", log_text)
            self.assertIn("runtime warning", log_text)
        finally:
            service.close()

    def test_close_stops_daemon_process_group(self) -> None:
        process = FakeDaemonProcess()
        popen_kwargs: dict[str, object] = {}

        def fake_popen(*args, **kwargs):
            popen_kwargs.update(kwargs)
            return process

        service = VideoEditService(
            repo_root=self.root,
            workspace_root=self.root / "jobs",
            daemon_popen_factory=fake_popen,
            socket_probe=lambda _socket_path: False,
            job_executor=lambda _job: None,
            poll_interval_seconds=0.01,
            startup_timeout_seconds=10.0,
        )
        try:
            with mock.patch("video_edit_service.os.getpgid", return_value=54321), mock.patch(
                "video_edit_service.os.killpg"
            ) as killpg:
                service.request_model_load()
                service.close()

            self.assertTrue(popen_kwargs["start_new_session"])
            killpg.assert_called_once()
            sig = killpg.call_args.args[1]
            self.assertEqual(sig, __import__("signal").SIGTERM)
        finally:
            if not service._closed:
                service.close()


if __name__ == "__main__":
    unittest.main()
