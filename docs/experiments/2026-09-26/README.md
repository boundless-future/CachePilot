# 2026-09-26：重启复核与多轮 KV 回载诊断

本次沿用 2026-09-24 的环境和合成输入。目标是把原先串行多轮 2/16 的输出差异，与保存前、回载后的 KV 内容关联起来。上一轮受控实验只覆盖了独立的前缀案例，不能替代原始多轮序列。

## 方法

`check_outputs.py --include-probe` 顺序运行三种配置，每种执行同样 16 条请求：

- `baseline`：原生 vLLM，每条请求前成功清空 GPU 前缀缓存。
- `immediate`：同样清空 GPU 缓存，但 CPU 缓存保留前几轮保存的 KV。
- `probe`：与 immediate 相同的请求序列，在保存前、回载完成后同步 GPU 并比较物理块内容。

每种配置启动全新引擎与 CPU 缓存。max-num-seqs=1、48 个生成 tokens、temperature=0、seed=42；生成内容不进入后续输入。短请求推动异步完成回执，每次检查 reset 成功。`--eager` 可单独关闭 compile/CUDA Graph 做执行路径对照。

探针比较保存前参考值与回载完成后的实际 GPU 块，**不是直接比较 CPU 内存，也不是比较原生 vLLM 与 LMCache 的全部中间激活**。它主动同步设备、读取 GPU 内存，不能用于延迟测量。必须另外比较 immediate 与 probe 的输出，检查探针是否改变复现现象。

## 编译模式复现结果

| 检查 | 结果 |
|---|---|
| 原生 vs immediate | 2/16 不同，event 8/9，与 9 月 24 日复现位置一致 |
| 原生 vs probe | 2/16 不同，同样是 event 8/9 |
| immediate vs probe | 16/16 文本一致 |
| CPU 回载 | 12 条后续轮请求各命中 2048 tokens；GPU 命中为 0 |
| 实际回载 chunk | 96（每条 8 个 256-token chunk） |
| 逐层比较 | 3456/3456 bitwise 一致（36 层），0 缺失参考，0 不同 |

两例都在第 33 个生成 token 首次分歧，候选仍为 `:` 和 `:\n`。本次 logprobs 和上次记录一致。详细结果见 [compiled-analysis.json](compiled-analysis.json)，环境与脚本摘要见 [environment.json](environment.json)。

这次在保留原始多轮序列、且输出差异仍存在的前提下确认了 KV 传输内容一致，证据强于上次独立前缀的 4 个案例。可以排除**本次已观测回载中的 KV 内容损坏**，不能据此声称所有模型、异步并发、取消/抢占路径都正确；也还不能仅凭这个结果将输出差异完全归因于某个浮点内核。

## Eager 对照结果

额外开启 `--enforce-eager`，三种模式各执行同样 16 条请求。baseline/immediate/probe 两两比较均为 0/16 不同；96 次回载 chunk 的 3456 次逐层比较也全部 bitwise 一致，无参考缺失。详见 [eager-analysis.json](eager-analysis.json)。两种执行配置总计 96 个测量请求，另有推动回执的短请求不计入此数。

目前可下的结论：这条固定串行序列的差异与启用编译/CUDA Graph 的执行配置有关，关闭它们后本次差异消失；两种执行模式的已观测传输均保持 KV 内容。`--enforce-eager` 同时影响编译和图执行，尚未独立消融二者，不能直接宣布具体内核有 bug，也不能据此对所有负载承诺逐 token 确定性。

下一步正确性测试应分别控制编译与 CUDA Graph，并比较首分歧前的 logits/实际 kernel 路径。性能基线仍保留原始编译配置，不用 eager 的性能数据替换它。参数扫描前先完成下面的计时路径修正。

`analyze_kv_probe.py` 分别计数回载 chunk、逐层比较、不相等和缺失参考值。没有样本或缺少参考值都不能算成功；测试覆盖这些错误通过的风险。

## 后续性能实验约束

2026-09-24 的 `AdaptiveConnector` 继承逐步写 JSON 的 `DecisionConnector`，会遍历 free queue、计算前缀摘要、同步写日志。因此当时的原型计时只能用于接入诊断，不能将其与无日志固定策略的差异归因于 horizon 算法本身。

本日后续已分离无日志策略执行与可选观测，为阈值、重复绑定与异常时配置恢复增加测试，并开展相同日志设置下的三次重复对比及独立的块分配诊断。结果和后续方向见 [策略诊断报告](STRATEGY_DIAGNOSIS.md)。原型尚未证明性能收益。

## 复现

从服务器项目根目录，在 cachepilot Conda 环境执行。每次使用新输出目录：

```bash
python scripts/check_outputs.py \
  --trace artifacts/baselines-2026-09-24/fits-gpu-trace.json \
  --output artifacts/natural-probe-new --include-probe
python scripts/analyze_kv_probe.py artifacts/natural-probe-new \
  --output artifacts/natural-probe-new/analysis.json
```

回归脚本的 `summary.json` 现在以模式名为键，分别保存 immediate/probe 与 baseline 的比较。历史归档保持原有格式；原始 responses.json 的结构未改。

原始响应（含 top-5 logprobs）、逐层 KV 摘要、服务日志、输入 trace 和运行脚本已保存到本地及服务器的 `artifacts/natural-probe-evidence-2026-09-26.tar.gz`。校验值见 [归档清单](natural-probe-evidence-manifest.json)。12 项本地测试通过；本轮结束后测试服务停止，服务器保持开机。
