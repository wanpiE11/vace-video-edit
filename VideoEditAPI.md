# 视频编辑服务 API 文档

Base URL: `http://<host>:8880`

## 接口总览

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/healthz` | 健康检查 |
| `POST` | `/api/v1/video-editing/jobs` | 创建视频编辑任务 |
| `GET` | `/api/v1/video-editing/jobs/{job_id}` | 查询任务状态 |
| `GET` | `/api/v1/video-editing/jobs/{job_id}/results` | 获取编辑结果路径 |
| `GET` | `/api/v1/video-editing/jobs/{job_id}/output/download` | 下载编辑后视频 |

## 通用返回格式

成功:

```json
{ "ok": true, "data": { ... } }
```

失败:

```json
{ "ok": false, "error": { "code": "ERROR_CODE", "message": "错误说明" } }
```

## 固定约定

为避免后端实现和客户端对接时反复调整，当前版本固定如下：

- 仅支持 `application/json`
- `video_path` 和 `reference_image_path` 必须是服务器可访问的绝对路径
- `bbox` 仅支持数组格式 `[x1, y1, x2, y2]`
- 输出视频格式固定为 `mp4`
- 任务状态固定为 `queued` / `running` / `done` / `failed`
- 成功响应固定使用 `ok + data`，失败响应固定使用 `ok + error`

---

## 1. 健康检查

```http
GET /healthz
```

```bash
curl http://127.0.0.1:8880/healthz
```

```json
{
  "ok": true,
  "data": {
    "status": "ok",
    "engine_pool": "ready",
    "job_queue": { "current_job": null, "pending": 0 }
  }
}
```

`engine_pool` 取值: `not_initialized` / `loading` / `ready`。建议等到 `ready` 再提交任务。

---

## 2. 创建视频编辑任务

```http
POST /api/v1/video-editing/jobs
```

该接口用于创建一个视频局部编辑任务。任务创建后立即进入后台执行，客户端通过 `job_id` 轮询状态并获取结果。

当前版本固定为：

- `application/json`: 仅支持传服务器本地文件路径

### 2.1 application/json

Content-Type: `application/json`

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `video_path` | `string` | 是 | — | 待编辑视频服务器本地绝对路径 |
| `reference_image_path` | `string` | 是 | — | 参考图服务器本地绝对路径 |
| `prompt` | `string` | 是 | — | 编辑提示词 |
| `bbox` | `array[int]` | 是 | — | 编辑区域，固定格式为 `[x1, y1, x2, y2]` |
| `workspace_id` | `string` | 否 | `default` | 工作区 ID |
| `output_name` | `string` | 否 | 自动生成 | 输出文件名，仅文件名，不允许包含目录 |
| `resolution` | `string` | 否 | `source` | 输出分辨率，枚举值：`source` / `480p` / `720p` |
| `fps` | `int` | 否 | `source` | 输出帧率，必须为正整数；默认跟随源视频 |
| `seed` | `int` | 否 | 随机 | 随机种子 |
| `edit_strength` | `float` | 否 | `1.0` | 编辑强度，范围 `0.0 ~ 1.0` |

`bbox` 固定格式:

```json
[120, 80, 420, 560]
```

**请求示例:**

```bash
curl -X POST "http://127.0.0.1:8880/api/v1/video-editing/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "my_project",
    "video_path": "/data/input/demo.mp4",
    "reference_image_path": "/data/reference/target.png",
    "prompt": "将框选区域中的人物衣服替换为白色西装，保持其他内容不变",
    "bbox": [120, 80, 420, 560],
    "output_name": "demo_edited.mp4",
    "resolution": "source",
    "fps": 16,
    "seed": 123456,
    "edit_strength": 1.0
  }'
```

### 返回

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "queued",
    "status_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21",
    "results_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/results",
    "output_download_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/output/download"
  }
}
```

`job_id` 建议格式为 `edit_job_<random_id>`，在服务内全局唯一。

### 参数约束

- `video_path`、`reference_image_path` 必须是绝对路径
- `prompt` 去除首尾空白后不能为空
- `bbox` 长度必须为 `4`
- `bbox[0] < bbox[2]`，`bbox[1] < bbox[3]`
- `bbox` 坐标必须落在视频帧范围内
- `output_name` 若传入，必须以 `.mp4` 结尾
- `output_name` 不允许包含 `/`、`..` 等路径片段

### 失败示例

HTTP 状态码：`400 Bad Request`

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "bbox must be [x1, y1, x2, y2] and within frame bounds"
  }
}
```

---

## 3. 查询任务状态

```http
GET /api/v1/video-editing/jobs/{job_id}
```

```bash
curl "http://127.0.0.1:8880/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21"
```

**返回（执行中）:**

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "running",
    "progress": 0.62,
    "queue_position": 0,
    "created_at": "2026-05-19T18:30:12Z",
    "started_at": "2026-05-19T18:30:14Z",
    "finished_at": null,
    "input": {
      "video_path": "/data/input/demo.mp4",
      "reference_image_path": "/data/reference/target.png",
      "prompt": "将框选区域中的人物衣服替换为白色西装，保持其他内容不变",
      "bbox": [120, 80, 420, 560]
    },
    "output": {
      "output_video_path": null,
      "output_download_url": null
    },
    "error": null
  }
}
```

**返回（完成）:**

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "done",
    "progress": 1.0,
    "queue_position": null,
    "created_at": "2026-05-19T18:30:12Z",
    "started_at": "2026-05-19T18:30:14Z",
    "finished_at": "2026-05-19T18:31:03Z",
    "input": {
      "video_path": "/data/input/demo.mp4",
      "reference_image_path": "/data/reference/target.png",
      "prompt": "将框选区域中的人物衣服替换为白色西装，保持其他内容不变",
      "bbox": [120, 80, 420, 560]
    },
    "output": {
      "output_video_path": "/data/output/demo_edited.mp4",
      "output_download_url": "/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/output/download"
    },
    "error": null
  }
}
```

**返回（失败）:**

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "failed",
    "progress": 0.37,
    "queue_position": null,
    "created_at": "2026-05-19T18:30:12Z",
    "started_at": "2026-05-19T18:30:14Z",
    "finished_at": "2026-05-19T18:30:29Z",
    "input": {
      "video_path": "/data/input/demo.mp4",
      "reference_image_path": "/data/reference/target.png",
      "prompt": "将框选区域中的人物衣服替换为白色西装，保持其他内容不变",
      "bbox": [120, 80, 420, 560]
    },
    "output": {
      "output_video_path": null,
      "output_download_url": null
    },
    "error": {
      "code": "ENGINE_EXECUTION_FAILED",
      "message": "video editing engine failed during inference"
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| `status` | `queued` → `running` → `done` / `failed` |
| `progress` | `0.0 ~ 1.0` |
| `queue_position` | `0` = 正在执行，`1+` = 排队中，`null` = 不在队列 |
| `created_at` | 任务创建时间，UTC ISO 8601 |
| `started_at` | 开始执行时间，未开始时为 `null` |
| `finished_at` | 完成或失败时间，未结束时为 `null` |
| `input.bbox` | 服务端最终采用的编辑区域 |
| `output.output_video_path` | 编辑完成后的视频绝对路径 |
| `output.output_download_url` | 可下载结果视频的接口地址 |
| `error` | 任务失败时的错误信息 |

**轮询示例:**

```bash
JOB_ID="edit_job_5f7f43f7f9738c21"
while true; do
  STATUS=$(curl -s "http://127.0.0.1:8880/api/v1/video-editing/jobs/${JOB_ID}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['status'])")
  echo "status: $STATUS"
  [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ] && break
  sleep 5
done
```

---

## 4. 获取编辑结果路径

```http
GET /api/v1/video-editing/jobs/{job_id}/results
```

该接口返回编辑任务的输入摘要和最终结果路径，便于后续评估、归档或下游处理。

仅当任务状态为 `done` 时返回成功；若任务尚未完成，建议返回 `409 Conflict`。

```bash
curl "http://127.0.0.1:8880/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/results"
```

**返回:**

```json
{
  "ok": true,
  "data": {
    "job_id": "edit_job_5f7f43f7f9738c21",
    "status": "done",
    "output_dir": "/data/output/jobs/edit_job_5f7f43f7f9738c21",
    "input": {
      "video_path": "/data/input/demo.mp4",
      "reference_image_path": "/data/reference/target.png",
      "prompt": "将框选区域中的人物衣服替换为白色西装，保持其他内容不变",
      "bbox": [120, 80, 420, 560]
    },
    "result": {
      "output_video_path": "/data/output/jobs/edit_job_5f7f43f7f9738c21/demo_edited.mp4",
      "preview_frame_path": "/data/output/jobs/edit_job_5f7f43f7f9738c21/preview.jpg"
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| `output_dir` | 当前任务的输出目录 |
| `result.output_video_path` | 编辑后视频绝对路径 |
| `result.preview_frame_path` | 可选，首帧或对比预览图路径；若未生成可为 `null` |

失败示例:

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

---

## 5. 下载编辑后视频

```http
GET /api/v1/video-editing/jobs/{job_id}/output/download
```

```bash
curl -o output.mp4 \
  "http://127.0.0.1:8880/api/v1/video-editing/jobs/edit_job_5f7f43f7f9738c21/output/download"
```

返回 `video/mp4` 文件。若视频尚未生成，返回 `404`。

失败示例:

```json
{
  "ok": false,
  "error": {
    "code": "OUTPUT_NOT_AVAILABLE",
    "message": "编辑后视频尚未生成"
  }
}
```

---

## 参数校验建议

后端建议至少校验以下内容：

- `video_path` 必须存在且可读取
- `reference_image_path` 必须存在且可读取
- `prompt` 不能为空
- `bbox` 必须是长度为 4 的有效坐标，且满足 `0 <= x1 < x2`、`0 <= y1 < y2`
- `bbox` 不得超出视频帧范围
- `video_path` 和 `reference_image_path` 必须是绝对路径
- `output_name` 若传入，只能是文件名，且必须以 `.mp4` 结尾
- `resolution`、`fps`、`edit_strength` 如传入，必须在允许范围内

建议错误码与 HTTP 状态码：

| HTTP 状态码 | 错误码 | 说明 |
|---|---|---|
| `400` | `INVALID_ARGUMENT` | 参数格式错误 |
| `400` | `BBOX_OUT_OF_RANGE` | bbox 超出视频范围 |
| `404` | `FILE_NOT_FOUND` | 输入文件不存在 |
| `404` | `JOB_NOT_FOUND` | job_id 不存在 |
| `404` | `OUTPUT_NOT_AVAILABLE` | 输出文件尚未生成 |
| `409` | `JOB_NOT_COMPLETED` | 任务尚未完成 |
| `409` | `ENGINE_NOT_READY` | 推理引擎未就绪 |
| `415` | `UNSUPPORTED_MEDIA` | 文件格式不支持 |
| `500` | `ENGINE_EXECUTION_FAILED` | 推理执行失败 |
| `500` | `INTERNAL_ERROR` | 服务内部错误 |

---

## 完整调用流程

### 简单流程

```text
1. POST .../jobs              → 拿到 job_id
2. GET  .../jobs/{job_id}     → 轮询至 status=done
3. GET  .../jobs/{job_id}/results
4. GET  .../output/download   → 下载编辑后视频
```

### 本地路径模式

```text
1. 客户端准备 video_path / reference_image_path / prompt / bbox
2. POST .../jobs（application/json）
3. GET  .../jobs/{job_id} 轮询任务状态
4. GET  .../jobs/{job_id}/results 获取 output_video_path
```

---

## 一句话定义

该服务提供“输入原始视频、参考图、提示词和 bbox，异步执行局部视频编辑，并输出编辑后视频”的标准化接口。
