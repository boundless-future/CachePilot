# 下一工作窗口：匿名结构正式三次重复

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
