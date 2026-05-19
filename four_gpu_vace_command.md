# VACE 四卡运行命令

已验证可跑通的四卡底层 `torchrun` 命令如下。

## 1. 直接运行推理

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh
cd repos/VACE

CUDA_VISIBLE_DEVICES=0,1,2,3 \
torchrun --standalone --nproc_per_node 4 vace/vace_wan_inference.py \
  --dit_fsdp \
  --t5_fsdp \
  --ulysses_size 4 \
  --ring_size 1 \
  --size 720p \
  --model_name vace-14B \
  --ckpt_dir /root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B \
  --src_video /root/data/gzn/vace-video-edit/workspace/outputs/processed/depth/2026-05-19-15-33-06/src_video-depth.mp4 \
  --prompt "镜头中任务改成男人其他不变"
```

## 2. 和 `run_edit_video.py` 的关系

`run_edit_video.py` 是上层封装。

它会先做预处理，再自动调用类似下面这条四卡命令：

```bash
torchrun --standalone --nproc_per_node 4 vace/vace_wan_inference.py \
  --dit_fsdp \
  --t5_fsdp \
  --ulysses_size 4 \
  --ring_size 1 \
  --size 720p \
  --model_name vace-14B \
  --ckpt_dir /root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B \
  --src_video <预处理后生成的 src_video> \
  --prompt "镜头中任务改成男人其他不变"
```

## 3. 已验证结果

本机已实际跑通一次，输出目录为：

```text
/root/data/gzn/vace-video-edit/workspace/outputs/results/vace-14B/2026-05-19-15-53-42/
```

输出文件包括：

- `out_video.mp4`
- `src_video.mp4`
- `src_mask.mp4`

## 4. 注意

- 这条底层命令不会自动做预处理。
- 如果任务需要局部编辑或参考图，需要额外补 `--src_mask` 和 `--src_ref_images`。
- 如果改成 8 卡，对应关系通常是把 `--nproc_per_node 4` 和 `--ulysses_size 4` 改成 `8`。

## 5. 你的当前需求: 原视频 + 参考图 + 提示词 + 首帧框选区域做局部替换

这个需求更适合用 `swap_anything`，不是 `depth`。

- 视频侧: 用 `bboxtrack`，表示用首帧框选框追踪整段视频中的替换区域。
- 参考图侧:
  - 如果参考图本身就是已经裁干净的主体，建议用 `plain`
  - 如果参考图里还有背景，建议用 `salient` 让它自动抠主体

### 推荐命令: 四卡封装入口

如果参考图已经是干净主体:

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh

python run_edit_video.py \
  --nproc-per-node 4 \
  --cuda-visible-devices 0,1,2,3 \
  --dit_fsdp \
  --t5_fsdp \
  --task swap_anything \
  --video /path/to/src_video.mp4 \
  --image /path/to/ref_image.png \
  --mode bboxtrack,plain \
  --bbox 100,120,420,680 \
  --size 720p \
  --model_name vace-14B \
  --ckpt_dir /root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B \
  --prompt "把框选区域中的人物替换成参考图中的人物，保留原镜头运动、背景和其他区域不变"
```

如果参考图里还有背景:

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh

python run_edit_video.py \
  --nproc-per-node 4 \
  --cuda-visible-devices 0,1,2,3 \
  --dit_fsdp \
  --t5_fsdp \
  --task swap_anything \
  --video /path/to/src_video.mp4 \
  --image /path/to/ref_image.png \
  --mode bboxtrack,salient \
  --bbox 100,120,420,680 \
  --size 720p \
  --model_name vace-14B \
  --ckpt_dir /root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B \
  --prompt "把框选区域中的主体替换成参考图中的主体，保留原镜头运动、背景和其他区域不变"
```

### 参数说明

- `--task swap_anything`: 组合“局部编辑 + 参考图主体注入”
- `--mode bboxtrack,plain`:
  - 前一个 `bboxtrack` 给视频侧 inpainting，用首帧框跟踪整段视频
  - 后一个 `plain` 给参考图侧 reference，直接使用整张参考图
- `--mode bboxtrack,salient`:
  - 视频侧仍然是首帧框跟踪
  - 参考图侧自动提主体
- `--bbox x1,y1,x2,y2`: 首帧里的替换区域
- `--image`: 参考图路径。多张参考图可用逗号分隔

### 底层关系

这条命令会先自动预处理，产出:

- `src_video`
- `src_mask`
- `src_ref_images`

然后再调用四卡 `torchrun vace/vace_wan_inference.py` 去生成结果。



cd /root/data/gzn/vace-video-edit
  source activate_vace.sh

  python run_edit_video.py \
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