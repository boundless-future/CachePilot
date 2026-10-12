# Generation＋保护正常路径 GPU 准入

2026-10-12。独立新组合正常路径诊断 b 完成 zero/one/two 三格；不是性能实验，也不覆盖服务故障退出、取消或抢占专项。

## 固定输入与实现

新增 `GenerationProtectionProbeConnector`，组合已有同步KV probe与新generation保护Connector；新增 `run_generation_protection_smoke.py`。使用未改动的 `protection_zero_smoke.run(..., bounded_workload=True)` 输入：576-token来源、12个压力前缀、固定两段160个224-token短请求尾部和原前缀恢复。每格348请求，尾部属于预定工作负载，不是失败后临时补流量。

Qwen3-4B BF16、4090 24GB、同步eager TP1、2 GiB KV池；保护预算zero/one/two为0/16/32物理blocks，reserve不变。每格独立启动服务，显式选择两个generation opt-in；服务端只读observer记录END返回与精确集合。无idle-finalize补偿，无注入故障。同步probe影响时延，本报告不进行性能聚合。

runner启动前写plan和214项源码SHA；三格同签名，结束后逐项复核并归档完整源码tar。独立审计脚本在本地开发，三格结束前未同步入服务器。

## 结果

| 项目 | zero | one | two |
|---|---:|---:|---:|
| 完整请求／generation END | 348 / 348 | 348 / 348 | 348 / 348 |
| STORE handoff批次 | 14 | 25 | 14 |
| 保护STORE | 0 | 11 | 9 |
| 保护峰值blocks | 0 | 16 | 32 |
| 严格保护来源回载记录 | 不适用 | 10 | 14 |
| KV层比较（全部bitwise一致） | 720 | 720 | 864 |
| 原reservation终结 | 22 | 22 | 26 |
| 原native transfer终结 | 24 | 38 | 26 |
| 最终BlockPool free | 909 | 909 | 909 |
| 最终保护pins | 0 | 0 | 0 |
| 相对zero输出文本差异 | — | 0 / 348 | 0 / 348 |

三个配置相同name/payload逐项核对；generation注册、scheduler END提交、server END返回精确对应。每格结束前后session/job/transfer wire集合为空，所有账本操作terminal/dropped，BlockPool无注册请求、tracked refs或deferred frees。保护来源审计连接原STORE→native terminal→实际回载及KV相等；来源记录数不是独立请求数。

one在本输入下包含保护[0,256)及默认suffix路径；本轮尚未建立逐链独立长前缀专项门槛，不能代替1088-token长prefix、取消/抢占或失败回执矩阵。

## 首轮失败保留

a轮zero完成348请求、720层全等、348精确END；但原smoke的状态摘要函数过滤掉 `generation_observer`，新审计缺少运行结束前/后精确集合证据。按门槛停止，one/two未启动。a仍标记整体不通过，不能因其他计数闭合改为通过。

b只在独立runner增加只读/status采集，保留observer字段；摘要和observer来自相邻两次查询，精确集合证据取第二次原始响应，不宣称两者同一原子快照。不改模型/输入/策略/阈值，新目录重跑三格。

## 可复核证据

`generation-protection-gpu/`保存a及b原始目录，共84文件、16,611,799 bytes，全部与服务器manifest逐字节一致。a计划含当时源码SHA，b另有214项源码tar；a首版runner未单独冻结tar。独立本地重算a=false、b三格=true，报告见 `a-independent.json`、`b-*-independent.json`、`independent-summary.json`。

新增独立重算工具 `scripts/audit_generation_protection_smoke.py`；实际归档输入的poison、pending、缺observer、KV不等篡改测试及既有generation auditor共 **14 passed**。这里保留原runner输出与独立复算，不能只看保存的passed标志。

```bash
PATH=/usr/local/miniconda3/envs/cachepilot/bin:$PATH \
  python scripts/run_generation_protection_smoke.py --output artifacts/NEW_UNIQUE_DIRECTORY
python -m scripts.audit_generation_protection_smoke CELL --output AUDIT_JSON
```

最后GPU 0%/1MiB，b三格vLLM日志未检出ERROR/Traceback；服务已正常停止，容器未关闭。

## 下一步

本组合首次正常模型服务准入已完成。下一步为真实服务中部分提交/metadata/receipt异常导致EngineCore退出及失败snapshot落盘，随后长prefix、取消/抢占和失败回执联合审计。全部门槛通过后才能新签名pilot/formal。当前不扩模型或削减reserve，不将正常路径成功写成全面生产正确性或性能收益。
