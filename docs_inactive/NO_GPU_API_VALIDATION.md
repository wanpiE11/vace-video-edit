# 无 GPU 条件下验证 HTTP API

这条验证路径只检查 HTTP API 壳层，不检查真实四卡推理。

## 自动化测试

先激活环境，再运行现有的 API 和 service 单测：

```bash
source activate_vace.sh
cd /root/data/gzn/vace-video-edit
python -m unittest tests.test_video_edit_api tests.test_video_edit_service
```

这组测试不依赖 GPU，也不会启动真实 `run_edit_video_server.py`。

## 手工 HTTP 联调

仓库现在提供了一个 fake backend 启动脚本：

```bash
source activate_vace.sh
cd /root/data/gzn/vace-video-edit
python run_fake_video_edit_api.py
```

脚本会在 `workspace/fake_api_server/fixtures` 下自动生成：

- `input.mp4`
- `reference.png`
- `edited.mp4`

启动后会在终端打印一份可直接使用的示例 JSON。默认每个假任务会延迟 `1` 秒完成，方便观察：

- `queued` / `running` 状态
- `GET /results` 在未完成时返回 `409`
- 下载接口返回真实 `video/mp4`

## 建议验证场景

如果当前 shell 配了代理，访问本地服务时建议额外带上 `--noproxy '*'`，或者先清掉 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。

```bash
curl http://127.0.0.1:8880/healthz
```

```bash
curl -X POST http://127.0.0.1:8880/api/v1/video-editing/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "workspace_id": "demo",
    "video_path": "/root/data/gzn/vace-video-edit/workspace/fake_api_server/fixtures/input.mp4",
    "reference_image_path": "/root/data/gzn/vace-video-edit/workspace/fake_api_server/fixtures/reference.png",
    "prompt": "replace subject",
    "bbox": [10, 20, 30, 40],
    "output_name": "edited.mp4",
    "client_request_id": "req-demo-001"
  }'
```

```bash
curl http://127.0.0.1:8880/api/v1/video-editing/jobs/<job_id>
curl http://127.0.0.1:8880/api/v1/video-editing/jobs/<job_id>/results
curl -OJ http://127.0.0.1:8880/api/v1/video-editing/jobs/<job_id>/output/download
```

非法参数也建议固定测一遍，例如：

- 相对路径
- 不存在的输入文件
- 越界 `bbox`

## 通过标准

无 GPU 模式下，可以认定“API 通了”的标准是：

- HTTP 服务可启动
- 所有路由可访问
- 状态码和错误码映射正确
- 返回结构与 `VideoEditAPI.md` 一致
- 下载接口返回 mp4 文件流
- 轮询流程可完整走通

以下内容不属于这条验证路径：

- 真实 engine 自动拉起
- 四卡模型加载
- 真正的推理成功
- GPU 资源不足时的真实失败行为
