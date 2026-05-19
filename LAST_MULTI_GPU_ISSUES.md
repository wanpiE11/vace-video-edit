# 双卡链路最后定位到的问题

这份记录只总结当前仍然存在的问题，不包含最开始已经排掉的 `flash-attn` 导入报错。

## 结论

当前双卡运行的问题更像是多卡运行时 / 分布式并行链路问题，不像模型权重损坏。

已经确认：

- 预处理可以完成
- `torchrun --nproc_per_node 2` 可以启动
- 两个 rank 可以完成 NCCL 初始化
- T5、VAE、主模型 checkpoint shard 可以加载完成

但双卡链路仍然存在两层未解决问题：

1. 开启 `dit_fsdp` / `t5_fsdp` 时，流程会卡在 `FSDP(...)` 初始化阶段
2. 关闭 `dit_fsdp` / `t5_fsdp`、只保留 USP 双卡后，流程仍然会在更后面的多卡路径挂住

## 问题 1：FSDP 初始化卡住

当双卡命令带上：

- `--dit_fsdp`
- `--t5_fsdp`

时，流程可以稳定走到：

- `VaceWanModel.from_pretrained(...)` 完成
- USP wrapper 完成
- `dist.barrier()` 完成

然后卡在 `shard_model(self.model)` 这一段，不再继续。

对应位置：

- `repos/VACE/vace/models/wan/wan_vace.py`
- `wan/distributed/fsdp.py` 里的 `FSDP(...)`

这说明挂点在 FSDP 构造内部，而不是采样循环。

### 已做过的排查

以下实验都做过，但没有解决卡住问题：

- 将 `sync_module_states=True` 改为 `False`
- 去掉 `auto_wrap_policy`

这说明问题不是这两个单独参数导致的简单卡死。

## 问题 2：去掉 FSDP 后，USP 双卡路径仍然挂住

做过一个对照实验：

- 保留 `ulysses_size=2`
- 保留 `ring_size=1`
- 去掉 `--dit_fsdp`
- 去掉 `--t5_fsdp`

结果：

- 不会再卡在 `FSDP(...)`
- 流程可以继续往后走
- 但双卡链路仍然不能正常完成推理

观察到的现象：

- 至少一个 rank 会进入 `D` 状态
- GPU 利用率接近 0
- 显存仍被占用
- 没有正常进入稳定的采样输出阶段

这说明即使把 FSDP 完全拿掉，USP 多卡路径本身仍然不干净。

## 为什么当前不像模型权重损坏

目前现象不支持“模型下载不稳定导致权重损坏”是主因，理由如下：

- 模型 shard 可以反复成功加载
- 没出现缺 shard、shape 不匹配、反序列化失败之类的错误
- 问题位置和是否启用 FSDP / USP 强相关
- 问题表现为卡住，而不是典型的权重读取失败

所以当前更像：

- 分布式运行时问题
- FSDP 初始化问题
- USP 多卡路径问题

而不是：

- 模型文件本身损坏

## 当前最合理的判断

当前双卡链路至少有两层问题：

1. `FSDP` 初始化在这套环境里会挂住
2. 即使绕开 `FSDP`，`USP` 多卡路径也还会挂住

所以后续如果继续排查，应该优先查：

- `wan/distributed/fsdp.py`
- `xfuser` / `yunchang` / USP 多卡链路
- 多卡同步、group、all_gather、attention 并行实现

而不是优先怀疑模型权重损坏。
