# 下一工作窗口：先正式 decode，再匿名结构

截至14:50，新decode receipt pilot四格全部通过；服务器 `/root/CachePilot` 保持205项冻结源码，不把本地新增CPU工具同步进去。模型、依赖、原 diagnostic/tuning/base reference 和完整pilot目录仍在服务器，正式重复无需重新下载模型。

## 1. 同签名 decode192 formal

先检查没有其他实验、端口可用、依赖/模型仍是已验收版本，再运行服务器已有 `verify_source_snapshot.py`。205项源字节必须与pilot plan完全相同且无额外source；本地的新CPU工具会改变该签名，因此直接git pull后不能拿原pilot做准入。若要更新，使用原tar源的隔离工作目录，或明确重新冻结并重做pilot。

以下在服务器执行。把 cutoff 替换为实际已授权工作窗口的带时区截止；输出目录必须不存在。正式十二格预计需要比本次剩余20分钟更长的窗口，不能以截至时间迫近为由跳过检查或只挑成功格。

```bash
cd /root/CachePilot
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:$PATH
python scripts/verify_source_snapshot.py \
  artifacts/generation-decode192-receipt-pilot-20261012b/plan.json \
  /root/CachePilot --output /tmp/decode-formal-source-check.json

python scripts/run_generation_matrix.py --stage formal --workload decode192 \
  --diagnostic artifacts/generation-long-prefill-pilot-20261012c \
  --tuning artifacts/fixed-baseline-formal-20261011a \
  --long-prefill-formal artifacts/generation-long-prefill-matrix-formal-20261012a \
  --base-reference artifacts/unprotected-references-formal-20261011a \
  --immediate-diagnostic artifacts/immediate-decode-candidate-20261012a \
  --pilot artifacts/generation-decode192-receipt-pilot-20261012b \
  --output artifacts/generation-decode192-receipt-formal-NEXTWINDOW \
  --stop-starting-at 'REPLACE_WITH_AUTHORIZED_ISO8601_CUTOFF'
```

四配置各三次，沿用runner的循环轮换；任一token/资源/服务/签名门槛失败则整轮停止，保留未运行格，不聚合部分结果。关闭热路径日志。完成后独立收集、请求/SSE时间复算、整套配置成本与阶段描述、原文件字节inventory归档。输出hash差异保留，单独报告范围；不解释为已证明语义等价。

## 2. 全部六会话匿名结构 GPU pilot

CPU准备完成，详见 [结构方案](REAL_STRUCTURE_PLAN.md)，冻结token trace SHA为 `445b05df9898232b97d2a0f7a0fc7633a42ab8a4d712943ee3f70ebb6c2acb83`。实际Qwen3-4B tokenizer签名、六源SHA及独立审计已保存。下一步需要独立结构runner和配置/trace门槛接线，当前没有该GPU runner，也没有该负载GPU结果。

保持全部六session/60请求、原输入/8、源会话内部时间/10、第一到达对齐0、48输出、2GiB GPU KV、不增加人工压力/复制/padding。四配置仍为native、new immediate receipt、generation default/selected，非零保护不加入。不要由pilot速度选择session或参数。

先记录真实运行中的running/等待请求、GPU KV占用/allocator物理证据、抢占计数、实际计算prefill、GPU/external hit、H2D/D2H、会话完成/入口等待和原资源快照。必须区分“请求/资源通过”和“真实物理压力确实发生”；若无压力，保留低压力结果，不能偷偷压缩到达后仍使用原实验名称。变换需要独立注册。

ImmediateReceiptConnector不支持抢占与unhealthy，若该结构触发限制就fail closed并定位，不能改为忽略状态继续跑。源匿名结构不含原文本或输出反馈，open replay不强制前轮完成；不能称live Agent质量实验。完整四格资源与配置门槛通过后才讨论formal；GPU KV bitwise/新生命周期证据不足时先独立诊断，不混入性能。

## 3. 仍不应扩大结论的范围

非零保护仍未证明稳定优于固定调优基线；generation/immediate候选仅零保护、同步TP1。多worker、异步、真实CUDA driver/DMA fault、永久I/O自动恢复仍开放。上游贡献必须基于锁定上游的最小复现及可维护补丁，不把本项目适配层时序缺口概括为所有LMCache版本的问题。
