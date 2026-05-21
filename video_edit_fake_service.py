#!/usr/bin/env python3
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from video_edit_service import JobExecutionResult, JobRecord, VideoEditService


DEFAULT_FAKE_JOB_DELAY_SECONDS = 1.0


@dataclass(frozen=True)
class FakeMediaPaths:
    fixtures_root: Path
    input_video_path: Path
    reference_image_path: Path
    output_video_path: Path

    def sample_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "workspace_id": "demo",
            "video_path": str(self.input_video_path),
            "reference_image_path": str(self.reference_image_path),
            "prompt": "replace subject",
            "bbox": [10, 20, 30, 40],
            "output_name": "edited.mp4",
            "client_request_id": "req-demo-001",
        }
        payload.update(overrides)
        return payload


class FakeDaemonProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def prepare_fake_media(root: Path | str) -> FakeMediaPaths:
    fixtures_root = Path(root).resolve()
    fixtures_root.mkdir(parents=True, exist_ok=True)

    input_video_path = fixtures_root / "input.mp4"
    reference_image_path = fixtures_root / "reference.png"
    output_video_path = fixtures_root / "edited.mp4"

    _write_test_video(input_video_path)
    _write_test_image(reference_image_path)
    _write_test_video(output_video_path)

    return FakeMediaPaths(
        fixtures_root=fixtures_root,
        input_video_path=input_video_path,
        reference_image_path=reference_image_path,
        output_video_path=output_video_path,
    )


def create_fake_service(
    repo_root: Path | str,
    workspace_root: Path | str,
    fixtures: FakeMediaPaths,
    *,
    job_delay_seconds: float = DEFAULT_FAKE_JOB_DELAY_SECONDS,
) -> VideoEditService:
    repo_root = Path(repo_root).resolve()
    workspace_root = Path(workspace_root).resolve()

    def fake_popen(*args, **kwargs) -> FakeDaemonProcess:
        del args, kwargs
        return FakeDaemonProcess()

    def fake_probe(_socket_path: str) -> bool:
        return True

    def fake_execute(job: JobRecord) -> JobExecutionResult:
        if job_delay_seconds > 0:
            time.sleep(job_delay_seconds)

        job_root = workspace_root / job.job_id
        processed_dir = job_root / "processed"
        results_dir = job_root / "results"
        processed_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)

        output_name = str(job.input.get("output_name") or fixtures.output_video_path.name)
        output_video_path = results_dir / output_name
        src_video_path = processed_dir / "src_video.mp4"
        src_mask_path = processed_dir / "src_mask.mp4"
        src_ref_image_path = processed_dir / "src_ref_image_0.png"

        shutil.copyfile(fixtures.output_video_path, output_video_path)
        shutil.copyfile(fixtures.input_video_path, src_video_path)
        shutil.copyfile(fixtures.output_video_path, src_mask_path)
        shutil.copyfile(fixtures.reference_image_path, src_ref_image_path)

        return JobExecutionResult(
            output_dir=str(results_dir),
            out_video_path=str(output_video_path),
            output_video_path=str(output_video_path),
            src_video_path=str(src_video_path),
            src_mask_path=str(src_mask_path),
            src_ref_image_paths=[str(src_ref_image_path)],
        )

    return VideoEditService(
        repo_root=repo_root,
        workspace_root=workspace_root,
        daemon_popen_factory=fake_popen,
        socket_probe=fake_probe,
        job_executor=fake_execute,
        poll_interval_seconds=0.01,
        startup_timeout_seconds=1.0,
    )


def _write_test_video(path: Path, width: int = 64, height: int = 48) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 8.0, (width, height))
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :, 1] = 180
    for _ in range(3):
        writer.write(frame)
    writer.release()


def _write_test_image(path: Path, width: int = 64, height: int = 48) -> None:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 2] = 255
    cv2.imwrite(str(path), image)
