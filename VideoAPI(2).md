# 视频生成服务 API 文档

Base URL: `http://<host>:8880`

## 接口总览

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/healthz` | 健康检查 |
| `POST` | `/api/v1/video-generation/jobs` | 创建视频生成任务 |
| `POST` | `/api/v1/video-generation/prompt-jobs` | 通过 prompt 创建单镜头视频生成任务 |
| `GET` | `/api/v1/video-generation/jobs/{job_id}` | 查询任务状态 |
| `GET` | `/api/v1/video-generation/jobs/{job_id}/results` | 获取各镜头视频文件路径 |
| `GET` | `/api/v1/video-generation/jobs/{job_id}/output/download` | 下载完整视频 (MP4) |
| `GET` | `/api/v1/video-generation/jobs/{job_id}/output/enhanced/download` | 下载增强版视频 (含字幕/配音/BGM) |
| `POST` | `/api/v1/video-generation/jobs/{job_id}/shots/regenerate` | 重新生成指定镜头 |
| `POST` | `/api/v1/video-generation/jobs/{job_id}/concat` | 按评分拼接完整视频 |

## 通用返回格式

成功:

```json
{ "ok": true, "data": { ... } }
```

失败:

```json
{ "ok": false, "error": { "code": "ERROR_CODE", "message": "错误说明" } }
```

---

## 1. 健康检查

```
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

## 2. 创建视频生成任务

```
POST /api/v1/video-generation/jobs
```

Content-Type: `multipart/form-data`

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `workspace_id` | `string` | 是 | — | 工作区 ID |
| `storyboard` | `file` | 二选一 | — | 上传 storyboard JSON 文件 |
| `storyboard_path` | `string` | 二选一 | — | 服务器本地 storyboard JSON 路径 |
| `resolution` | `string` | 是 | — | 分辨率: `360p` / `480p` / `720p` |
| `fps` | `string` | 否 | `16` | 帧率 |
| `style` | `string` | 否 | `realistic` | 风格 |
| `watermark` | `boolean` | 否 | `true` | 是否给生成的 shot 和拼接后的完整视频添加项目水印 |

**请求示例:**

```bash
# 方式 A: 上传文件
curl -X POST "http://127.0.0.1:8880/api/v1/video-generation/jobs" \
  -F "workspace_id=my_project" \
  -F "storyboard=@storyboard.json" \
  -F "resolution=720p" \
  -F "watermark=true"

# 方式 B: 传服务器路径
curl -X POST "http://127.0.0.1:8880/api/v1/video-generation/jobs" \
  -F "workspace_id=my_project" \
  -F "storyboard_path=/data/storyboard.json" \
  -F "resolution=720p"
```

**返回:**

```json
{
  "ok": true,
  "data": {
    "job_id": "job_5f7f43f7f9738c21",
    "status_url": "/api/v1/video-generation/jobs/job_5f7f43f7f9738c21",
    "results_url": "/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/results",
    "output_download_url": "/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/output/download",
    "package_download_url": "/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/package"
  }
}
```

任务创建后立即进入后台执行。使用返回的 `job_id` 轮询状态。

---

## 3. 通过 prompt 创建视频生成任务

```
POST /api/v1/video-generation/prompt-jobs
```

Content-Type: `application/json`

该接口会把传入的 prompt 包装成一个单场景、单镜头 storyboard, 然后复用普通任务队列。`duration_sec` 必须显式传入, 不传会返回参数校验错误。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `workspace_id` | `string` | 是 | — | 工作区 ID |
| `prompt` | `string` | 是 | — | 视频生成正向 prompt |
| `negative_prompt` | `string` | 否 | `null` | negative prompt; Turbo engine 当前会忽略该字段 |
| `resolution` | `string` | 是 | — | 分辨率: `360p` / `480p` / `720p` |
| `duration_sec` | `number` | 是 | — | 视频时长, 必须大于 0 |
| `fps` | `int` | 否 | `16` | 帧率, 必须为正整数 |
| `style` | `string` | 否 | `realistic` | 风格 |
| `watermark` | `boolean` | 否 | `true` | 是否给生成的 shot 和拼接后的完整视频添加项目水印 |

**请求示例:**

```bash
curl -X POST "http://127.0.0.1:8880/api/v1/video-generation/prompt-jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "my_project",
    "prompt": "清晨阳光照进安静书房,木质书桌上有一本翻开的书和一杯冒着热气的咖啡,真实摄影质感,镜头缓慢推进",
    "negative_prompt": "文字水印,模糊,抖动",
    "resolution": "720p",
    "duration_sec": 3,
    "fps": 16,
    "watermark": false
  }'
```

**返回:** 与创建 storyboard 任务相同, 包含 `job_id` 和状态/结果/下载 URL。

---

## 4. 查询任务状态

```
GET /api/v1/video-generation/jobs/{job_id}
```

```bash
curl "http://127.0.0.1:8880/api/v1/video-generation/jobs/job_5f7f43f7f9738c21"
```

**返回 (执行中):**

```json
{
  "ok": true,
  "data": {
    "job_id": "job_5f7f43f7f9738c21",
    "status": "running",
    "progress": 0.3333,
    "queue_position": 0,
    "assets": [
      {
        "id": "scene_01_shot_01",
        "name": "scene_01_shot_01.mp4",
        "duration": "00:03",
        "resolution": "1280x720",
        "size": "714.4 KB",
        "status": "done"
      },
      {
        "id": "scene_01_shot_02",
        "name": "scene_01_shot_02.mp4",
        "duration": "00:00",
        "resolution": "1280x720",
        "size": "0 B",
        "status": "pending"
      }
    ],
    "enhanced_video_download_url": null,
    "enhanced_video": null,
    "error": null
  }
}
```

| 字段 | 说明 |
|---|---|
| `status` | `queued` → `running` → `done` / `failed` |
| `progress` | `0.0 ~ 1.0` |
| `queue_position` | `0` = 正在执行, `1+` = 排队中, `null` = 不在队列 |
| `assets[*].status` | 每个镜头: `pending` / `done` / `failed` |
| `enhanced_video_download_url` | 增强版视频 (含字幕/配音/BGM) 的下载链接; 若不存在则为 `null` |
| `enhanced_video` | 增强版视频的服务器绝对路径; 若不存在则为 `null` |

**轮询示例:**

```bash
JOB_ID="job_5f7f43f7f9738c21"
while true; do
  STATUS=$(curl -s "http://127.0.0.1:8880/api/v1/video-generation/jobs/${JOB_ID}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['status'])")
  echo "status: $STATUS"
  [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ] && break
  sleep 5
done
```

---

## 5. 获取生成结果路径

```
GET /api/v1/video-generation/jobs/{job_id}/results
```

返回各镜头视频文件在服务器上的绝对路径。**评估模块需要这些路径来读取视频并打分。**

```bash
curl "http://127.0.0.1:8880/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/results"
```

**返回:**

```json
{
  "ok": true,
  "data": {
    "job_id": "job_5f7f43f7f9738c21",
    "status": "done",
    "output_dir": "/path/to/outputs/video_api/jobs/job_5f7f43f7f9738c21",
    "shots": [
      {
        "scene_id": 1,
        "shot_id": 1,
        "shot_key": "scene_01_shot_01",
        "status": "completed",
        "video_path": "/path/to/.../videos/scene_01/scene_01_shot_01.mp4",
        "versions": null
      },
      {
        "scene_id": 1,
        "shot_id": 2,
        "shot_key": "scene_01_shot_02",
        "status": "completed",
        "video_path": "/path/to/.../videos/scene_01/scene_01_shot_02.mp4",
        "versions": null
      }
    ],
    "scene_videos": [
      { "scene_id": 1, "video_path": "/path/to/.../videos/scenes/scene_01.mp4" }
    ],
    "full_video": "/path/to/.../videos/full/full_video.mp4",
    "enhanced_video": "/path/to/.../videos/full/full_video_full.mp4"
  }
}
```

| 字段 | 说明 |
|---|---|
| `shots[*].video_path` | 镜头视频的绝对路径, 可直接读取文件进行评估 |
| `shots[*].shot_key` | 镜头标识, 格式为 `scene_{scene_id:02d}_shot_{shot_id:02d}` |
| `shots[*].versions` | 经过重新生成后会出现, 包含每个版本的路径和评分; 首次生成时为 `null` |
| `full_video` | 完整拼接视频路径 |
| `enhanced_video` | 增强版视频 (含字幕/配音/BGM) 的绝对路径; 若不存在则为 `null` |

---

## 6. 重新生成指定镜头

```
POST /api/v1/video-generation/jobs/{job_id}/shots/regenerate
```

Content-Type: `application/json`

评估后发现某些镜头质量不达标, 调用此接口重新生成。**旧版本视频会保留**, 新版本生成到 `_v2.mp4`、`_v3.mp4` 等路径。**不会自动拼接完整视频**, 拼接由 `/concat` 接口完成。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `shots` | `array` | 是 | 要重新生成的镜头列表 |
| `shots[*].scene_id` | `int` | 是 | 场景 ID |
| `shots[*].shot_id` | `int` | 是 | 镜头 ID |
| `shots[*].prompt` | `string` | 否 | 新 prompt, 不传则沿用原始值 |
| `shots[*].negative_prompt` | `string` | 否 | 新 negative prompt |
| `shot_scores` | `object` | 否 | 所有镜头的版本评分 (见下方说明) |
| `watermark` | `boolean` | 否 | 是否给本次重新生成的视频添加水印, 默认 `true` |

`shot_scores` 格式: key 为 `shot_key`, value 为版本评分数组:

```json
{
  "shot_scores": {
    "scene_01_shot_01": [{ "version": 1, "score": 0.95 }],
    "scene_01_shot_02": [{ "version": 1, "score": 0.3 }]
  }
}
```

**请求示例:**

```bash
curl -X POST "http://127.0.0.1:8880/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/shots/regenerate" \
  -H "Content-Type: application/json" \
  -d '{
    "shots": [
      { "scene_id": 1, "shot_id": 2 },
      { "scene_id": 2, "shot_id": 1 }
    ],
    "shot_scores": {
      "scene_01_shot_01": [{ "version": 1, "score": 0.95 }],
      "scene_01_shot_02": [{ "version": 1, "score": 0.3 }],
      "scene_02_shot_01": [{ "version": 1, "score": 0.4 }]
    }
  }'
```

**返回:**

```json
{
  "ok": true,
  "data": {
    "job_id": "job_5f7f43f7f9738c21",
    "output_dir": "/path/to/outputs/video_api/jobs/job_5f7f43f7f9738c21",
    "regenerated_shots": [
      {
        "scene_id": 1,
        "shot_id": 2,
        "shot_key": "scene_01_shot_02",
        "new_version": 2,
        "video_path": "/path/to/.../videos/scene_01/scene_01_shot_02_v2.mp4"
      },
      {
        "scene_id": 2,
        "shot_id": 1,
        "shot_key": "scene_02_shot_01",
        "new_version": 2,
        "video_path": "/path/to/.../videos/scene_02/scene_02_shot_01_v2.mp4"
      }
    ]
  }
}
```

**调用后:**
- 任务状态变为 `running`, 需要轮询 `GET .../jobs/{job_id}` 等待完成。
- 完成后, 用返回的 `video_path` 交给评估模块打分。

**注意:**
- 只有 `done` 或 `failed` 状态的任务才能调用。
- 旧版本文件不会被删除。
- 不会自动拼接, 需要最后调 `/concat`。

---

## 7. 按评分拼接完整视频

```
POST /api/v1/video-generation/jobs/{job_id}/concat
```

Content-Type: `application/json`

所有镜头评估完毕 (或达到重试上限) 后, 调用此接口拼接场景视频和完整视频。**只需传入被重新生成过的镜头的版本评分**, 系统会自动为这些镜头选评分最高的版本; 未出现在 `shot_scores` 中的镜头视为未重新生成, 直接使用原始版本 (v1) 拼接。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `shot_scores` | `object` | 否 | 已重新生成镜头的版本评分; 未包含的镜头按 v1 拼接。若没有任何镜头被重新生成, 可传 `{}` 或省略 |

**请求示例 (只有 `scene_01_shot_02` 被重新生成过, 其他镜头直接按原始版本拼):**

```bash
curl -X POST "http://127.0.0.1:8880/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/concat" \
  -H "Content-Type: application/json" \
  -d '{
    "shot_scores": {
      "scene_01_shot_02": [
        { "version": 1, "score": 0.3 },
        { "version": 2, "score": 0.85 }
      ]
    }
  }'
```

**请求示例 (没有任何镜头被重新生成):**

```bash
curl -X POST "http://127.0.0.1:8880/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/concat" \
  -H "Content-Type: application/json" \
  -d '{}'
```

**返回:**

```json
{
  "ok": true,
  "data": {
    "job_id": "job_5f7f43f7f9738c21",
    "selected_shots": [
      {
        "shot_key": "scene_01_shot_01",
        "selected_version": 1,
        "score": 0.95,
        "video_path": "/path/to/.../scene_01_shot_01.mp4"
      },
      {
        "shot_key": "scene_01_shot_02",
        "selected_version": 2,
        "score": 0.85,
        "video_path": "/path/to/.../scene_01_shot_02_v2.mp4"
      },
      {
        "shot_key": "scene_02_shot_01",
        "selected_version": 1,
        "score": 0.4,
        "video_path": "/path/to/.../scene_02_shot_01.mp4"
      }
    ],
    "scene_videos": [
      { "scene_id": 1, "video_path": "/path/to/.../videos/scenes/scene_01.mp4" },
      { "scene_id": 2, "video_path": "/path/to/.../videos/scenes/scene_02.mp4" }
    ],
    "full_video": "/path/to/.../videos/full/full_video.mp4"
  }
}
```

**注意:**
- 此接口是**同步的**, 拼接很快, 不需要轮询。
- `shot_scores` 里出现的镜头, 取评分最高的版本; 未出现的镜头视为未重新生成, 直接按原始版本 (v1) 拼接。
- 仅需传入实际重新生成过的镜头, **无需提交全量评分**。
- 调用后 `GET .../output/download` 会返回新拼接的完整视频。

---

## 8. 下载完整视频

```
GET /api/v1/video-generation/jobs/{job_id}/output/download
```

```bash
curl -o output.mp4 \
  "http://127.0.0.1:8880/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/output/download"
```

返回 `video/mp4` 文件。若视频尚未生成, 返回 `404`。

---

## 9. 下载增强版视频 (含字幕/配音/BGM)

```
GET /api/v1/video-generation/jobs/{job_id}/output/enhanced/download
```

```bash
curl -o output_enhanced.mp4 \
  "http://127.0.0.1:8880/api/v1/video-generation/jobs/job_5f7f43f7f9738c21/output/enhanced/download"
```

返回 `video/mp4` 文件。这是由后端配字幕、配音、BGM 后生成的最终成品视频 (`full_video_full.mp4`)。

若增强版视频不存在 (后端尚未生成, 或该任务不需要增强处理), 返回 `404`:

```json
{
  "ok": false,
  "error": {
    "code": "OUTPUT_NOT_AVAILABLE",
    "message": "增强版视频（含字幕/配音/BGM）尚未生成"
  }
}
```

**判断是否可下载:** 查询任务状态 (`GET .../jobs/{job_id}`) 时, 若 `enhanced_video_download_url` 不为 `null`, 即可使用该链接下载。

---

## 完整调用流程

### 无评估的简单流程

```
1. POST .../jobs              → 拿到 job_id
2. GET  .../jobs/{job_id}     → 轮询至 status=done
3. GET  .../output/download   → 下载视频
```

### 带评估的推荐流程

```
1. POST .../jobs                         → 拿到 job_id
2. GET  .../jobs/{job_id}                → 轮询至 status=done
3. GET  .../jobs/{job_id}/results        → 拿到各 shot 的 video_path
4. 将 video_path 交给评估模块            → 拿回各 shot 的分数
5. 若有镜头不达标且未到重试上限:
   a. POST .../shots/regenerate          → 带上 shot_scores, 拿到新版本 video_path
   b. GET  .../jobs/{job_id}             → 轮询至 status=done
   c. 将新版本 video_path 交给评估模块   → 拿回新分数, 更新 shot_scores
   d. 若仍不达标, 回到 5a
6. POST .../concat                       → 只传被重新生成过镜头的 shot_scores, 拿到 full_video
```

**关键点:**

- `shot_scores` 由 Pipeline 维护, 每次评估后更新, 调接口时传入。
- `/regenerate` 接口仍可接收全量 `shot_scores`, 用于把历史评分持久化到 `shot_versions.json`。
- `/concat` 接口**只需传被重新生成过的镜头**; 未重新生成的镜头直接按原始版本 (v1) 拼接。若本轮无重新生成, 可传 `{}`。
- `regenerate` 只生成不拼接, `concat` 只拼接不生成。
- 到达重试上限后直接调 `concat`, 系统自动选最高分版本。
- 评估模块不需要调任何接口, 只需要: 输入 video_path → 输出 score。

---

## 错误码速查

| 错误码 | HTTP 状态码 | 含义 |
|---|---|---|
| `JOB_NOT_FOUND` | 404 | job_id 不存在 |
| `JOB_NOT_READY` | 409 | 任务还在执行中, 不能 regenerate / concat / 下载 |
| `SHOT_NOT_FOUND` | 404 | scene_id / shot_id 在分镜中不存在 |
| `INVALID_PROMPT` | 400 | prompt 为空字符串 |
| `INVALID_RESOLUTION` | 400 | resolution 不在支持列表中 |
| `MISSING_STORYBOARD` | 400 | 未提供 storyboard |
| `RESULTS_NOT_AVAILABLE` | 409 | 任务尚未产生结果 |
| `OUTPUT_NOT_AVAILABLE` | 404 | 完整视频或增强版视频尚未生成 |
