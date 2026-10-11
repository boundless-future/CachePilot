# 同所有权栈固定参数调优与留出验证预注册

日期：2026-10-11。此方案在任何固定参数扫描 GPU cell 启动前保存。现有 2.5 GiB 容量 formal 结束并归档后才允许同步新 runner，避免改变在途源码签名。

## 研究问题

保护策略必须与经过合理调参的 EVICTION_AWARE 比较，而不只比较默认 horizon。此轮补齐 P2 的固定 horizon/每步提交上限扫描，并保留 one/two 策略的同期对照。它不替代立即卸载、原生 vLLM 或新到达分布对照。

## 固定环境和变量

- Qwen3-4B/BF16、4090 24GB、同步 eager、单卡，GPU KV 固定 2 GiB，CPU KV 16 GiB。
- 八个配置使用同一 IdleFinalizeConnector、Owned MP 服务、15 秒空闲宽限；诊断日志和 KV probe 关闭。保护预算以外的所有权链相同。
- 六个固定基线：horizon = 1.25 / 2.5 / 5.0，与 max_drain_per_step = 1 / 64 全因子组合，保护预算为零。
- 两个候选：one/two chunk，horizon=2.5、max_drain_per_step=64。
- `max_deferral_seconds=0`；不将 deadline 与空闲宽限混为一谈。固定版源码确认 `max_drain_per_step` 计数是每次 drain 发出的 STORE operation 数，不是 chunk 或字节数。1 为受限提交端点，64 为默认端点；不在看结果后增删搜索点。
- 沿用已归档开发 trace `protection-ablation-staged-20261011a/trace.json`，68 请求、每请求固定 48 decode token、同到达时间；本轮不能在已有 validation trace 上选参数。
- 开跑前生成新 namespace `fixed-baseline-validation-20261011a` 的同结构 68-request trace，完整 prompt SHA 与开发 trace 无交集。此验证只复核新文本，不宣称新到达分布或真实 Agent 泛化。

## 执行顺序和门槛

1. pilot 八个配置各一次，只做运行、token、H2D、保护覆盖和资源准入，不计入正式统计。任何一格失败停止本轮后续格，保留证据；不得改变输入、追加压力或放宽门槛来筛选通过。
2. 完整通过后 formal 八个配置各三次，固定 repeat 轮转偏移 0/3/6，24 格。全部配置同样冻结源码 SHA、trace SHA、资源和计时方法。
3. 调优选择只在六个固定基线中按开发集三次 TTFT P95 的均值最小选一个；精确同分按配置名排序。one/two 不参与选择。输出所有点、样本 SD 与个别结果，不只发布最优点。
4. validation：锁定选择后，在预先固定的新文本 trace 上运行默认 fixed、选中 fixed、one、two 各三次；若选中项即默认项则去重，9 或 12 格。验证阶段不再调参；源码及非 trace 签名必须与 formal 相同。
5. 沿用原 ablation 完整资源/日志门槛，并要求运行期与退出后 active_sessions/temporary_count 清零、请求 event_id 无缺失重复、每条实际 completion_tokens 满足原 trace。H2D>0；候选必须有真实保护提交/回执。保留所有输出 hash 差异和精确分类的退出竞态。
6. 北京时间 21:50 后不启动新 cell，为 22:00 收尾留时间；不足时保留完整已完成格并标记矩阵未完成，不提前把部分扫描选成最优。

## 报告口径

复用 `run_protection_ablation.run_cell`；不修改安装包和原 runner 默认路径。报告 TTFT、ready-to-first/end、会话完成、实际 prefill、GPU/CPU hit、H2D/D2H、提交/回执、策略开销、idle drop 和全部资源证据。开发集调参结果与留出验证分开；n=3 不代表统计显著性，输出非确定性尚未根因定位。历史不同源码/运行时段的容量数据不并入本次均值。

入口为 `scripts/run_fixed_baselines.py`：pilot 无 prerequisite，formal 的 prerequisite 指向完整 pilot，validation 的 prerequisite 指向完整 formal。所有阶段都要求显式带时区截止时间。

## 运行前输入冻结

北京时间 19:27 左右生成并保存于服务器 `artifacts/fixed-baseline-inputs-20261011a`。开发 trace 68 请求、74,652 prompt tokens，规范化 SHA256 `49f2c9cb0b2faba83806a53727ef2280fa2bf38351154041f18847c3b0b056af`；新文本验证 trace 68 请求、75,808 prompt tokens，规范化 SHA256 `015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b`。逐事件 arrival_ms 完全一致，prompt SHA 交集为零。输入 token 数不同需单独报告，不能伪称逐 token 相同。
