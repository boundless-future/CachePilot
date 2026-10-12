# 下一工作窗口：generation与保护的显式交接

20:52更新：多候选部分失败与shutdown snapshot契约完成，878项全套通过。下一步独立服务诊断runner与预注册，zero启动/关闭→one/two实际保护与KV bitwise/资源审计→服务故障退出，再性能；见 [故障报告](GENERATION_PROTECTION_FAULTS.md)。下方时间较早内容为历史状态。

20:38更新：独立GenerationProtectionConnector接线完成，最终873 passed/115 subtests，无跳过，见 [报告](GENERATION_PROTECTION_WIRING.md)。下一步补多候选部分失败、poison服务退出与shutdown终态证据，再建立新组合zero/one/two GPU诊断入口（原terminal/H2D/KV bitwise/resource/generation联合审计）。尚未运行新模型服务；不得直接开始性能formal。下面内容为历史状态。

19:29更新：CPU 候选交接完成，60项真实库回归通过，详见 [报告](GENERATION_PROTECTION_CONTRACT.md)。下一步从独立 opt-in Connector 的 helper bind、metadata、receipt、END pump 接线契约开始，补多候选部分提交/连续 reset。保留现有零预算 guard；GPU诊断须在服务契约通过之后。服务器已加入候选源码，历史210项严格签名不再匹配当前目录，复现旧formal请使用已归档源码。下面18:50及更早内容为历史记录。

18:50更新：下方历史formal入口已完整执行，12/12通过，不要重复启动。结果见 [正式报告](REAL_STRUCTURE_FORMAL.md)。下一步从 [接线设计](../../GENERATION_PROTECTION_INTEGRATION.md) 的真实manager CPU契约开始：完整范围及prefix/suffix票据、原回执、零步回滚、异常后unresolved、END关闭。独立opt-in实现、GPU bitwise准入后再新签名策略消融；不直接删除零预算guard。服务器保留210项已完成formal的冻结源，后续开发请先保存/使用归档源复现历史结果。

## 已完成formal的历史复现入口


截至2026-10-12 16:52，decode receipt formal十二格已完成；六session匿名结构有采样准入4/4、关闭额外采样pilot4/4也完成。三处独立复算与原始文件字节审计通过，见 [decode正式报告](DECODE_RECEIPT_FORMAL.md)、[结构GPU报告](REAL_STRUCTURE_GPU.md)。

服务器 `/root/CachePilot` 保留**210项无采样pilot冻结源**。不要先git pull或同步新脚本；新增scripts/configs也改变严格签名。本地与服务器有历史辅助文件差异，已完整源包归档，不能直接用本地目录代替。

## 正式入口

先检查SSH、无其他实验、GPU空闲、8000/8080/5555无监听；检查源snapshot的failures为空且additional_files为空。模型和依赖保持已验收环境，无需重新下载。

```bash
cd /root/CachePilot
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:$PATH
python scripts/verify_source_snapshot.py \
  artifacts/real-structure-unobserved-pilot-20261012a/plan.json \
  /root/CachePilot --output /tmp/structure-formal-source-check.json

python scripts/run_real_structure_pilot.py --stage formal \
  --admission artifacts/real-structure-pilot-20261012a \
  --pilot artifacts/real-structure-unobserved-pilot-20261012a \
  --built docs/experiments/2026-10-12/real-structure-built \
  --source-dir docs/experiments/2026-10-12/real-structure-source \
  --catalogue docs/experiments/2026-10-12/REAL_STRUCTURE_CATALOGUE.json \
  --decode-formal artifacts/generation-decode192-receipt-formal-20261012a \
  --output artifacts/real-structure-formal-NEXTWINDOW \
  --stop-starting-at REPLACE_WITH_AUTHORIZED_ISO8601_CUTOFF
```

用实际授权的带时区未来截止替换占位，输出目录必须不存在。预计十二格约30分钟，另留审计归档时间。四配置每组3次、轮换顺序、全六session60请求/48输出和原变换不变。任何格失败立即停止并保留全部失败及未运行格；不聚合部分矩阵，不重跑择优，不把有观测开销的准入样本混入。

## 完成后

`audit_real_structure_pilot.py` 复核来源、请求/计时、原资源、generation、无额外采样文件和immediate零抢占；服务器、本地、冻结源复算一致后归档原文件和inventory。正式n=3报告TTFT、ready-to-end、session完成、prefill/hits、H2D/D2H、queue和抢占，保留输出hash差异。不要使用合成trace的early/pressure/revisit分组；后续CPU分析应按全部六source session，必要时在独立本地目录开发，不修改运行中服务器签名。

依据该结果再决定是否需要拓宽输入/模型或接回非零保护。当前generation路径仅零保护，不能直接把保护预算改成非零进行性能测试，必须先独立接线及GPU正确性诊断。多worker/异步、真实driver/DMA fault、永久I/O自动恢复、保护泛化收益仍开放。

本次收尾未关闭容器。上游贡献仍以最小可维护复现为前提，不把项目适配层缺口概括为所有上游版本缺陷。
