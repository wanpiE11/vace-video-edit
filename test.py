import os
import time

# 必须放在 import huggingface_hub 之前
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "60"

from huggingface_hub import snapshot_download

repo_id = "ali-vilab/VACE-Annotators"

while True:
    try:
        snapshot_download(
            repo_id=repo_id,
            endpoint="https://hf-mirror.com",  # 再显式指定一次，避免环境变量没生效
            max_workers=1,
            local_dir="/root/data/gzn/vace-video-edit/repos/VACE/models/VACE-Annotators",
        )
        print("下载完成")
        break

    except Exception as e:
        print("下载中断，10 秒后继续重试：")
        print(type(e).__name__, e)
        time.sleep(10)