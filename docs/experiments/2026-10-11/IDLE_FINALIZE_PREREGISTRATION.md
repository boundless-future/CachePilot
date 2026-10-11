# 空闲收尾候选：非零保护交互诊断

注册时间：2026-10-11，北京时间；在启动本轮 GPU 实验前保存。

前置证据：zero/2.5 GiB/15 秒宽限能够触发 idle drop 并在重入前后归零；two/2 GiB 重入虽然覆盖保护，但 idle drop=0。后者只能说明兼容性，不能证明两条路径共同发生时的生命周期。

固定配置：当前 `IdleFinalizeConnector` 源码、Qwen3-4B、RTX 4090 24GB、同步 eager、GPU KV 2.5 GiB、CPU KV 16 GiB、最多两个保护 chunk、15 秒空闲宽限；trace 沿用 `capacity-pilot-2p5g-20261011a/trace.json`，SHA 为 `079c9507c9585a5e5397affc8750c42603ee7efeeca3127d1cafafc2b1c54eb5`。首轮 68 请求、资源收尾后重复同一 68 请求。运行目录 `idle-finalize-two-2p5g-reentry-20261011a`。

新增覆盖门槛：至少 1 个真实保护提交及原回执，至少 1 个 idle pending drop。沿用首轮及重复轮 token 合约、H2D > 0、运行期和关闭后资源、无服务错误门槛；单独复算每个原 reservation、job、transfer/native terminal 与 END/reclaim。输出 hash 差异原样保存。本轮未启用逐层 KV 探针，只做生命周期/覆盖诊断，不能宣称 KV 等价或性能收益。

停止规则：缺任何覆盖或资源门槛则记录该轮失败/未覆盖，不追加压力、不改 trace、不放宽门槛。不把本轮加入历史容量扫描聚合，不自动启动 2.5 GiB 正式重复。实际 raw ID 重用、共享前缀取消、失败回执交互及旧 30 秒延迟案例另行设计。

```bash
python scripts/diagnose_capacity_owner.py --output artifacts/idle-finalize-two-2p5g-reentry-20261011a --trace artifacts/capacity-pilot-2p5g-20261011a/trace.json --idle-finalize --idle-grace-seconds 15 --mode two --kv-cache-bytes 2684354560 --repeat-after-idle
```

18:49 北京时间补记：a 在 engine.start 时因 SSH 的 PATH 缺少 Conda bin 而失败，`serve.sh: exec: vllm: not found`，没有模型请求或策略覆盖。保留原目录和原始错误；允许在 b 新目录、同源码/配置/trace/门槛下仅修正启动环境后重试。此操作不补救运行中的资源或覆盖失败。

```bash
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:$PATH
python scripts/diagnose_capacity_owner.py --output artifacts/idle-finalize-two-2p5g-reentry-20261011b --trace artifacts/capacity-pilot-2p5g-20261011a/trace.json --idle-finalize --idle-grace-seconds 15 --mode two --kv-cache-bytes 2684354560 --repeat-after-idle
```
