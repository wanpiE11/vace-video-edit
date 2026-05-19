# 单卡启动方式

`run_edit_video_single_gpu.py` 在工作区根目录，不在 `repos/VACE/` 里。

而 `source activate_vace.sh` 之后，shell 会自动切到：

```text
/root/data/gzn/vace-video-edit/repos/VACE
```

所以不要在激活后直接执行：

```bash
python run_edit_video_single_gpu.py ...
```

否则常见结果是找不到脚本，终端退出，`exit code: 2`。

## 正确启动

推荐直接用绝对路径：

```bash
source /root/data/gzn/vace-video-edit/activate_vace.sh

python /root/data/gzn/vace-video-edit/run_edit_video_single_gpu.py \
  --cuda-visible-devices 0 \
  --model-name vace-14B \
  --ckpt-dir "$VACE_MODEL_ROOT/Wan2.1-VACE-14B" \
  --size 480p \
  --frame-num 49 \
  --video "$VACE_INPUT_ROOT/videos/scene_01_shot_02.mp4" \
  --prompt "把画面中的人物改成男人，其他不变。"
```

## 另一种写法

先回到工作区根目录，再运行相对路径：

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh
cd /root/data/gzn/vace-video-edit

python run_edit_video_single_gpu.py \
  --cuda-visible-devices 0 \
  --model-name vace-14B \
  --ckpt-dir "$VACE_MODEL_ROOT/Wan2.1-VACE-14B" \
  --size 480p \
  --frame-num 49 \
  --video "$VACE_INPUT_ROOT/videos/scene_01_shot_02.mp4" \
  --prompt "把画面中的人物改成男人，其他不变。"
```

## 说明

- 默认那条不带参数的启动方式会走 `14B + 720p + 81帧`，当前机器上容易显存不足。
- 上面给的是已经实际跑通的一组单卡参数：`14B + 480p + 49帧`。
