# 视频编辑模块交付说明

本文档用于把当前视频编辑模块交给测试同事和前端同事联调使用。

- 对外接口契约文档：[`VideoEditAPI.md`](/root/data/gzn/vace-video-edit/VideoEditAPI.md)
- 四卡常驻 engine 启动说明：[`four_gpu_vace_command.md`](/root/data/gzn/vace-video-edit/four_gpu_vace_command.md)
- 单次命令行运行说明：[`RUN_EDIT_VIDEO.md`](/root/data/gzn/vace-video-edit/docs_inactive/RUN_EDIT_VIDEO.md)

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

当前机器 IP 每次重启后可能变化，因此本文档不再写死某一个内网 IP。

建议先在服务器上动态获取当前 IP：

```bash
hostname -I
```

如果返回多个地址，默认取第一个内网地址作为联调地址。

建议联调地址：

- `http://<当前服务器IP>:8880`

本机地址：

- `http://127.0.0.1:8880`

说明：

- 端口固定按当前文档使用 `8880`
- 启动 API 时请使用 `--host 0.0.0.0`
- 如果 shell 设置了代理，测试本机或内网地址时建议带 `--noproxy '*'`
- 建议联调前先执行 `export HOST_IP=$(hostname -I | awk '{print $1}')`
- 建议后续命令统一使用 `export BASE_URL=http://$HOST_IP:8880`

## 3. 建议交付方式

建议测试和前端联调统一采用“先启动 HTTP API，再调用模型加载接口预热 engine”的模式。

原因：

- 更稳定
- API 自己知道当前模型加载状态
- `healthz` 和 `/engine` 状态更容易观察
- 避免首个任务触发长时间模型加载，影响前端联调体验
- 更接近后续线上常驻服务形态

## 4. 启动步骤

### 4.1 前置条件

启动前应确认：

- 机器具备 4 张可用 GPU
- 模型目录存在：`/root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B`
- 默认测试视频存在：`/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4`
- 默认参考图存在：`/root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png`

### 4.2 启动 HTTP API

另开一个 shell：

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh

python -m uvicorn video_edit_api:app --host 0.0.0.0 --port 8880
```

启动后，对外联调地址为：

```text
http://<当前服务器IP>:8880
```

### 4.3 用 tmux 常驻启动

如果希望退出 SSH 后服务继续运行，建议用一个 `tmux` 会话启动 `API`。

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

模型加载改由 API 接口触发，不再要求单独常驻启动 `engine`。

只退出 `tmux` 会话但保持进程继续运行：

```bash
Ctrl+b d
```

重新进入会话：

```bash
tmux attach -t vace-api
```

查看当前 `tmux` 会话：

```bash
tmux ls
```

### 4.5 查看是否已经启动

查看 API 进程：

```bash
ps -ef | grep "python -m uvicorn video_edit_api:app" | grep -v grep
```

查看 `8880` 端口监听：

```bash
ss -ltnp | grep :8880
```

查看 engine 状态：

```bash
curl --noproxy '*' http://127.0.0.1:8880/api/v1/video-editing/engine
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
tmux kill-session -t vace-api
```

如果要按进程名终止：

```bash
pkill -f "python -m uvicorn video_edit_api:app"
```

如果普通终止没有生效，再强制终止：

```bash
pkill -9 -f "python -m uvicorn video_edit_api:app"
```

说明：

- 优先使用 `Ctrl+C` 或 `pkill -f`，避免直接强杀。
- 强制终止会直接打断当前推理中的任务。
- 当前服务没有“取消单个任务”接口；停掉 API 会影响当前内存里的全部任务，并中断它所管理的 engine。

### 4.7 自动健康检查和重启

如果希望 API 服务异常退出或健康检查失败后自动恢复，可以使用仓库内的 watchdog 脚本：

```bash
cd /root/data/gzn/vace-video-edit
chmod +x watch_video_edit_api.sh

tmux new -s vace-api-watchdog
./watch_video_edit_api.sh
```

只退出 `tmux` 会话但保持 watchdog 继续运行：

```bash
Ctrl+b d
```

watchdog 默认行为：

- 启动并管理 `python -m uvicorn video_edit_api:app --host 0.0.0.0 --port 8880`
- 如果该 API 已经在运行，会先接管已有进程，不重复启动
- API 启动或重启后，等待 `/healthz` 可用，然后自动调用 `/api/v1/video-editing/engine/load` 触发模型加载
- 每 `30` 秒请求一次 `http://127.0.0.1:8880/healthz`
- 连续 `2` 次健康检查失败后重启 API
- `/healthz` 返回 `engine_state=failed` 时也会触发重启
- 重启时先发送 `SIGTERM`，等待 `30` 秒后仍未退出再发送 `SIGKILL`

watchdog 日志：

```bash
cd /root/data/gzn/vace-video-edit
tail -f logs/video_edit_watchdog.log
```

watchdog 启动的 API stdout/stderr：

```bash
cd /root/data/gzn/vace-video-edit
tail -f logs/video_edit_api_watchdog_service.log
```

重新进入 watchdog 会话：

```bash
tmux attach -t vace-api-watchdog
```

停止 watchdog：

```bash
tmux kill-session -t vace-api-watchdog
```

说明：

- watchdog 停止后，不会主动停止已经启动的 API 进程。
- 如需关闭自动模型加载，可设置 `AUTO_LOAD_ENGINE_AFTER_START=0`。
- 如果希望停止 API，仍按第 4.6 节使用 `Ctrl+C`、`tmux kill-session -t vace-api` 或 `pkill -f "python -m uvicorn video_edit_api:app"`。
- 重启 API 会中断当前正在推理的任务，并丢失 API 进程内存中的任务状态。
- 可通过环境变量调整配置，例如：

```bash
CHECK_INTERVAL_SECONDS=10 MAX_CONSECUTIVE_FAILURES=3 AUTO_LOAD_ENGINE_AFTER_START=1 ./watch_video_edit_api.sh
```

## 5. 调用说明

完整接口字段、状态码和错误码定义，以 [`VideoEditAPI.md`](/root/data/gzn/vace-video-edit/VideoEditAPI.md) 为准。

这里给测试同事和前端同事一套最小可用调用流程。

### 5.1 健康检查

先设置联调基地址：

```bash
export HOST_IP=$(hostname -I | awk '{print $1}')
export BASE_URL=http://$HOST_IP:8880
```

```bash
curl --noproxy '*' "$BASE_URL/healthz"
```

期望：

- HTTP `200`
- `ok=true`
- 建任务前允许 `engine_state=stopped`

### 5.2 触发模型加载

```bash
curl --noproxy '*' -X POST "$BASE_URL/api/v1/video-editing/engine/load"
```

期望：

- 首次返回 HTTP `202`
- 返回 `state`
- 返回 `phase`
- 返回 `progress`
- 返回 `status_url`

### 5.3 轮询 engine 状态

```bash
curl --noproxy '*' "$BASE_URL/api/v1/video-editing/engine"
```

期望：

- 最终 `state=ready`
- `phase=ready`
- `progress=1.0`
- `ready_at` 非空

### 5.4 创建任务

```bash
curl --noproxy '*' \
  -X POST "$BASE_URL/api/v1/video-editing/jobs" \
  -H 'Content-Type: application/json' \
  -d '{
    "workspace_id": "frontend_integration",
    "video_path": "/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4",
    "reference_image_path": "/root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png",
    "prompt": "镜头中的人物改成参考图的人物，其他不变",
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

### 5.5 轮询任务状态

```bash
curl --noproxy '*' \
  "$BASE_URL/api/v1/video-editing/jobs/<job_id>"
```

期望：

- 任务状态会经历 `queued` 或 `running`
- 完成后状态为 `done`
- 完成后 `output.output_video_path` 非空

### 5.6 未完成时取结果

```bash
curl --noproxy '*' \
  "$BASE_URL/api/v1/video-editing/jobs/<job_id>/results"
```

期望：

- 若任务尚未完成，返回 HTTP `409`
- 错误码为 `JOB_NOT_COMPLETED`

### 5.7 获取结果

```bash
curl --noproxy '*' \
  "$BASE_URL/api/v1/video-editing/jobs/<job_id>/results"
```

期望：

- 任务完成后返回 HTTP `200`
- 返回 `output_dir`
- 返回 `output_video_path`
- 返回 `src_video_path`
- 返回 `src_mask_path`
- 返回 `src_ref_image_paths`

### 5.8 下载输出视频

```bash
curl --noproxy '*' -OJ \
  "$BASE_URL/api/v1/video-editing/jobs/<job_id>/output/download"
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

## 8. 日志与排障

服务启动和任务执行后，会在服务器本地写日志。当前不通过 HTTP 接口暴露日志，避免泄露内部路径、命令行和 traceback。

### 8.1 日志位置

- API 和 service 主日志：`/root/data/gzn/vace-video-edit/logs/video_edit_api.log`
- engine daemon 日志：`/root/data/gzn/vace-video-edit/logs/video_edit_engine.log`
- 单任务日志：`/root/data/gzn/vace-video-edit/workspace/jobs/<job_id>/logs/job.log`

说明：

- `video_edit_api.log` 按大小轮转，单文件约 `50MB`，保留 `5` 个备份。
- `video_edit_engine.log` 记录常驻 engine 的 stdout/stderr。
- `job.log` 记录单个任务的状态、执行命令、`run_edit_video.py` 完整 stdout/stderr、成功输出或失败 traceback。

### 8.2 常用查看命令

查看 API/service 主日志：

```bash
cd /root/data/gzn/vace-video-edit
tail -f logs/video_edit_api.log
```

查看 engine daemon 日志：

```bash
cd /root/data/gzn/vace-video-edit
tail -f logs/video_edit_engine.log
```

查看单个任务日志：

```bash
cd /root/data/gzn/vace-video-edit
tail -f workspace/jobs/<job_id>/logs/job.log
```

按 `job_id` 回查主日志：

```bash
cd /root/data/gzn/vace-video-edit
grep "<job_id>" logs/video_edit_api.log
```

查看最近错误：

```bash
cd /root/data/gzn/vace-video-edit
grep -E "ERROR|WARNING|failed|traceback" logs/video_edit_api.log
```

### 8.3 排障顺序

如果 `POST /jobs` 直接失败：

- 先看 HTTP 响应里的 `error.code`
- 再看 `logs/video_edit_api.log`
- 常见原因是参数校验、文件路径不存在、bbox 超出视频尺寸

如果 engine 长时间停在 `starting`：

- 先看 `GET /api/v1/video-editing/engine` 的 `phase`、`progress`、`last_error`
- 再看 `logs/video_edit_engine.log`
- 常见原因是模型目录错误、GPU 资源不足、daemon 启动失败、socket 长时间没有 ready

如果任务状态变成 `failed`：

- 先看 `GET /api/v1/video-editing/jobs/<job_id>` 的 `error.code` 和 `error.message`
- 再看 `workspace/jobs/<job_id>/logs/job.log`
- 最后用 `grep "<job_id>" logs/video_edit_api.log` 回查 service 状态变化

如果 `/results` 成功但下载失败：

- 确认任务状态已经是 `done`
- 查看 `/results` 返回的 `output.output_video_path`
- 检查该文件是否存在、是否非空
- 回查 `workspace/jobs/<job_id>/logs/job.log` 中的输出路径和 `run_edit_video.py` stdout/stderr

### 8.4 常见错误码对应日志

- `INVALID_ARGUMENT`：看 `video_edit_api.log`，重点检查请求字段、路径是否绝对路径、`output_name` 是否只是文件名。
- `BBOX_OUT_OF_RANGE`：看 `video_edit_api.log`，重点检查 bbox 是否超过输入视频宽高。
- `FILE_NOT_FOUND`：看 `video_edit_api.log`，重点检查 `video_path` 或 `reference_image_path` 是否存在。
- `ENGINE_START_FAILED`：看 `video_edit_engine.log`，重点检查模型加载、GPU、daemon 进程和 socket。
- `ENGINE_EXECUTION_FAILED`：看 `workspace/jobs/<job_id>/logs/job.log`，重点检查预处理、socket 调用、推理输出和结果解析。
- `OUTPUT_NOT_AVAILABLE`：看任务状态和 `job.log`，重点确认任务是否完成、输出路径是否生成、文件是否被删除。

## 9. 验收标准

测试同事联调通过后，可认为模块达到前端合入条件的最低标准：

1. API 服务能按本文档启动
2. `GET /healthz` 返回正常
3. `POST /jobs` 能成功创建任务
4. `GET /jobs/{job_id}` 能正常轮询到 `done`
5. `GET /jobs/{job_id}/results` 在未完成时返回 `409`，完成后返回 `200`
6. `GET /jobs/{job_id}/output/download` 能下载非空 mp4
7. 前端能基于 `job_id` 完成一次完整任务链路展示
8. 出现失败时，能按第 8 节日志说明定位到 API 参数、engine 启动或单任务执行问题

## 10. 已知限制

当前版本存在以下限制，交付时应明确给测试和前端：

- 只支持服务器本地绝对路径输入，不支持直接上传文件
- 任务进度当前只有粗粒度状态，`running` 时 `progress` 目前固定为中间值，不是细粒度采样进度
- 首次模型加载很慢，建议先调用 `/api/v1/video-editing/engine/load`，待 `ready` 后再提任务
- 四卡推理对 GPU 资源要求高，联调期间不建议与其他重任务混跑

## 11. 文档使用方式

建议团队内按下面方式使用文档：

- `VideoEditAPI.md`
  作为正式接口契约文档
- `VIDEO_EDIT_ACCEPTANCE.md`
  作为交付、测试、联调、验收说明
- `four_gpu_vace_command.md`
  作为底层 engine 启动与排障参考，不是默认联调主流程

如果测试同事完成本文档第 9 节中的验收项，就可以通知前端按 `VideoEditAPI.md` 的接口契约合入。
