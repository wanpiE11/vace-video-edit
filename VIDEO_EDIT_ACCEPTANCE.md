# 视频编辑模块交付说明

本文档用于把当前视频编辑模块交给测试同事和前端同事联调使用。

- 对外接口契约文档：[`VideoEditAPI.md`](/root/data/gzn/vace-video-edit/VideoEditAPI.md)
- 四卡常驻 engine 启动说明：[`four_gpu_vace_command.md`](/root/data/gzn/vace-video-edit/four_gpu_vace_command.md)
- 单次命令行运行说明：[`RUN_EDIT_VIDEO.md`](/root/data/gzn/vace-video-edit/RUN_EDIT_VIDEO.md)

`VideoEditAPI.md` 仍然是正式接口文档；本文档是交付、启动、联调和验收说明，不替代接口契约。

## 1. 交付结论

当前模块已经具备以下交付能力：

- 提供基于 HTTP 的异步视频编辑接口
- 输入原始视频、参考图、提示词和 bbox
- 服务端异步执行视频编辑任务
- 支持任务状态轮询
- 支持结果查询
- 支持输出视频下载

已完成一次真机全链路验证，验证时间为 `2026-05-20`，验证链路如下：

1. `GET /healthz`
2. `POST /api/v1/video-editing/jobs`
3. `GET /api/v1/video-editing/jobs/{job_id}`
4. `GET /api/v1/video-editing/jobs/{job_id}/results`
5. `GET /api/v1/video-editing/jobs/{job_id}/output/download`

真机验证结果：

- `job_id`: `edit_job_2ec458c1a9c24417`
- 创建时间: `2026-05-20T08:18:09Z`
- 完成时间: `2026-05-20T08:24:47Z`
- 总耗时: 约 `6分38秒`
- 输出视频: [api_e2e_real_machine.mp4](/root/data/gzn/vace-video-edit/workspace/jobs/edit_job_2ec458c1a9c24417/results/api_e2e_real_machine.mp4)
- 下载校验文件: [edit_job_2ec458c1a9c24417.mp4](/root/data/gzn/vace-video-edit/workspace/test_downloads/edit_job_2ec458c1a9c24417.mp4)
- 输出文件可读，`81` 帧，`16fps`，`832x480`

## 2. 测试地址

当前机器 IP：

- `10.233.70.35`

建议联调地址：

- `http://10.233.70.35:8880`

本机地址：

- `http://127.0.0.1:8880`

说明：

- 端口固定按当前文档使用 `8880`
- 启动 API 时请使用 `--host 0.0.0.0`
- 如果 shell 设置了代理，测试本机或内网地址时建议带 `--noproxy '*'`

## 3. 建议交付方式

建议测试和前端联调统一采用“先预热 engine，再启动 HTTP API”的模式。

原因：

- 更稳定
- `healthz` 状态更容易观察
- 避免首个任务触发长时间模型加载，影响前端联调体验
- 更接近后续线上常驻服务形态

## 4. 启动步骤

### 4.1 前置条件

启动前应确认：

- 机器具备 4 张可用 GPU
- 模型目录存在：`/root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B`
- 默认测试视频存在：`/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4`
- 默认参考图存在：`/root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png`

### 4.2 启动四卡常驻 engine

在仓库根目录执行：

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

说明：

- 首次模型加载很慢，等待时间可能较长
- `/tmp/vace_wan_infer.sock` 出现前，不代表启动失败
- 进入推理后，4 张卡会长时间高利用率

### 4.3 启动 HTTP API

另开一个 shell：

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh

python -m uvicorn video_edit_api:app --host 0.0.0.0 --port 8880
```

启动后，对外联调地址为：

```text
http://10.233.70.35:8880
```

### 4.4 用 tmux 常驻启动

如果希望退出 SSH 后服务继续运行，建议分别用两个 `tmux` 会话启动 `engine` 和 `API`。

启动四卡常驻 `engine`：

```bash
tmux new -s vace-engine
```

进入会话后执行：

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

启动 HTTP API：

```bash
tmux new -s vace-api
```

进入会话后执行：

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh

python -m uvicorn video_edit_api:app --host 0.0.0.0 --port 8880
```

只退出 `tmux` 会话但保持进程继续运行：

```bash
Ctrl+b d
```

重新进入会话：

```bash
tmux attach -t vace-engine
tmux attach -t vace-api
```

查看当前 `tmux` 会话：

```bash
tmux ls
```

### 4.5 查看是否已经启动

查看 `engine` 进程：

```bash
ps -ef | grep run_edit_video_server.py | grep -v grep
```

查看 API 进程：

```bash
ps -ef | grep "python -m uvicorn video_edit_api:app" | grep -v grep
```

查看 `8880` 端口监听：

```bash
ss -ltnp | grep :8880
```

查看 `engine` socket 是否 ready：

```bash
ls -l /tmp/vace_wan_infer.sock
```

查看 API 健康状态：

```bash
curl --noproxy '*' http://127.0.0.1:8880/healthz
```

### 4.6 退出、停止和终止

进入 `tmux` 会话后，正常停止当前前台进程：

```bash
Ctrl+C
```

进程停掉后，再退出当前 shell：

```bash
exit
```

如果只是想离开会话、不停止进程，使用：

```bash
Ctrl+b d
```

如果要直接关闭整个 `tmux` 会话：

```bash
tmux kill-session -t vace-engine
tmux kill-session -t vace-api
```

如果要按进程名终止：

```bash
pkill -f run_edit_video_server.py
pkill -f "python -m uvicorn video_edit_api:app"
```

如果普通终止没有生效，再强制终止：

```bash
pkill -9 -f run_edit_video_server.py
pkill -9 -f "python -m uvicorn video_edit_api:app"
```

说明：

- 优先使用 `Ctrl+C` 或 `pkill -f`，避免直接强杀。
- 强制终止会直接打断当前推理中的任务。
- 当前服务没有“取消单个任务”接口；停掉 API 或 engine 会影响当前内存里的全部任务。

## 5. 调用说明

完整接口字段、状态码和错误码定义，以 [`VideoEditAPI.md`](/root/data/gzn/vace-video-edit/VideoEditAPI.md) 为准。

这里给测试同事和前端同事一套最小可用调用流程。

### 5.1 健康检查

```bash
curl --noproxy '*' http://10.233.70.35:8880/healthz
```

期望：

- HTTP `200`
- `ok=true`
- `engine_state` 为 `ready` 或 `busy`

### 5.2 创建任务

```bash
curl --noproxy '*' \
  -X POST http://10.233.70.35:8880/api/v1/video-editing/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "workspace_id": "frontend_integration",
    "video_path": "/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4",
    "reference_image_path": "/root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png",
    "prompt": "将框选区域中的人物替换成哪吒，保持背景、镜头运动和其他区域不变",
    "bbox": [421, 0, 826, 535],
    "output_name": "frontend_integration.mp4",
    "resolution": "480p",
    "fps": 16,
    "seed": 123456,
    "client_request_id": "frontend-integration-001"
  }'
```

期望：

- HTTP `202`
- 返回 `job_id`
- 返回 `status_url`
- 返回 `results_url`
- 返回 `output_download_url`

### 5.3 轮询任务状态

```bash
curl --noproxy '*' \
  http://10.233.70.35:8880/api/v1/video-editing/jobs/<job_id>
```

期望：

- 任务状态会经历 `queued` 或 `running`
- 完成后状态为 `done`
- 完成后 `output.output_video_path` 非空

### 5.4 未完成时取结果

```bash
curl --noproxy '*' \
  http://10.233.70.35:8880/api/v1/video-editing/jobs/<job_id>/results
```

期望：

- 若任务尚未完成，返回 HTTP `409`
- 错误码为 `JOB_NOT_COMPLETED`

### 5.5 获取结果

```bash
curl --noproxy '*' \
  http://10.233.70.35:8880/api/v1/video-editing/jobs/<job_id>/results
```

期望：

- 任务完成后返回 HTTP `200`
- 返回 `output_dir`
- 返回 `output_video_path`
- 返回 `src_video_path`
- 返回 `src_mask_path`
- 返回 `src_ref_image_paths`

### 5.6 下载输出视频

```bash
curl --noproxy '*' -OJ \
  http://10.233.70.35:8880/api/v1/video-editing/jobs/<job_id>/output/download
```

期望：

- HTTP `200`
- `Content-Type: video/mp4`
- 下载文件非空

## 6. 前端联调建议

前端联调时建议采用以下约定：

- 创建任务后保存 `job_id`
- 用 `GET /api/v1/video-editing/jobs/{job_id}` 轮询状态
- 轮询间隔建议 `10s` 到 `15s`
- 收到 `done` 后，再调用 `/results`
- 最终播放或下载使用 `/output/download`

建议前端在状态上至少覆盖：

- `queued`
- `running`
- `done`
- `failed`

建议前端在错误提示上至少覆盖：

- `INVALID_ARGUMENT`
- `BBOX_OUT_OF_RANGE`
- `FILE_NOT_FOUND`
- `JOB_NOT_FOUND`
- `JOB_NOT_COMPLETED`
- `OUTPUT_NOT_AVAILABLE`
- `ENGINE_START_FAILED`
- `ENGINE_EXECUTION_FAILED`

## 7. 当前固定测试素材

建议测试阶段先不要让前端上传任意文件，先走固定服务器本地路径。

固定测试素材：

- 视频：`/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4`
- 参考图：`/root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png`
- bbox：`[421, 0, 826, 535]`

说明：

- 当前版本只支持服务器本地绝对路径输入
- `video_path` 和 `reference_image_path` 必须是绝对路径
- `output_name` 只能是文件名，不能带目录

## 8. 验收标准

测试同事联调通过后，可认为模块达到前端合入条件的最低标准：

1. API 服务能按本文档启动
2. `GET /healthz` 返回正常
3. `POST /jobs` 能成功创建任务
4. `GET /jobs/{job_id}` 能正常轮询到 `done`
5. `GET /jobs/{job_id}/results` 在未完成时返回 `409`，完成后返回 `200`
6. `GET /jobs/{job_id}/output/download` 能下载非空 mp4
7. 前端能基于 `job_id` 完成一次完整任务链路展示

## 9. 已知限制

当前版本存在以下限制，交付时应明确给测试和前端：

- 只支持服务器本地绝对路径输入，不支持直接上传文件
- 任务进度当前只有粗粒度状态，`running` 时 `progress` 目前固定为中间值，不是细粒度采样进度
- 首次模型加载很慢，不适合把“自动拉起 engine”作为日常联调模式
- 四卡推理对 GPU 资源要求高，联调期间不建议与其他重任务混跑

## 10. 文档使用方式

建议团队内按下面方式使用文档：

- `VideoEditAPI.md`
  作为正式接口契约文档
- `VIDEO_EDIT_ACCEPTANCE.md`
  作为交付、测试、联调、验收说明
- `four_gpu_vace_command.md`
  作为四卡常驻 engine 启动手册

如果测试同事完成本文档第 8 节中的验收项，就可以通知前端按 `VideoEditAPI.md` 的接口契约合入。 
