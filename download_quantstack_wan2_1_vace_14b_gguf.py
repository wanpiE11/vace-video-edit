from pathlib import Path
import os
import sys

from modelscope import snapshot_download


MODEL_ID = "QuantStack/Wan2.1-VACE-14B-GGUF"
ROOT = Path("/root/data/gzn/vace-video-edit")
TARGET_DIR = ROOT / "models" / "Wan2.1-VACE-14B-GGUF"
CACHE_DIR = ROOT / "cache" / "modelscope"
DONE_MARKER = ROOT / "logs" / "Wan2.1-VACE-14B-GGUF.download.done"


def main() -> int:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DONE_MARKER.unlink(missing_ok=True)

    os.environ.setdefault("HF_HOME", str(ROOT / "cache" / "hf_home"))
    os.environ.setdefault("MODELSCOPE_CACHE", str(CACHE_DIR))

    print(f"MODEL_ID={MODEL_ID}", flush=True)
    print(f"TARGET_DIR={TARGET_DIR}", flush=True)
    print(f"CACHE_DIR={CACHE_DIR}", flush=True)
    path = snapshot_download(
        model_id=MODEL_ID,
        cache_dir=str(CACHE_DIR),
        local_dir=str(TARGET_DIR),
        max_workers=4,
    )
    print(f"DOWNLOAD_DONE={path}", flush=True)
    DONE_MARKER.write_text(path + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
