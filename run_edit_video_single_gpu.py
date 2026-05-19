#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import gc
import importlib
import os
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_VIDEO = "/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4"
DEFAULT_PROMPT = "把画面中的人物改成男人，其他不变。"
DEFAULT_MODEL_NAME = "vace-14B"

VIDEO_EDIT_TASKS = [
    "plain",
    "depth",
    "depthv2",
    "flow",
    "gray",
    "pose",
    "pose_body",
    "scribble",
    "inpainting",
    "inpainting_mask",
    "inpainting_bbox",
    "inpainting_masktrack",
    "inpainting_bboxtrack",
    "inpainting_label",
    "inpainting_caption",
    "outpainting",
    "outpainting_inner",
    "layout_track",
]

MODEL_DEFAULTS = {
    "vace-1.3B": {
        "ckpt_dir_name": "Wan2.1-VACE-1.3B",
        "size": "480p",
    },
    "vace-14B": {
        "ckpt_dir_name": "Wan2.1-VACE-14B",
        "size": "720p",
    },
}


@contextlib.contextmanager
def pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def parse_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def parse_bboxes(raw: str) -> list[list[float]]:
    bboxes: list[list[float]] = []
    for chunk in raw.split():
        coords = [float(item) for item in chunk.split(",")]
        if len(coords) != 4:
            raise argparse.ArgumentTypeError(
                "Each bbox must contain 4 comma-separated numbers: x1,y1,x2,y2"
            )
        bboxes.append(coords)
    return bboxes


def repo_dir_from_args(repo_dir: str | None) -> Path:
    script_dir = Path(__file__).resolve().parent
    if repo_dir:
        return Path(repo_dir).expanduser().resolve()
    return script_dir / "repos" / "VACE"


def resolve_path(path: str | None) -> str | None:
    if path is None:
        return None
    return str(Path(path).expanduser().resolve())


def resolve_csv_paths(value: str | None) -> str | None:
    if value is None:
        return None
    parts = [item.strip() for item in value.split(",") if item.strip()]
    if not parts:
        return None
    return ",".join(resolve_path(item) for item in parts)


def existing_csv_paths(value: str | None) -> list[Path]:
    if value is None:
        return []
    return [Path(item) for item in value.split(",") if item]


def add_vace_to_syspath(repo_dir: Path) -> None:
    vace_dir = repo_dir / "vace"
    if not vace_dir.exists():
        raise FileNotFoundError(f"VACE module directory not found: {vace_dir}")
    if str(vace_dir) not in sys.path:
        sys.path.insert(0, str(vace_dir))


def load_parser(module_name: str) -> argparse.ArgumentParser:
    module = importlib.import_module(module_name)
    if not hasattr(module, "get_parser"):
        raise ValueError(f"{module_name} does not define get_parser()")
    return module.get_parser()


def filter_args(args: dict[str, object], parser: argparse.ArgumentParser) -> dict[str, object]:
    valid = {
        action.dest
        for action in parser._actions
        if action.dest and action.dest != "help"
    }
    return {key: value for key, value in args.items() if key in valid}


def build_cli_args(args_dict: dict[str, object], parser: argparse.ArgumentParser) -> list[str]:
    cli_args: list[str] = []
    for action in parser._actions:
        if not action.dest or action.dest == "help":
            continue

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


def infer_mode(args: argparse.Namespace) -> str | None:
    if args.mode:
        return args.mode

    if args.task == "inpainting":
        if args.mask:
            return "mask"
        if args.bbox:
            return "bbox"
        if args.label:
            return "label"
        if args.caption:
            return "caption"

    if args.task == "layout_track":
        if args.mask:
            return "masktrack"
        if args.bbox:
            return "bboxtrack"
        if args.caption:
            return "caption"
        if args.label:
            return "label"

    return None


def cleanup_torch() -> None:
    try:
        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def validate_paths(args: argparse.Namespace, repo_dir: Path) -> None:
    if not repo_dir.exists():
        raise FileNotFoundError(f"VACE repo directory not found: {repo_dir}")

    if not Path(args.video).exists():
        raise FileNotFoundError(f"Input video not found: {args.video}")

    if args.mask and not Path(args.mask).exists():
        raise FileNotFoundError(f"Input mask not found: {args.mask}")

    for ref_image in existing_csv_paths(args.src_ref_images):
        if not ref_image.exists():
            raise FileNotFoundError(f"Reference image not found: {ref_image}")

    if not Path(args.ckpt_dir).exists():
        raise FileNotFoundError(f"Checkpoint directory not found: {args.ckpt_dir}")


def validate_semantics(args: argparse.Namespace) -> None:
    if args.frame_num <= 0 or args.frame_num % 4 != 1:
        raise ValueError("--frame-num must be a positive number in the form 4n+1, for example 81.")

    if args.task == "plain":
        if args.mask or args.bbox or args.label or args.caption:
            raise ValueError("`plain` task does not use mask/bbox/label/caption. Use an inpainting task instead.")
        return

    if args.task in {"inpainting_mask", "inpainting_masktrack"} and not args.mask:
        raise ValueError(f"`{args.task}` requires --mask.")

    if args.task in {"inpainting_bbox", "inpainting_bboxtrack"} and not args.bbox:
        raise ValueError(f"`{args.task}` requires --bbox.")

    if args.task == "inpainting_label" and not args.label:
        raise ValueError("`inpainting_label` requires --label.")

    if args.task == "inpainting_caption" and not args.caption:
        raise ValueError("`inpainting_caption` requires --caption.")

    if args.task == "inpainting" and not args.mode:
        raise ValueError(
            "`inpainting` requires one of --mask / --bbox / --label / --caption, or an explicit --mode."
        )

    if args.task == "layout_track" and not args.mode:
        raise ValueError(
            "`layout_track` requires one of --mask / --bbox / --label / --caption, or an explicit --mode."
        )


def build_parser(default_repo_dir: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Single-GPU VACE video editing runner for Wan2.1-VACE.",
    )
    parser.add_argument("--repo-dir", default=str(default_repo_dir), help="Path to the VACE repository root.")
    parser.add_argument("--video", default=DEFAULT_VIDEO, help="Input video path.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Editing prompt.")
    parser.add_argument("--task", default="plain", choices=VIDEO_EDIT_TASKS, help="VACE video preprocessing task.")
    parser.add_argument("--mode", default=None, help="Optional preprocessing mode such as mask, bbox, bboxtrack.")
    parser.add_argument("--mask", default=None, help="Mask image path for masked video editing.")
    parser.add_argument(
        "--bbox",
        type=parse_bboxes,
        default=None,
        help="One or more bounding boxes. Format: x1,y1,x2,y2 or 'x1,y1,x2,y2 x1,y1,x2,y2'.",
    )
    parser.add_argument("--label", default=None, help="Label text for label-based editing.")
    parser.add_argument("--caption", default=None, help="Caption text for caption-based editing.")
    parser.add_argument(
        "--ref-images",
        dest="src_ref_images",
        default=None,
        help="Comma-separated reference image paths.",
    )
    parser.add_argument("--direction", default=None, help="Outpainting directions such as up,down,left,right.")
    parser.add_argument("--expand-ratio", type=float, default=None, help="Outpainting expand ratio.")
    parser.add_argument("--expand-num", type=int, default=None, help="Frame expansion count for frame reference tasks.")
    parser.add_argument("--maskaug-mode", default=None, help="Mask augmentation mode.")
    parser.add_argument("--maskaug-ratio", type=float, default=None, help="Mask augmentation expand ratio.")
    parser.add_argument("--pre-save-dir", default=None, help="Directory for saving preprocessing outputs.")
    parser.add_argument("--save-fps", type=int, default=16, help="FPS for preprocessing output videos.")
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        choices=sorted(MODEL_DEFAULTS),
        help="Wan2.1-VACE model to use.",
    )
    parser.add_argument("--ckpt-dir", default=None, help="Checkpoint directory. Defaults to workspace models path.")
    parser.add_argument("--size", default=None, help="Output size such as 480p or 720p.")
    parser.add_argument("--frame-num", type=int, default=81, help="Number of output frames. Must be 4n+1.")
    parser.add_argument("--offload-model", type=parse_bool, default=None, help="Whether to offload the model to CPU.")
    parser.add_argument("--save-dir", default=None, help="Directory for final inference outputs.")
    parser.add_argument("--save-file", default=None, help="Output video path.")
    parser.add_argument("--use-prompt-extend", default="plain", help="Prompt extension mode.")
    parser.add_argument("--base-seed", type=int, default=2025, help="Random seed for inference.")
    parser.add_argument("--sample-solver", default="unipc", choices=["unipc", "dpm++"], help="Sampling solver.")
    parser.add_argument("--sample-steps", type=int, default=None, help="Sampling step count.")
    parser.add_argument("--sample-shift", type=float, default=None, help="Flow-matching shift value.")
    parser.add_argument("--sample-guide-scale", type=float, default=5.0, help="CFG guidance scale.")
    parser.add_argument("--cuda-visible-devices", default=None, help="Single GPU id, for example `0`.")
    return parser


def normalize_args(args: argparse.Namespace, repo_dir: Path) -> argparse.Namespace:
    args.video = resolve_path(args.video)
    args.mask = resolve_path(args.mask)
    args.src_ref_images = resolve_csv_paths(args.src_ref_images)
    args.repo_dir = str(repo_dir)

    model_defaults = MODEL_DEFAULTS[args.model_name]
    if args.ckpt_dir is None:
        args.ckpt_dir = str(repo_dir.parent.parent / "models" / model_defaults["ckpt_dir_name"])
    else:
        args.ckpt_dir = resolve_path(args.ckpt_dir)

    if args.size is None:
        args.size = model_defaults["size"]

    if args.pre_save_dir is not None:
        args.pre_save_dir = resolve_path(args.pre_save_dir)
    if args.save_dir is not None:
        args.save_dir = resolve_path(args.save_dir)
    if args.save_file is not None:
        args.save_file = resolve_path(args.save_file)

    args.mode = infer_mode(args)
    return args


def run_preprocess(repo_dir: Path, args: argparse.Namespace) -> dict[str, object]:
    if args.task == "plain":
        return {"src_video": args.video}

    add_vace_to_syspath(repo_dir)
    preprocess_parser = load_parser("vace_preproccess")
    preprocess_module = importlib.import_module("vace_preproccess")
    preprocess_args = filter_args(vars(args), preprocess_parser)

    with pushd(repo_dir):
        output = preprocess_module.main(preprocess_args)

    cleanup_torch()
    return output


def run_inference(repo_dir: Path, args: argparse.Namespace, preprocess_output: dict[str, object]) -> int:
    add_vace_to_syspath(repo_dir)
    inference_parser = load_parser("vace_wan_inference")
    inference_args = filter_args(vars(args), inference_parser)
    inference_args.update(preprocess_output)

    command = [sys.executable, str(repo_dir / "vace" / "vace_wan_inference.py")]
    command.extend(build_cli_args(inference_args, inference_parser))

    print("Running command:")
    print(" ".join(shlex.quote(part) for part in command))
    print()

    subprocess.run(command, cwd=str(repo_dir), env=os.environ.copy(), check=True)
    return 0


def main() -> int:
    repo_dir = repo_dir_from_args(None)
    parser = build_parser(repo_dir)
    args = parser.parse_args()

    repo_dir = repo_dir_from_args(args.repo_dir)
    args = normalize_args(args, repo_dir)

    if args.cuda_visible_devices:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.cuda_visible_devices

    validate_paths(args, repo_dir)
    validate_semantics(args)

    preprocess_output = run_preprocess(repo_dir, args)
    return run_inference(repo_dir, args, preprocess_output)


if __name__ == "__main__":
    raise SystemExit(main())
