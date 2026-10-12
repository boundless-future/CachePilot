# Generation bridge 与新矩阵的复核

从仓库根目录运行。离线 Python 环境需要 requests/aiohttp，测试另需 pytest；
GPU 运行依赖固定 Linux/Conda/vLLM/LMCache/模型栈，见 `docs/ENVIRONMENT.md`。

## 不用 GPU 的审计

诊断 c 原始目录在
`docs/experiments/2026-10-12/generation-bridge-evidence/generation-long-prefill-pilot-20261012c/zero-r0`。
`audit_generation_pilot.audit_cell` 复核原始 handoff 和服务端 wire；
`audit_generation_ownership.audit_cell` 复核原 reservation、native terminal
及 BlockPool。两者同时通过，才代表本轮的完整诊断门槛。

```bash
python -c "import sys; from pathlib import Path; sys.path.insert(0, 'scripts'); from audit_generation_pilot import audit_cell; from audit_generation_ownership import audit_cell as owned; p=Path('docs/experiments/2026-10-12/generation-bridge-evidence/generation-long-prefill-pilot-20261012c/zero-r0'); print(audit_cell(p)); print(owned(p))"
python scripts/summarize_generation_matrix.py docs/experiments/2026-10-12/generation-matrix-evidence/generation-long-prefill-matrix-pilot-20261012a --output artifacts/review/generation-pilot.json
```

诊断 c 应为 78 代际、589 reservation、69 lookup、75 transfer、909 free；
性能 pilot 为 4/4。原 a 是启动失败，b 没有 BlockPool snapshot，不能用 c 的
更强门槛事后宣称 a/b 都通过。诊断的逐事件日志不能算性能样本。

冻结源码包 `generation-matrix-evidence/generation-matrix-source-20261012.tar.gz`
SHA256 为 `9f05cac9d2f1f5c5d8ccea71b07a50f5aaf846bd363c6aa3de2d4a746a329627`。
解压到独立目录后用 `scripts/verify_source_snapshot.py <plan.json> <source-root>
--output <report.json>` 检查全部 manifest 字节；不要拿后续新增脚本替代历史源码。
该包保存 scripts/configs；native 扩展和完整运行环境仍参照既有 build manifest
与环境快照，不把它描述为包含模型的完整镜像。

## GPU 顺序与失败边界

固定原 long-prefill 失败目录 → 独立 generation 诊断 → 新性能 pilot →
完整独立审计 → 同一源码签名 formal 三重复。每阶段目录新建且不复用失败目录。
入口分别为 `run_generation_pilot.py` 与 `run_generation_matrix.py`；
显式传入 `--stop-starting-at` 时区截止点。

新矩阵保留 native/owned-immediate/default/selected 四配置，只有 lazy
两组采用新 generation Connector。所有组比较的是整套配置成本，零保护限定。
快照显示 END 已提交时，还必须结合原服务端精确资源集合与关闭结果；不把提交
当 RPC ACK。新源码改动需要新的 pilot，不能继承旧 formal 准入签名。

计划的 decode192 后续以完整 long-prefill formal 和原 held-out reference 为
前置，改变输出预算但保持 prompt/arrival/seed。先重新冻结 pilot；详情见
`DECODE_WORKLOAD_DESIGN.md`。首次/末次非空 SSE fragment 的时间差不是
逐 token 的 CUDA 延迟。所有输出 hash 差异继续报告。

## 六会话匿名结构试点

CPU来源及转换复核不需要GPU：

```bash
python scripts/audit_real_structure.py \
  --directory docs/experiments/2026-10-12/real-structure-built \
  --source-dir docs/experiments/2026-10-12/real-structure-source \
  --catalogue docs/experiments/2026-10-12/REAL_STRUCTURE_CATALOGUE.json \
  --output artifacts/review/real-structure-source-audit.json
```

GPU入口 `run_real_structure_pilot.py` 要求 `--decode-formal` 指向完整十二格新立即卸载候选矩阵，另传 `--built`、`--source-dir`、`--catalogue`、新的 `--output` 和实际授权的 `--stop-starting-at`。不能在decode冻结运行期间同步这些新增文件。详见 [结构GPU预注册](REAL_STRUCTURE_GPU_PREREGISTRATION.md)。

输出目录包含 `input/` 的原catalogue/六session/built trace、运行源码签名、每格请求、完整原metrics采样及原资源快照。随后离线执行 `python scripts/audit_real_structure_pilot.py OUTPUT_DIR --output audit.json`，它复算采样峰值并复核来源和原资源门槛；缺指标不当零、原失败不能被通过标志覆盖。该试点有0.5秒额外API采样，单次诊断不生成正式性能聚合，也不使用合成trace的early/pressure/revisit划分。实际GPU完成情况以今日记录为准。
