#!/usr/bin/env python3
import argparse
import json
import logging
import os
import socketserver
import sys
import time
import traceback
from pathlib import Path


def repo_dir_from_args(repo_dir: str | None) -> Path:
    script_dir = Path(__file__).resolve().parent
    return Path(repo_dir) if repo_dir else script_dir / "repos" / "VACE"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resident Wan inference service that keeps VACE models loaded between requests."
    )
    parser.add_argument("--repo-dir", default=None, help="Path to the VACE repo root.")
    parser.add_argument(
        "--socket-path",
        default="/tmp/vace_wan_infer.sock",
        help="UNIX socket path for the resident inference service.",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        default="vace-14B",
        help="Model name to preload in the resident service.",
    )
    parser.add_argument(
        "--ckpt_dir",
        type=str,
        default="/root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B",
        help="Checkpoint directory to preload.",
    )
    parser.add_argument(
        "--nproc-per-node",
        type=int,
        default=4,
        help="Number of GPUs to keep loaded. Values >1 use WanVaceMP.",
    )
    parser.add_argument("--ulysses_size", type=int, default=4, help="Sequence parallel size.")
    parser.add_argument("--ring_size", type=int, default=1, help="Ring attention parallel size.")
    parser.add_argument("--t5_cpu", action="store_true", help="Single-GPU mode only: keep T5 on CPU.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    return parser


class ResidentWanInferenceService:
    def __init__(self, args: argparse.Namespace) -> None:
        repo_dir = repo_dir_from_args(args.repo_dir).resolve()
        vace_dir = repo_dir / "vace"
        if str(vace_dir) not in sys.path:
            sys.path.insert(0, str(vace_dir))

        from models.wan.configs import SIZE_CONFIGS, WAN_CONFIGS
        from models.wan.wan_vace import WanVace, WanVaceMP
        from vace_wan_inference import validate_args
        from wan.utils.utils import cache_image, cache_video

        self.args = args
        self.repo_dir = repo_dir
        self.size_configs = SIZE_CONFIGS
        self.wan_configs = WAN_CONFIGS
        self.validate_args = validate_args
        self.cache_image = cache_image
        self.cache_video = cache_video

        config = WAN_CONFIGS[args.model_name]
        ckpt_dir = str(Path(args.ckpt_dir).resolve())
        use_usp = args.ulysses_size > 1 or args.ring_size > 1

        os.chdir(repo_dir)
        if args.nproc_per_node > 1:
            self.pipe = WanVaceMP(
                config=config,
                checkpoint_dir=ckpt_dir,
                use_usp=use_usp,
                ulysses_size=args.ulysses_size,
                ring_size=args.ring_size,
            )
        else:
            self.pipe = WanVace(
                config=config,
                checkpoint_dir=ckpt_dir,
                device_id=0,
                rank=0,
                t5_fsdp=False,
                dit_fsdp=False,
                use_usp=False,
                t5_cpu=args.t5_cpu,
            )

    def _normalize_request(self, request: dict[str, object]) -> argparse.Namespace:
        payload = dict(request)
        payload.setdefault("model_name", self.args.model_name)
        payload.setdefault("ckpt_dir", str(Path(self.args.ckpt_dir).resolve()))
        payload["offload_model"] = False
        args = argparse.Namespace(**payload)
        return self.validate_args(args)

    def _save_outputs(self, args: argparse.Namespace, cfg, video, src_video, src_mask, src_ref_images) -> dict[str, str]:
        ret_data: dict[str, str] = {}
        if args.save_dir is None:
            save_dir = os.path.join("results", args.model_name, time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime()))
        else:
            save_dir = args.save_dir
        os.makedirs(save_dir, exist_ok=True)

        if args.save_file is not None:
            out_video_path = args.save_file
        else:
            out_video_path = os.path.join(save_dir, "out_video.mp4")

        self.cache_video(
            tensor=video[None],
            save_file=out_video_path,
            fps=cfg.sample_fps,
            nrow=1,
            normalize=True,
            value_range=(-1, 1),
        )
        ret_data["out_video"] = out_video_path

        src_video_path = os.path.join(save_dir, "src_video.mp4")
        self.cache_video(
            tensor=src_video[0][None],
            save_file=src_video_path,
            fps=cfg.sample_fps,
            nrow=1,
            normalize=True,
            value_range=(-1, 1),
        )
        ret_data["src_video"] = src_video_path

        src_mask_path = os.path.join(save_dir, "src_mask.mp4")
        self.cache_video(
            tensor=src_mask[0][None],
            save_file=src_mask_path,
            fps=cfg.sample_fps,
            nrow=1,
            normalize=True,
            value_range=(0, 1),
        )
        ret_data["src_mask"] = src_mask_path

        if src_ref_images[0] is not None:
            for i, ref_img in enumerate(src_ref_images[0]):
                ref_path = os.path.join(save_dir, f"src_ref_image_{i}.png")
                self.cache_image(
                    tensor=ref_img[:, 0, ...],
                    save_file=ref_path,
                    nrow=1,
                    normalize=True,
                    value_range=(-1, 1),
                )
                ret_data[f"src_ref_image_{i}"] = ref_path

        return ret_data

    def run(self, request: dict[str, object]) -> dict[str, str]:
        args = self._normalize_request(request)
        cfg = self.wan_configs[args.model_name]
        src_ref_images = [None if args.src_ref_images is None else args.src_ref_images.split(",")]
        source_size = self.size_configs[args.size]

        src_video, src_mask, src_ref_images = self.pipe.prepare_source(
            [args.src_video],
            [args.src_mask],
            [src_ref_images[0]],
            args.frame_num,
            source_size,
            self.pipe.device,
        )

        video = self.pipe.generate(
            args.prompt,
            src_video,
            src_mask,
            src_ref_images,
            size=source_size,
            frame_num=args.frame_num,
            shift=args.sample_shift,
            sample_solver=args.sample_solver,
            sampling_steps=args.sample_steps,
            guide_scale=args.sample_guide_scale,
            seed=args.base_seed,
            offload_model=False,
        )

        return self._save_outputs(args, cfg, video, src_video, src_mask, src_ref_images)


def build_handler(service: ResidentWanInferenceService):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            line = self.rfile.readline()
            if not line:
                return

            try:
                request = json.loads(line.decode("utf-8"))
                logging.info("Received resident inference request.")
                result = service.run(request)
                response = {"ok": True, "result": result}
            except Exception as exc:
                logging.exception("Resident inference request failed.")
                response = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}

            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))

    return Handler


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s: %(message)s",
    )

    socket_path = Path(args.socket_path)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    service = ResidentWanInferenceService(args)
    handler = build_handler(service)

    class UnixSocketServer(socketserver.UnixStreamServer):
        allow_reuse_address = True

    with UnixSocketServer(str(socket_path), handler) as server:
        logging.info("Resident Wan inference service listening on %s", socket_path)
        try:
            server.serve_forever()
        finally:
            if socket_path.exists():
                socket_path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
