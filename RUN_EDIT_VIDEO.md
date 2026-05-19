# run_edit_video.py 启动说明

在仓库根目录执行：

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh
```

## 单卡运行

```bash
python run_edit_video.py \
  --video /root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4 \
  --prompt "镜头中任务改成男人其他不变"
```

更稳一点的参数：

```bash
python run_edit_video.py \
  --size 480p \
  --frame_num 49 \
  --sample_steps 30 \
  --video /root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4 \
  --prompt "镜头中任务改成男人其他不变"
```

## 双卡运行

先安装多卡依赖：

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy -u NO_PROXY -u no_proxy \
python -m pip install PySocks "xfuser>=0.4.1"
```

然后执行：

```bash
python run_edit_video.py \
  --nproc-per-node 2 \
  --cuda-visible-devices 0,1 \
  --size 480p \
  --frame_num 49 \
  --sample_steps 30 \
  --video /root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4 \
  --prompt "镜头中任务改成男人其他不变"
```

## 输出位置

- 预处理结果：`repos/VACE/processed/`
- 最终结果：`repos/VACE/results/`

## 当前已知问题

当前环境下：

- 单卡可以启动并进入推理，但默认 `vace-14B` 配置可能会显存不足
- 双卡可以完成分布式初始化和模型加载，但目前会在采样阶段因为 `xfuser / yunchang / flash_attn` 版本不兼容而报错
