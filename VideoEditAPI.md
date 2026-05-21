# 视频编辑服务 API 最终版

Base URL: `http://<host>:8880`

本文档定义对外 HTTP 接口、字段约束、状态枚举和错误码。  
内部实现约束同时固定如下：

- 服务端自动管理常驻四卡 VACE engine
- 首次请求可触发 engine 启动
- engine 未 ready 时请求进入等待或排队
- 默认任务策略固定为 `task=swap_anything`
- 默认参考图含背景，固定使用 `mode=bboxtrack,salient`
- 当前版本只支持服务器本地绝对路径输入

## 1. 接口总览

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/healthz` | 健康检查 |
| `POST` | `/api/v1/video-editing/jobs` | 创建异步视频编辑任务 |
| `GET` | `/api/v1/video-editing/jobs/{job_id}` | 查询任务状态 |
| `GET` | `/api/v1/video-editing/jobs/{job_id}/results` | 获取任务结果 |
| `GET` | `/api/v1/video-editing/jobs/{job_id}/output/download` | 下载输出视频 |

## 2. 通用约定

### 2.1 Content-Type

当前版本只支持：

- `application/json`

### 2.2 通用成功响应

```json
{
  "ok": true,
  "data": {}
}
```

### 2.3 通用失败响应

```json
{
  "ok": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "错误说明"
  }
}
```

### 2.4 时间格式

所有时间字段统一为 UTC ISO 8601，例如：

```text
2026-05-19T19:08:23Z
```

### 2.5 状态枚举

#### engine_state

- `stopped`
- `starting`
- `ready`
- `busy`
- `failed`

#### job_status

- `queued`
- `running`
- `done`
- `failed`
- `canceled`

### 2.6 路径约束

- `video_path` 必须是服务器可访问的绝对路径
- `reference_image_path` 必须是服务器可访问的绝对路径
- `output_name` 只能是文件名，不能带目录

## 3. 任务输入模型

### 3.1 CreateJobRequest

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `video_path` | `string` | 是 | — | 输入视频绝对路径 |
| `reference_image_path` | `string` | 是 | — | 参考图绝对路径 |
| `prompt` | `string` | 是 | — | 编辑提示词 |
| `bbox` | `array[int]` | 是 | — | 编辑框，格式固定为 `[x1, y1, x2, y2]` |
| `workspace_id` | `string` | 否 | `default` | 工作区标识 |
| `output_name` | `string` | 否 | 自动生成 | 输出文件名，必须以 `.mp4` 结尾 |
| `resolution` | `string` | 否 | `720p` | 枚举：`480p` / `720p` / `source` |
| `fps` | `int` | 否 | `source` | 输出帧率；若省略则跟随源视频 |
| `seed` | `int` | 否 | 随机 | 随机种子 |
| `callback_url` | `string` | 否 | `null` | 任务完成回调地址，当前版本可先预留不实现 |
| `client_request_id` | `string` | 否 | `null` | 调用方幂等标识，当前版本建议支持 |

### 3.2 字段规则

#### `bbox`

固定格式：

```json
[120, 80, 420, 560]
```

校验规则：

- 长度必须为 `4`
- 必须全部为整数
- 必须满足 `x1 < x2`
- 必须满足 `y1 < y2`
- 必须落在视频首帧范围内

#### `resolution`

取值：

- `480p`
- `720p`
- `source`

建议：

- 默认 `720p`
- 如果传 `source`，服务端需自行映射到当前支持尺寸；若不支持应直接报错，不做隐式缩放猜测

#### `output_name`

规则：

- 必须以 `.mp4` 结尾
- 不允许包含 `/`
- 不允许包含 `\`
- 不允许包含 `..`

## 4. 任务输出模型

### 4.1 JobError

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `string` | 是 | 错误码 |
| `message` | `string` | 是 | 对外错误信息 |

### 4.2 JobInputSummary

| 字段 | 类型 |
|---|---|
| `video_path` | `string` |
| `reference_image_path` | `string` |
| `prompt` | `string` |
| `bbox` | `array[int]` |
| `workspace_id` | `string` |
| `output_name` | `string \| null` |
| `resolution` | `string` |
| `fps` | `int \| null` |
| `seed` | `int \| null` |

### 4.3 JobOutputSummary

| 字段 | 类型 | 说明 |
|---|---|---|
| `output_dir` | `string \| null` | 任务输出目录 |
| `output_video_path` | `string \| null` | 最终输出视频 |
| `src_video_path` | `string \| null` | 预处理后的源视频 |
| `src_mask_path` | `string \| null` | 预处理后的 mask 视频 |
| `src_ref_image_paths` | `array[string] \| null` | 预处理后的参考图路径列表 |
| `output_download_url` | `string \| null` | 下载输出视频接口 |

### 4.4 JobStatusData

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | `string` | 任务 ID |
| `status` | `string` | `queued` / `running` / `done` / `failed` / `canceled` |
| `progress` | `float` | `0.0 ~ 1.0` |
| `queue_position` | `int \| null` | `0` 表示正在执行；`1+` 表示排队；无队列时为 `null` |
| `created_at` | `string` | 创建时间 |
| `started_at` | `string \| null` | 开始执行时间 |
| `finished_at` | `string \| null` | 结束时间 |
| `input` | `object` | 任务输入摘要 |
| `output` | `object` | 任务输出摘要 |
| `error` | `object \| null` | 失败时错误信息 |

## 5. 健康检查

### 5.1 请求

```http
GET /healthz
```

### 5.2 成功返回

```json
{
  "ok": true,
  "data": {
    "status": "ok",
    "engine_state": "ready",
    "engine": {
      "model_name": "vace-14B",
      "device_mode": "4gpu",
      "current_job_id": null,
      "started_at": "2026-05-19T19:00:03Z"
    },
    "queue": {
      "pending": 0
    }
  }
}
```

### 5.3 字段说明

| 字段 | 说明 |
|---|---|
| `status` | API 存活状态，固定 `ok` |
| `engine_state` | `stopped` / `starting` / `ready` / `busy` / `failed` |
| `engine.current_job_id` | 当前执行中的任务 ID |
| `queue.pending` | 排队中任务数，不含当前运行任务 |

## 6. 创建任务

### 6.1 请求

```http
POST /api/v1/video-editing/jobs
Content-Type: application/json
```

### 6.2 请求示例

```json
{
  "workspace_id": "my_project",
  "video_path": "/data/input/demo.mp4",
  "reference_image_path": "/data/reference/target.png",
  "prompt": "将框选区域中的人物替换成参考图中的人物，保持背景和其他区域不变",
  "bbox": [120, 80, 420, 560],
  "output_name": "demo_edited.mp4",
  "resolution": "720p",
  "fps": 16,
  "seed": 123456,
  "client_request_id": "req_20260519_001"
}
```

### 6.3 成功返回

HTTP 状态码：`202 Accepted`

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "queued",
    "queue_position": 1,
    "created_at": "2026-05-19T19:08:23Z",
    "status_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21",
    "results_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/results",
    "output_download_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/output/download"
  }
}
```

### 6.4 返回字段

| 字段 | 说明 |
|---|---|
| `job_id` | 全局唯一任务 ID，建议格式 `edit_job_<random_id>` |
| `status` | 初始固定为 `queued` |
| `queue_position` | 当前排队位置 |
| `status_url` | 状态查询接口 |
| `results_url` | 结果查询接口 |
| `output_download_url` | 下载输出视频接口 |

## 7. 查询任务状态

### 7.1 请求

```http
GET /api/v1/video-editing/jobs/{job_id}
```

### 7.2 返回示例：执行中

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "running",
    "progress": 0.62,
    "queue_position": 0,
    "created_at": "2026-05-19T19:08:23Z",
    "started_at": "2026-05-19T19:08:31Z",
    "finished_at": null,
    "input": {
      "video_path": "/data/input/demo.mp4",
      "reference_image_path": "/data/reference/target.png",
      "prompt": "将框选区域中的人物替换成参考图中的人物，保持背景和其他区域不变",
      "bbox": [120, 80, 420, 560],
      "workspace_id": "my_project",
      "output_name": "demo_edited.mp4",
      "resolution": "720p",
      "fps": 16,
      "seed": 123456
    },
    "output": {
      "output_dir": null,
      "output_video_path": null,
      "src_video_path": null,
      "src_mask_path": null,
      "src_ref_image_paths": null,
      "output_download_url": null
    },
    "error": null
  }
}
```

### 7.3 返回示例：完成

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "done",
    "progress": 1.0,
    "queue_position": null,
    "created_at": "2026-05-19T19:08:23Z",
    "started_at": "2026-05-19T19:08:31Z",
    "finished_at": "2026-05-19T19:10:12Z",
    "input": {
      "video_path": "/data/input/demo.mp4",
      "reference_image_path": "/data/reference/target.png",
      "prompt": "将框选区域中的人物替换成参考图中的人物，保持背景和其他区域不变",
      "bbox": [120, 80, 420, 560],
      "workspace_id": "my_project",
      "output_name": "demo_edited.mp4",
      "resolution": "720p",
      "fps": 16,
      "seed": 123456
    },
    "output": {
      "output_dir": "/data/output/jobs/edit_job_5f7f43f7f9738c21",
      "output_video_path": "/data/output/jobs/edit_job_5f7f43f7f9738c21/demo_edited.mp4",
      "src_video_path": "/data/output/jobs/edit_job_5f7f43f7f9738c21/processed/src_video.mp4",
      "src_mask_path": "/data/output/jobs/edit_job_5f7f43f7f9738c21/processed/src_mask.mp4",
      "src_ref_image_paths": [
        "/data/output/jobs/edit_job_5f7f43f7f9738c21/processed/src_ref_image_0.png"
      ],
      "output_download_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/output/download"
    },
    "error": null
  }
}
```

### 7.4 返回示例：失败

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "failed",
    "progress": 0.37,
    "queue_position": null,
    "created_at": "2026-05-19T19:08:23Z",
    "started_at": "2026-05-19T19:08:31Z",
    "finished_at": "2026-05-19T19:08:49Z",
    "input": {
      "video_path": "/data/input/demo.mp4",
      "reference_image_path": "/data/reference/target.png",
      "prompt": "将框选区域中的人物替换成参考图中的人物，保持背景和其他区域不变",
      "bbox": [120, 80, 420, 560],
      "workspace_id": "my_project",
      "output_name": "demo_edited.mp4",
      "resolution": "720p",
      "fps": 16,
      "seed": 123456
    },
    "output": {
      "output_dir": "/data/output/jobs/edit_job_5f7f43f7f9738c21",
      "output_video_path": null,
      "src_video_path": null,
      "src_mask_path": null,
      "src_ref_image_paths": null,
      "output_download_url": null
    },
    "error": {
      "code": "ENGINE_EXECUTION_FAILED",
      "message": "video editing engine failed during inference"
    }
  }
}
```

## 8. 获取结果

### 8.1 请求

```http
GET /api/v1/video-editing/jobs/{job_id}/results
```

### 8.2 成功返回

仅当任务状态为 `done` 时返回 `200 OK`。

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "done",
    "output": {
      "output_dir": "/data/output/jobs/edit_job_5f7f43f7f9738c21",
      "output_video_path": "/data/output/jobs/edit_job_5f7f43f7f9738c21/demo_edited.mp4",
      "src_video_path": "/data/output/jobs/edit_job_5f7f43f7f9738c21/processed/src_video.mp4",
      "src_mask_path": "/data/output/jobs/edit_job_5f7f43f7f9738c21/processed/src_mask.mp4",
      "src_ref_image_paths": [
        "/data/output/jobs/edit_job_5f7f43f7f9738c21/processed/src_ref_image_0.png"
      ],
      "output_download_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/output/download"
    }
  }
}
```

### 8.3 未完成返回

HTTP 状态码：`409 Conflict`

```json
{
  "ok": false,
  "error": {
    "code": "JOB_NOT_COMPLETED",
    "message": "job status is running"
  }
}
```

## 9. 下载输出视频

### 9.1 请求

```http
GET /api/v1/video-editing/jobs/{job_id}/output/download
```

### 9.2 成功返回

- HTTP 状态码：`200 OK`
- Content-Type：`video/mp4`
- Body：视频二进制

### 9.3 失败返回

```json
{
  "ok": false,
  "error": {
    "code": "OUTPUT_NOT_AVAILABLE",
    "message": "output video is not available"
  }
}
```

## 10. 参数校验规则

### 10.1 基础校验

- `video_path` 必须存在且可读取
- `reference_image_path` 必须存在且可读取
- `prompt` 去除首尾空白后不能为空
- `video_path` 和 `reference_image_path` 必须是绝对路径
- `output_name` 若存在必须符合文件名约束

### 10.2 bbox 校验

- `bbox` 长度必须为 `4`
- `bbox` 必须全部为整数
- `0 <= x1 < x2`
- `0 <= y1 < y2`
- `bbox` 不能超出视频首帧边界

### 10.3 业务校验

- `resolution` 必须在允许枚举内
- `fps` 若传入必须为正整数
- `seed` 若传入必须为整数

## 11. 错误码

| HTTP 状态码 | 错误码 | 说明 |
|---|---|---|
| `400` | `INVALID_ARGUMENT` | 请求字段格式错误 |
| `400` | `BBOX_OUT_OF_RANGE` | bbox 超出视频边界 |
| `404` | `FILE_NOT_FOUND` | 输入文件不存在 |
| `404` | `JOB_NOT_FOUND` | 任务不存在 |
| `404` | `OUTPUT_NOT_AVAILABLE` | 输出文件不存在 |
| `409` | `JOB_NOT_COMPLETED` | 任务尚未完成 |
| `409` | `ENGINE_NOT_READY` | engine 尚未 ready，且当前策略不允许等待 |
| `415` | `UNSUPPORTED_MEDIA` | 输入媒体格式不支持 |
| `429` | `QUEUE_FULL` | 队列已满 |
| `500` | `ENGINE_START_FAILED` | engine 启动失败 |
| `500` | `ENGINE_EXECUTION_FAILED` | engine 执行失败 |
| `500` | `INTERNAL_ERROR` | 服务内部异常 |

## 12. 幂等与排队建议

### 12.1 幂等

建议支持 `client_request_id`：

- 同一个调用方在短时间内重复提交相同 `client_request_id`
- 服务可直接返回已有 `job_id`
- 避免客户端重试时生成重复任务

### 12.2 队列

当前版本建议：

- 单队列
- 单任务执行
- 后续任务排队
- `queue_position=0` 表示正在执行

## 13. 结果目录建议

建议每个任务输出到独立目录：

- `/root/data/gzn/vace-video-edit/workspace/jobs/{job_id}/inputs`
- `/root/data/gzn/vace-video-edit/workspace/jobs/{job_id}/processed`
- `/root/data/gzn/vace-video-edit/workspace/jobs/{job_id}/results`
- `/root/data/gzn/vace-video-edit/workspace/jobs/{job_id}/job.json`

## 14. 调用流程

```text
1. POST /api/v1/video-editing/jobs
2. GET  /api/v1/video-editing/jobs/{job_id} 轮询
3. GET  /api/v1/video-editing/jobs/{job_id}/results
4. GET  /api/v1/video-editing/jobs/{job_id}/output/download
```

## 15. 一句话定义

该服务提供“输入原始视频、参考图、提示词和 bbox，异步执行局部视频编辑，并返回输出视频路径或下载地址”的标准化接口。

## 16. 服务启动方式

### 16.1 启动 HTTP API

在仓库根目录执行：

```bash
cd /root/data/gzn/vace-video-edit
source activate_vace.sh

python -m uvicorn video_edit_api:app --host 0.0.0.0 --port 8880
```

默认对外地址：

- `http://127.0.0.1:8880`

### 16.2 两种真机测试模式

#### 模式 A：先预热 engine，再测 HTTP API

适合先确认接口协议、状态码和输出回填是否正常。

- 先按 [four_gpu_vace_command.md](/root/data/gzn/vace-video-edit/four_gpu_vace_command.md) 单独启动 `run_edit_video_server.py`
- 等待 `/tmp/vace_wan_infer.sock` ready
- 再启动 `video_edit_api:app`
- 此时 `GET /healthz` 预期为 `ready` 或 `busy`

#### 模式 B：直接测自动拉起

适合确认“首次请求自动拉起四卡 engine”这条真实链路。

- 不手动启动 `run_edit_video_server.py`
- 只启动 `video_edit_api:app`
- 用第一次 `POST /api/v1/video-editing/jobs` 触发 engine 启动
- 此时 `GET /healthz` 允许经历 `stopped -> starting -> ready/busy`

## 17. 真机接口验收

### 17.1 固定测试素材

- `video_path`: `/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4`
- `reference_image_path`: `/root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png`
- `ckpt_dir`: `/root/data/gzn/vace-video-edit/models/Wan2.1-VACE-14B`

### 17.2 建议请求体

```json
{
  "workspace_id": "real_api_validation",
  "video_path": "/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4",
  "reference_image_path": "/root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png",
  "prompt": "镜头中的人物改成参考图的人物，其他不变",
  "bbox": [421, 0, 826, 535],
  "output_name": "real_api_validation.mp4",
  "resolution": "720p",
  "client_request_id": "real-api-validation-001"
}
```

### 17.3 建议验证顺序

如果当前 shell 配了代理，访问本地服务时建议额外带上 `--noproxy '*'`，或者先清掉 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。

#### 第一步：健康检查

```bash
curl --noproxy '*' http://127.0.0.1:8880/healthz
```

通过标准：

- 返回 `200`
- `ok=true`
- 预热模式下 `engine_state` 为 `ready` 或 `busy`
- 自动拉起模式下，建任务前允许为 `stopped`

#### 第二步：创建真实任务

```bash
curl --noproxy '*' \
  -X POST http://127.0.0.1:8880/api/v1/video-editing/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "workspace_id": "real_api_validation",
    "video_path": "/root/data/gzn/vace-video-edit/workspace/inputs/videos/scene_01_shot_02.mp4",
    "reference_image_path": "/root/data/gzn/vace-video-edit/workspace/inputs/images/nezha.png",
    "prompt": "镜头中的人物改成参考图的人物，其他不变",
    "bbox": [421, 0, 826, 535],
    "output_name": "real_api_validation.mp4",
    "resolution": "720p",
    "client_request_id": "real-api-validation-001"
  }'
```

通过标准：

- 返回 `202`
- 返回体含 `job_id`
- 返回体含 `status_url` / `results_url` / `output_download_url`

#### 第三步：未完成时取结果

```bash
curl --noproxy '*' http://127.0.0.1:8880/api/v1/video-editing/jobs/<job_id>/results
```

通过标准：

- 若任务尚未完成，返回 `409`
- 错误码为 `JOB_NOT_COMPLETED`

#### 第四步：轮询任务状态

```bash
curl --noproxy '*' http://127.0.0.1:8880/api/v1/video-editing/jobs/<job_id>
```

通过标准：

- 能看到 `queued` / `running` / `done` 中至少一种真实状态
- 完成后 `output.output_video_path` 非空
- 失败时 `error.code` 与 `error.message` 非空

#### 第五步：获取结果和下载视频

```bash
curl --noproxy '*' http://127.0.0.1:8880/api/v1/video-editing/jobs/<job_id>/results
curl --noproxy '*' -OJ http://127.0.0.1:8880/api/v1/video-editing/jobs/<job_id>/output/download
```

通过标准：

- `/results` 在任务完成后返回 `200`
- `/output/download` 返回 `200`
- 下载响应 `Content-Type` 为 `video/mp4`
- 下载文件非空且文件名与输出一致

### 17.4 关键错误路径

除 happy path 外，建议固定再测以下错误场景：

- 相对 `video_path`，预期 `400 INVALID_ARGUMENT`
- 不存在的输入视频或参考图，预期 `404 FILE_NOT_FOUND`
- 越界 `bbox`，预期 `400 BBOX_OUT_OF_RANGE`
- 不存在的 `job_id` 查状态，预期 `404 JOB_NOT_FOUND`
- 不存在的 `job_id` 查结果，预期 `404 JOB_NOT_FOUND`

### 17.5 真机验收记录

建议至少记录：

- HTTP API 启动时间
- engine 启动开始时间与 ready 时间
- `job_id`
- 任务 `created_at` / `started_at` / `finished_at`
- 最终 `output_video_path`
- 下载文件大小
- 任一失败时的 HTTP 状态码、错误码和错误消息
