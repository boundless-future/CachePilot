# 固定轨迹回放与基线实验

工具固定每轮完整输入、工具记录、到达时间和输出 token 预算；本次生成文本不进入下一轮输入。比较的是推理服务的缓存与排队行为，不要求模型重演同样的 Agent 决策。

## 输入与时间语义

schema_version=2 的事件包含 session_id、turn、arrival_ms、think_ms、prompt（文本或 token ID 列表）、prompt_tokens、prompt_sha256、max_tokens、seed。合成轨迹逐轮追加已记录 assistant/tool 文本，校验器检查前缀、连续轮次和 hash。匿名真实结构轨迹允许前缀分叉。

生成器使用 Qwen3-4B tokenizer。context-tokens 是初始材料预算，实际每轮还包括任务与固定历史；服务 usage 必须与记录长度匹配。输出固定 temperature=0、seed=42、ignore_eos=true、48 tokens，用于系统对照，不适用于自然 Agent 行为质量评价。

开放环：事件按 arrival_ms 独立就绪，允许后续轮在前轮未完成时就绪。它是固定提供负载，不是因果执行的在线 Agent。最大在途 64；客户端排队计入 ready-to-first/end。

封闭环：每个 session 首轮按 arrival_ms 启动，其后等待前轮完成再等待 think_ms；不同 session 并发。实际发送时间随服务速度变化。两种模式分开统计。

## 测量

- ttft_ms：请求发出至首个非空文本 SSE chunk，是客户端首个可见文本时间。
- ready_to_first_ms / ready_to_end_ms：从应就绪时刻计，包含客户端排队和事件循环晚发。
- e2e_ms：请求发出至流结束；client_queue_ms 单独记录。
- chunk_arrivals_ms：流块到达时间，不能把一个 chunk 当作一个 token 计算 ITL。
- session completion：最后响应完成减首轮应就绪时间。
- 请求保存文本、输出 hash、usage、finish_reason 和错误。缺失 DONE、usage 长度不匹配判失败。
- vLLM/LMCache counter 取同一实验前后快照差分。

## 实验入口

Linux 容器内激活 cachepilot，在项目根目录执行：

```bash
python scripts/run_baselines.py --output artifacts/baselines-run-01
```

默认 baseline、immediate、eviction、eviction-h5 四种策略；4/12 个 session、每个 4 轮的两种合成工作集；三次重复。每单元新建服务，重复间轮换策略顺序。2 GiB GPU KV、16 GiB L1、max-num-seqs=4、8K 上下文、默认编译模式。共同短 warmup 后调用 loopback dev API reset_prefix_cache 并检查 success；warmup 不计入计时/counter 差分。

每轮到达间隔 1.8 秒，session 间错开 20ms。初始工作集约 8K/24K tokens，GPU KV 实际约 14.5K tokens。horizon=5 只是固定参数候选，不预先称为最优。

实验占用 8000、5556、8081，独立于环境检查的 5555/8080。VLLM_SERVER_DEV_MODE 仅用于本机缓存重置，不用于公网部署。结束关闭测试服务，服务器保持运行。

实际分配信号消融可使用 `--modes eviction allocation immediate`；诊断单独用 `--modes allocation-decision --workloads exceeds-gpu --repeats 1`。每个单元保存 `run-manifest.json`（脚本/配置 SHA256、KV 预算、日志模式），诊断账本保存在该单元的 `decisions/`。配置改变后必须用新的输出目录。

```bash
python scripts/generate_trace.py --model models/Qwen3-4B --output artifacts/trace.json
python scripts/replay_trace.py --trace artifacts/trace.json --output artifacts/requests.json \
  --model /root/CachePilot/models/Qwen3-4B --mode closed
python -m unittest discover -s tests -v
```

单元测试用本地流式假服务检查 SSE 分片、空 chunk、缺失 DONE、封闭环依赖与开放环排队，不需要 GPU。

## 真实结构小样本

prepare_real_trace.py 固定 kv-cache-tester commit，读取编号前 20 个匿名轨迹，排除嵌套子 Agent，筛选 3–32 轮、输入长度 /8 后每轮输入+48≤8192 的完整 session。保留全部筛选结果与 SHA256，最多取前 4 个合格会话。本轮只有 trace_0019 合格（24 轮）；原尺寸 8K 覆盖率为 0/20。

源 64-token hash block 映射为确定性的 8 个 Qwen token ID，保留块身份，不恢复原始文本。时间 /50，输出固定 48 tokens。这改变了绝对压力、chunk 对齐和服务时间比例，结论仅适用于该缩比负载。原始与派生数据保存在 artifacts，不混称原始 Agent 执行。

```bash
python scripts/prepare_real_trace.py --model models/Qwen3-4B --output artifacts/real-source
python scripts/run_baselines.py --trace artifacts/real-source/real-trace.json \
  --output artifacts/real-baselines --repeats 1 --modes baseline immediate eviction eviction-h5
```
