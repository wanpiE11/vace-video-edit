# Video Edit Service Implementation Progress

## Current Goal

当前阶段目标是先打通这条最小链路：

1. 第一次提交任务时，自动启动常驻四卡 engine 并加载模型。
2. 第二次提交任务时，复用同一个 engine，不重复加载模型。
3. 如果第二个请求在第一阶段加载时到来，只等待，不重复起第二个 daemon。
4. 任务完成后，可以拿到输出视频路径。
5. 任务失败时，`job_store` 里有明确错误状态和错误信息。

## Implemented

### 1. In-process service layer

已新增 [video_edit_service.py](/root/data/gzn/vace-video-edit/video_edit_service.py)：

- `VideoEditService`
- `submit_job(payload)`
- `get_job(job_id)`
- `get_engine_state()`
- `close_default_service()`

核心行为：

- 进程内单例常驻 service
- 单 FIFO 任务队列
- 单后台 worker 串行执行
- `engine_state` 支持 `stopped / starting / ready / busy / failed`
- `job_status` 支持 `queued / running / done / failed`
- 首次提交任务时自动拉起 `run_edit_video_server.py`
- 任务执行复用现有 `run_edit_video.py --server-socket ...` 链路
- `job_store` 目前为内存态
- 失败时记录：
  - `error.code`
  - `error.message`
  - `error.traceback`

### 2. Tests

已新增 [tests/test_video_edit_service.py](/root/data/gzn/vace-video-edit/tests/test_video_edit_service.py)。

已覆盖：

- 首次提交触发 engine 启动
- 第二次提交复用同一个 daemon
- `starting` 期间第二个请求只等待，不重复启动
- 成功完成后回填 `out_video_path`
- 失败后写入 `ENGINE_EXECUTION_FAILED` 和错误详情

已实际跑过：

```bash
python -m unittest discover -s tests -p 'test_*.py'
python -m py_compile video_edit_service.py tests/test_video_edit_service.py
```

## Real-world Validation

### 1. Environment checks passed

已确认：

- 4 张 GPU 可见
- 输入视频存在
- 输入参考图存在
- `models/Wan2.1-VACE-14B` 存在

### 2. Resident 4-GPU server can really load

直接按文档命令单独启动 `run_edit_video_server.py` 时，真实观察到：

- 多卡 worker 正常拉起
- 模型初始化需要数分钟，不是秒级 ready
- 最终输出：
  - `Inference model is initialized`
  - `Resident Wan inference service listening on /tmp/vace_wan_infer_clean.sock`

这说明四卡常驻 engine 这条底层路径本身是能起来的。

### 3. Real inference chain status

真实跑过一次：

- 本地预处理成功
- server 端收到了 resident inference request
- server 端已进入 50-step 采样推理，进度稳定推进

本次对话里我没有拿到最终客户端标准输出，因为等待过程中会话被中断，之后按你的要求清理了推理进程。

直接能确认落盘的最新产物是这次真实跑的预处理结果：

- [src_video-swap_anything.mp4](/root/data/gzn/vace-video-edit/workspace/outputs/processed/real_run_clean/src_video-swap_anything.mp4)
- [src_mask-swap_anything.mp4](/root/data/gzn/vace-video-edit/workspace/outputs/processed/real_run_clean/src_mask-swap_anything.mp4)
- [src_ref_image_0-swap_anything.png](/root/data/gzn/vace-video-edit/workspace/outputs/processed/real_run_clean/src_ref_image_0-swap_anything.png)

你补充说明“默认推理已经结束并且成功了”。这一点在本轮中以用户确认作为事实记录；但我在清理进程前没有再次抓到最终 stdout，也没有在 `real_run_clean` 目录下直接复核到新 `out_video.mp4`。

## Known Issues

### 1. Current ready check is too weak

`VideoEditService` 现在用“socket 文件存在且可连接”作为 ready 判定。

这在干净环境下可工作，但在有陈旧 daemon 残留时不够稳，会出现：

- 误判为 `ready`
- 任务真正执行时 `run_edit_video.py` 连接不到 socket
- `job_store` 记录为 `ENGINE_EXECUTION_FAILED`

### 2. Stale multi-process children can be orphaned

`run_edit_video_server.py` 四卡模式会拉起多进程。

当前 `VideoEditService.close()` 只终止了它持有的父进程对象，不保证把所有子进程一起回收。失败试跑时确实出现过：

- `run_edit_video_server.py` 子进程残留
- GPU 被残留进程占用
- 后续启动/ready 判定被污染

### 3. Real-run output capture is not closed-loop yet

这轮手工真实推理里：

- 预处理输出明确落盘
- server 确实进入了真实采样
- 但 `VideoEditService` 自身还没有在真机上完整验证出“成功回写 job_store 里的 `out_video_path`”

也就是说，当前代码已经把编排结构搭好了，但“真机成功结束并由 service 稳定收口结果路径”还没有完全闭环验证完。

## Recommended Next Steps

优先级建议如下：

1. 修正 daemon 生命周期管理。
   - 启动时创建独立 process group
   - 关闭或失败时回收整组子进程
   - 避免 orphan workers 留在 GPU 上

2. 强化 ready 判定。
   - 不只看 socket 文件
   - 增加真正的握手/探活响应
   - 最好避免被陈旧 socket 或旧进程误判

3. 再跑一次由 `VideoEditService` 驱动的真机全链路。
   - 清理环境
   - 提交 job
   - 轮询到 `done`
   - 抓到最终 `out_video_path`
   - 再提交第二个 job，验证不重复加载模型

4. 真机闭环后，再挂最小 HTTP API。
   - `/healthz`
   - `POST /api/v1/video-editing/jobs`
   - `GET /api/v1/video-editing/jobs/{job_id}`


## Files Added In This Phase

- [video_edit_service.py](/root/data/gzn/vace-video-edit/video_edit_service.py)
- [tests/test_video_edit_service.py](/root/data/gzn/vace-video-edit/tests/test_video_edit_service.py)
- [IMPLEMENTATION_PROGRESS.md](/root/data/gzn/vace-video-edit/IMPLEMENTATION_PROGRESS.md)

## Existing Local Change Not Touched

- [VideoEditAPI.md](/root/data/gzn/vace-video-edit/VideoEditAPI.md) 已有本地修改，当前实现没有覆盖它。
