#!/usr/bin/env python3
import argparse
import contextlib
import importlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import torch


DEFAULT_VIDEO = "/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4"
DEFAULT_PROMPT = "镜头中任务改成男人其他不变"
DEFAULT_MODEL_NAME = "vace-14B"
DEFAULT_CKPT_DIR = "/root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B"


@contextlib.contextmanager
def pushd(path: Path):
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


def repo_dir_from_args(repo_dir: str | None) -> Path:
    script_dir = Path(__file__).resolve().parent
    return Path(repo_dir) if repo_dir else script_dir / "repos" / "VACE"


def load_parser(module_name: str) -> argparse.ArgumentParser:
    module = importlib.import_module(module_name)
    if not hasattr(module, "get_parser"):
        raise ValueError(f"{module_name} does not define get_parser()")
    return module.get_parser()


def filter_args(args: dict[str, object], parser: argparse.ArgumentParser) -> dict[str, object]:
    known_args = set()
    for action in parser._actions:
        if action.dest and action.dest != "help":
            known_args.add(action.dest)
    return {key: value for key, value in args.items() if key in known_args}


def add_module_actions(target: argparse.ArgumentParser, parser: argparse.ArgumentParser) -> None:
    existing = {option for action in target._actions for option in action.option_strings}
    for action in parser._actions:
        if action.dest == "help":
            continue
        if any(option in existing for option in action.option_strings):
            continue
        target._add_action(action)
        existing.update(action.option_strings)


def build_parser(repo_dir: Path) -> argparse.ArgumentParser:
    vace_dir = repo_dir / "vace"
    if not vace_dir.exists():
        raise FileNotFoundError(f"VACE module directory not found: {vace_dir}")

    if str(vace_dir) not in sys.path:
        sys.path.insert(0, str(vace_dir))

    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--base", default="wan", choices=["wan", "ltx"], help="Inference backend.")
    bootstrap.add_argument("--repo-dir", default=str(repo_dir), help="Path to the VACE repo root.")
    bootstrap.add_argument(
        "--nproc-per-node",
        type=int,
        default=1,
        help="Number of local processes for distributed inference. Set to 2 for dual GPU.",
    )
    bootstrap.add_argument(
        "--cuda-visible-devices",
        default=None,
        help="Optional CUDA device list, for example `0,1`.",
    )
    bootstrap.add_argument(
        "--server-socket",
        default=None,
        help="Optional UNIX socket path for a resident Wan inference service. When set, preprocessing runs locally and inference is dispatched to the resident service instead of launching torchrun.",
    )

    base_args, _ = bootstrap.parse_known_args()
    preproccess_name = "vace_preproccess"
    inference_name = "vace_ltx_inference" if base_args.base == "ltx" else "vace_wan_inference"

    preprocess_parser = load_parser(preproccess_name)
    inference_parser = load_parser(inference_name)

    parser = argparse.ArgumentParser(
        description="Run VACE prompt-based video editing with optional multi-GPU Wan inference.",
        parents=[bootstrap],
    )
    add_module_actions(parser, preprocess_parser)
    add_module_actions(parser, inference_parser)

    parser.set_defaults(
        video=DEFAULT_VIDEO,
        prompt=DEFAULT_PROMPT,
        task="depth",
        model_name=DEFAULT_MODEL_NAME,
        ckpt_dir=DEFAULT_CKPT_DIR,
    )
    return parser


def configure_multi_gpu_defaults(args: argparse.Namespace) -> None:
    if args.nproc_per_node <= 1:
        return
    if args.base != "wan":
        raise ValueError("Multi-GPU mode is only wired for the Wan backend.")
    if not shutil.which("torchrun"):
        raise RuntimeError("`torchrun` was not found in PATH.")

    if args.ulysses_size == 1 and args.ring_size == 1:
        if args.model_name == "vace-1.3B":
            args.ring_size = args.nproc_per_node
        else:
            args.ulysses_size = args.nproc_per_node
    if args.ulysses_size * args.ring_size != args.nproc_per_node:
        raise ValueError(
            f"`ulysses_size * ring_size` must equal nproc_per_node ({args.nproc_per_node}), "
            f"got {args.ulysses_size} * {args.ring_size}."
        )


def build_cli_args(args_dict: dict[str, object], parser: argparse.ArgumentParser) -> list[str]:
    cli_args: list[str] = []
    valid_actions = [action for action in parser._actions if action.dest and action.dest != "help"]
    for action in valid_actions:
        value = args_dict.get(action.dest)
        if value is None:
            continue
        if isinstance(action, argparse._StoreTrueAction):
            if value:
                cli_args.append(action.option_strings[0])
            continue
        if not action.option_strings:
            continue
        cli_args.extend([action.option_strings[0], str(value)])
    return cli_args


def run_preprocess(repo_dir: Path, args: argparse.Namespace) -> dict[str, object]:
    preprocess_parser = load_parser("vace_preproccess")
    preprocess_args = filter_args(vars(args), preprocess_parser)
    preprocesser = importlib.import_module("vace_preproccess")
    with pushd(repo_dir):
        preprocess_output = preprocesser.main(preprocess_args)
    print("preprocess_output:", preprocess_output)
    torch.cuda.empty_cache()
    return preprocess_output


def absolutize_path(value: str, base_dir: Path) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def normalize_inference_payload(repo_dir: Path, payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    for key in ("ckpt_dir", "src_video", "src_mask", "save_dir", "save_file"):
        value = normalized.get(key)
        if isinstance(value, str) and value:
            normalized[key] = absolutize_path(value, repo_dir)
    src_ref_images = normalized.get("src_ref_images")
    if isinstance(src_ref_images, str) and src_ref_images:
        normalized["src_ref_images"] = ",".join(
            absolutize_path(part, repo_dir) if part else part for part in src_ref_images.split(",")
        )
    return normalized


def run_inference_via_server(repo_dir: Path, args: argparse.Namespace, preprocess_output: dict[str, object]) -> int:
    if args.base != "wan":
        raise ValueError("Resident inference service mode currently supports only the Wan backend.")

    inference_parser = load_parser("vace_wan_inference")
    inference_args = filter_args(vars(args), inference_parser)
    inference_args.update(preprocess_output)
    payload = normalize_inference_payload(repo_dir, inference_args)

    request = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    socket_path = args.server_socket
    if socket_path is None:
        raise ValueError("server_socket is required for resident service mode.")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(socket_path)
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)

        response_chunks: list[bytes] = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            response_chunks.append(chunk)

    if not response_chunks:
        raise RuntimeError("Resident inference service closed the connection without a response.")

    response = json.loads(b"".join(response_chunks).decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Resident inference service failed."))

    print("Resident service result:", response.get("result"))
    return 0


def run_inference(repo_dir: Path, args: argparse.Namespace, preprocess_output: dict[str, object]) -> int:
    if args.server_socket:
        return run_inference_via_server(repo_dir, args, preprocess_output)

    inference_name = "vace_ltx_inference" if args.base == "ltx" else "vace_wan_inference"
    inference_parser = load_parser(inference_name)
    inference_args = filter_args(vars(args), inference_parser)
    inference_args.update(preprocess_output)

    module_path = repo_dir / "vace" / f"{inference_name}.py"
    if args.nproc_per_node > 1:
        cmd = ["torchrun", "--standalone", "--nproc_per_node", str(args.nproc_per_node), str(module_path)]
    else:
        cmd = [sys.executable, str(module_path)]
    cmd.extend(build_cli_args(inference_args, inference_parser))

    env = os.environ.copy()
    if args.cuda_visible_devices:
        env["CUDA_VISIBLE_DEVICES"] = args.cuda_visible_devices

    print("Running command:")
    print(" ".join(shlex.quote(part) for part in cmd))
    print()
    subprocess.run(cmd, cwd=str(repo_dir), env=env, check=True)
    return 0


def main() -> int:
    initial_repo_dir = repo_dir_from_args(None)
    parser = build_parser(initial_repo_dir)
    args = parser.parse_args()
    repo_dir = repo_dir_from_args(args.repo_dir)
    pipeline_path = repo_dir / "vace" / "vace_pipeline.py"

    if not repo_dir.exists():
        print(f"VACE repo directory not found: {repo_dir}", file=sys.stderr)
        return 1
    if not pipeline_path.exists():
        print(f"VACE pipeline script not found: {pipeline_path}", file=sys.stderr)
        return 1
    if getattr(args, "video", None) and not os.path.exists(args.video):
        print(f"Input video not found: {args.video}", file=sys.stderr)
        return 1

    configure_multi_gpu_defaults(args)
    preprocess_output = run_preprocess(repo_dir, args)
    return run_inference(repo_dir, args, preprocess_output)


if __name__ == "__main__":
    raise SystemExit(main())
