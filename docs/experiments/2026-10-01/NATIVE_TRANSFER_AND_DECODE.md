# Native transfer、writer 与 decode 正确性复核

日期：2026-10-01。固定 GPU 环境为 RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0、LMCache commit `1a4b40b1d79b0e76244f127f96ee0982f8bd270f`。输入 trace SHA256：`28ecd2fe00ff37db14d5ec9504b31a1e09b7f48e14324c1fde900c1ac1e5bc23`。本轮实现是独立实验 endpoint 和契约，**没有替换正式 LMCache MP server、Connector 或 vLLM BlockPool；3B 仍未验收**。

## 已实现

- `native_transfer_runtime.py` 将原 Lookup/worker/transfer 身份及 nonce 放入安装版 LMCache native completion dispatcher 的 payload。提交返回和匹配的 server callback 都发生后才结束 source/target lease；缺 callback 时保留。独立 `TargetArena` 对真实 CUDA tensor 的 block 分配、布局和占用做检查，不代表 vLLM allocator。
- `owned_wire_protocol.py` 和 `owned_socket_runtime.py` 提供版本化 QUERY/RETRIEVE ticket 的严格 msgpack 解析、loopback ZMQ 往返以及显式断联/迟到请求/shutdown 契约。客户端不能从 bitmap 补造 token。endpoint 是单 worker 同进程的可信实验环境，没有自动进程死亡检测。
- `owned_writer_contract.py` 和 `native_writer_runtime.py` 以原 writer 身份保护 L1 写入；真实 D2H 部分写入或错误只能在 native marker 后丢弃，成功只在 marker 后发布。它没有接入正式 StorageManager。
- `diagnostic_connector.py` 记录真实 worker KV 布局和逐步 decode slot；`check_outputs.py` 支持编译与 CUDA Graph 分离消融，`check_decode_pairs.py` 对冷请求和 CPU 回载请求作成对比较。探针只适用于串行、非异步、单 dense group 的诊断运行，不用于性能评估。

## GPU 与模型证据

服务器完整测试 **398 passed、115 subtests、131 warnings，17.29s**，见 [日志](native-transfer-evidence/full-pytest.txt)。包含真实 pinned CPU→CUDA、LMCache `GPUCacheContext`/`transfer_kv_per_object_group` 原生 kernel、dispatcher/ZMQ 往返、错误身份、TTL/END、目标 block 复用，以及真实 D2H 部分写入。GPU fixture 使用 Torch-owned stream 和 CuPy `ExternalStream`，避免进程退出时借用 stream 的 allocator 生命周期冲突。测试中的延时只制造在途 CUDA work，**不是 vLLM scheduler 自然抢占**。

串行 16 请求、baseline/immediate/probe 输出与恢复前后 KV 的结果：

| 执行配置 | baseline vs immediate 输出 | immediate vs probe 输出 | 回载前后 KV |
| --- | ---: | ---: | ---: |
| compile-only (`mode=3`, Graph `NONE`) | 1/16 不同，事件 8 的 token 32 | 0/16 | 3456/3456 层 bitwise 相同 |
| graph-only (`mode=0`, `FULL_DECODE_ONLY`) | 0/16 | 0/16 | 3456/3456 层 bitwise 相同 |

graph-only 实验记录的 worker tensor 为 BF16 `[910, 8, 16, 256]`、stride `[32768, 256, 2048, 1]`，36 层、16-token block、256-token LMCache chunk。历史默认编译加 Graph 有 2/16 输出差异，eager 为 0/16。当前证据说明独立编译配置可以复现输出差异，而本 trace 的独立 Graph 配置没有复现；**尚未定位唯一根因**。回载 KV 相同不等于后续 token 必然相同。

另以事件 0/4/8/9 做四组成对冷请求/CPU 回载请求，每请求 48 个生成 token，每组 47 个已计算生成输入 slot。每个配置均以 GPU prefix reset 和隔离 cache salt 排除 GPU prefix 命中：

| 执行配置 | 冷/回载输出差异 | 探针干扰 | decode slot 与逐层 KV |
| --- | ---: | ---: | ---: |
| eager | 0/4 | 0/4 | 188/188 slot，6768/6768 层相同 |
| compile-only | 0/4 | 0/4 | 188/188 slot，6768/6768 层相同 |
| graph-only | 0/4 | 0/4 | 188/188 slot，6768/6768 层相同 |

这组短成对实验不能否定 16 请求串行编译实验中的差异。逐层比较覆盖选定 token 前缀的 decode KV，均无缺失参考。首次 decode 探针在默认异步调度下因 scheduler/token ledger 错位返回 500；改为明确关闭异步调度后完成。因此**异步 decode 探针尚未覆盖**，不能把这次诊断运行当作默认异步路径的正确性证明。

原生 completion recorder 源码在固定 LMCache checkout 的 SHA256 为 `33d84093edf3c4f0a58307cdd455aecb8a2df05223fead03f625b203b851d391`。其 `cudaLaunchHostFunc` 失败时清理 payload，但不会给 Python 调用方传回完成证据；runtime 在 callback 缺失时保留 unresolved。没有注入真实 CUDA driver fault，也没有修改安装版 LMCache。

证据目录保存各配置的 `manifest.json`、`analysis.json`、必要摘要和测试日志；大体积逐 token 探针原始 JSONL 留在服务器 `artifacts/`。复现入口：

```bash
python -m pytest -q
python scripts/check_outputs.py --trace artifacts/baselines-2026-09-24/fits-gpu-trace.json --output artifacts/output-compile-only-recheck --execution compile-only
python scripts/check_outputs.py --trace artifacts/baselines-2026-09-24/fits-gpu-trace.json --output artifacts/output-graph-only-recheck --execution graph-only
python scripts/check_decode_pairs.py --trace artifacts/baselines-2026-09-24/fits-gpu-trace.json --output artifacts/decode-pairs-eager-recheck --execution eager
python scripts/check_decode_pairs.py --trace artifacts/baselines-2026-09-24/fits-gpu-trace.json --output artifacts/decode-pairs-compile-only-recheck --execution compile-only
python scripts/check_decode_pairs.py --trace artifacts/baselines-2026-09-24/fits-gpu-trace.json --output artifacts/decode-pairs-graph-only-recheck --execution graph-only
```

## 未关闭的验收条件

正式 MP RPC 和 Connector 尚未传递原 ticket/完成身份，也没有真正的 vLLM BlockPool owner；因此这些 GPU 测试不能证明正式服务可安全复用 block。无 END 的自动失联发现、永久阻塞 controller 的关闭、真实传输中止、并发及 side-stream join、自然 DMA 期间抢占、正式 StorageManager writer 路径仍需处理。下一步应以固定版真实 MP server + Connector 作 opt-in 集成，先保留现有协议测试和原生对照，再验证这些资源边界；仅在 3B 门槛有真实服务证据后进入 3C GPU 策略消融。
