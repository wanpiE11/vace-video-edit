#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from video_edit_api import create_app
from video_edit_fake_service import create_fake_service, prepare_fake_media


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the video edit HTTP API with a fake no-GPU backend.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8880, help="Bind port. Default: 8880")
    parser.add_argument(
        "--state-root",
        default="workspace/fake_api_server",
        help="Directory used for fake fixtures and job outputs. Default: workspace/fake_api_server",
    )
    parser.add_argument(
        "--job-delay-seconds",
        type=float,
        default=1.0,
        help="Delay each fake job so queued/running/result-409 flows are observable. Default: 1.0",
    )
    parser.add_argument("--log-level", default="info", help="Uvicorn log level. Default: info")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    state_root = (repo_root / args.state_root).resolve()
    fixtures = prepare_fake_media(state_root / "fixtures")
    service = create_fake_service(
        repo_root=repo_root,
        workspace_root=state_root / "jobs",
        fixtures=fixtures,
        job_delay_seconds=args.job_delay_seconds,
    )

    print(f"Starting fake video edit API on http://{args.host}:{args.port}")
    print(f"State root: {state_root}")
    print("Sample request payload:")
    print(json.dumps(fixtures.sample_payload(), ensure_ascii=False, indent=2))

    app = create_app(service=service)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
