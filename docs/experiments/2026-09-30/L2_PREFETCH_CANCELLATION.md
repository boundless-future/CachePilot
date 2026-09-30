# 真实 FS L2 prefetch 的取消后延迟回收

## 结论

开启现有回收候选时，两轮均在真实文件系统 L2 load 完成后回收 17 个对象，CPU 读写锁、活动 job、abandoned job 与 controller 完成结果全部归零。关闭候选的同条件对照残留 17 个读锁、1 个 job 和 1 个完成结果，直到 follow-up 与引擎退出后仍在。三轮 follow-up 均正常命中 4,352 token，32-token 输出与各自参考一致。此次没有修改候选回收算法。

这是**真实数据读写 + 人为控制异步时序**的实验，不是自然 I/O 故障，也不是性能测试。取消发生在 L2 lookup 的屏障内；取消后才放行 lookup 并进入 L2 load。它验证取消后尚未完成的 prefetch 的清理所有权，不覆盖客户端在数据传输中途取消、传输中断或所有并发顺序。

## 环境、流程和代码

RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache `1a4b40b1d79b0e76244f127f96ee0982f8bd270f`。原生 `LMCacheMPConnector`，2 GiB GPU KV、16 GiB L1，三轮均 `--enforce-eager`。与 9 月 27 日的 L1 实验不是性能对照。

1. 原生模型请求生成 KV，真实 FS L2 adapter 保存 17 个 `.data` 文件，共 641,728,512 bytes（612 MiB）。结果保存文件名、大小及 SHA256。
2. 停止并重启 vLLM 和 MP server，保留磁盘文件；`before-status.json` 确认 L1 对象数及占用为 0。
3. 新请求在真实 FS adapter 的 lookup coroutine 前进入异步屏障，观察 `deferred>0` 后断开客户端，等待 END_SESSION 返回。
4. 放行 lookup，保持实际文件读取前的 load 屏障。此时 17 个 L1 缓冲区均为写锁，实际 prefetch controller 仍有 1 个在途任务。候选保有 1 个 abandoned job，完成回收数为 0；等待一秒再取样，写锁仍为 17。
5. 放行原始 FS load coroutine，真实读取磁盘字节。候选消费 controller 的最终 bitmap，逐对象解锁；后续同前缀请求验证真实回载与生成。
6. 记录引擎退出后的资源快照，最后停止本轮拥有的服务。

[l2_prefetch_gate.py](../../../../scripts/l2_prefetch_gate.py) 对固定 SHA256 的 FS adapter 加屏障，保留原始 coroutine；不修改安装包。屏障只阻塞自己的异步协程，server 仍可接收 END_SESSION 和状态查询。60 秒超时会放行以便清理，但记录 `gate_timeout` 并导致验收失败。[l2_prefetch_cancel_smoke.py](../../../../scripts/l2_prefetch_cancel_smoke.py) 管理服务、磁盘数据、屏障和资源验收；`finally` 放行并关闭服务。对照也加载相同日志/时序包装器，但不加载回收候选，因此不是完全未修改的 server。

```bash
source /usr/local/miniconda3/bin/activate cachepilot
python scripts/l2_prefetch_cancel_smoke.py --reclaim \
  --output artifacts/l2-prefetch-cancel-2026-09-30-candidate-r1
python scripts/l2_prefetch_cancel_smoke.py \
  --output artifacts/l2-prefetch-cancel-2026-09-30-control-r1
python scripts/l2_prefetch_cancel_smoke.py --reclaim \
  --output artifacts/l2-prefetch-cancel-2026-09-30-candidate-r2
```

## 实测结果

| 配置 | 取消后 load 屏障内写锁 | load 完成后读锁 / job / 完成结果 | follow-up 后及引擎退出后 | 回收对象 | 资源验收 |
| --- | ---: | --- | --- | ---: | --- |
| 候选 r1 | 17，保持至少 1 秒 | 0 / 0 / 0 | 同样归零 | 17 | 通过 |
| 关闭候选 control-r1 | 17，保持至少 1 秒 | 17 / 1 / 1 | 残留不变 | 0 | 失败 |
| 候选 r2 | 17，保持至少 1 秒 | 0 / 0 / 0 | 同样归零 | 17 | 通过 |

17 个 load 缓冲区在此配置中为 temporary；候选释放后 `total_object_count` 和 `memory_used_bytes` 都回到 0。对照在 load 完成后保留 612 MiB 临时 KV。观测时间远短于 300 秒 read TTL，本实验不声称对照永不回收。

两轮候选均出现 `reclaim_owned → reclaim_pending → reclaim_completed`，prefetch ID 为真实 controller ID `0`（非 L1 快速路径的 `-1`）。每轮三个事件的 request ID 和 job token 一致，完成事件恰好释放 17 个不同对象 key、reader=1，与目标 load 的 key 集合相同。完成事件发生在 load 屏障放行之后；真实 FS coroutine 返回、资源快照和 follow-up 输出共同支持真实 load 完成。

首轮运行时还未加入脚本内的完整事件审计，已对保存的原始 JSONL 离线补审计，结果单独保存在 [event-audit.json](candidate-r1/event-audit.json)，没有改写原始结果。审计按 PID/task ID 区分 warmup、目标请求和 follow-up；六项反例/正例测试防止错误对象、提前释放、L1-only、遗漏 pending、屏障超时和后续请求混淆导致假通过。服务器 `pytest -q tests/`：90 passed、11 subtests；本地：74 passed、22 skipped、11 subtests。服务器仅有已知 OpenTelemetry 弃用警告。

## 产物与边界

[候选 r1](candidate-r1/result.json)、[对照](control-r1/result.json)、[候选 r2](candidate-r2/result.json)及各自完整状态快照、server/gate/reclaim 事件已入库。服务日志已备份到本地忽略目录 `artifacts/l2-prefetch-cancel-2026-09-30-{candidate-r1,control-r1,candidate-r2}/`；612 MiB/轮的 KV 文件留在服务器同名目录的 `kv-files/`，本地保存摘要，不将模型 KV 大文件入库。

阶段 3B 仍在进行中。下一项优先处理 END_SESSION 先于 LOOKUP 的协议乱序：现有候选只能接管已经注册的 job，提前到达的 END_SESSION 仍可能遗漏迟到 LOOKUP。无 END_SESSION 的客户端死亡、永久未完成 controller、关闭时 unresolved job、自然故障/部分写入，以及自然抢占和在途 STORE 仍是独立待办。当前实验尚不解除 GPU 提前 pin/准入保护门槛，也不是可提交上游的完整修复。
