# VACE 四卡常驻命令

这份文档对应 [VideoEditAPI.md](/root/data/gzn/vace-video-edit/VideoEditAPI.md) 里的“先预热 engine，再测 HTTP API”模式。

默认参考图里有背景，所以这套命令用 `--mode bboxtrack,salient`。

## 1. 预加载四卡服务

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh

python /root/data/gzn/vace-video-edit/run_edit_video_server.py \
  --socket-path /tmp/vace_wan_infer.sock \
  --model_name vace-14B \
  --ckpt_dir /root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B \
  --nproc-per-node 4 \
  --ulysses_size 4 \
  --ring_size 1
```

如果是验证 HTTP API，不要在这里直接发 HTTP 请求；等 socket ready 后，再按 `VideoEditAPI.md` 里的 `uvicorn video_edit_api:app` 命令启动 API 服务。

## 2. 跑视频编辑

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh

python /root/data/gzn/vace-video-edit/run_edit_video.py \
  --server-socket /tmp/vace_wan_infer.sock \
  --nproc-per-node 4 \
  --cuda-visible-devices 0,1,2,3 \
  --dit_fsdp \
  --t5_fsdp \
  --task swap_anything \
  --video /root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4 \
  --image /root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png \
  --mode bboxtrack,salient \
  --bbox 421,0,826,535 \
  --size 720p \
  --model_name vace-14B \
  --ckpt_dir /root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B \
  --prompt "镜头中的人物改成参考图的人物，其他不变"
```

## 3. 真机经验

- 这套四卡常驻服务在真机上可以跑通，但首次启动非常慢。
- 实测 `run_edit_video_server.py` 大约用了 39 分钟才把 `/tmp/vace_wan_infer.sock` 拉起来；看到 socket 出来前，不代表已经卡死。
- 启动阶段显存会逐步上涨到每卡约 20GB+，真正进入推理后会接近满卡并长时间保持 100% GPU 利用率。
- `run_edit_video.py` 的本地预处理会先单独跑一段，之后才会通过 socket 把请求发给常驻服务。
