# VACE 四卡常驻命令

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
