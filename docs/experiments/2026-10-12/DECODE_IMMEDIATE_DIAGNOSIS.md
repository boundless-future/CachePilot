# decode192：立即卸载 END/末批 STORE 的时序支线

原预注册 pilot `generation-decode192-matrix-pilot-20261012a` 在 native 通过后，于 owned-immediate 停止：68 个请求、13,056 输出 tokens、HTTP errors=0，但两个 exact-wire session 在退出前后均残留。default/selected 和 formal 未运行，不能聚合部分性能数据。原关闭错误保持 fatal，不放宽门槛。

冻结源码快照为 `generation-decode-source-20261012.tar.gz`，SHA256 `54d842f208ee3e969ec3ff02f3d77637a52be94634ba05dc0a1104b14e2290d9`；199 文件逐项校验见同目录 `decode-source-verification.json`。

## 只读复现

14:01–14:03 独立运行 `immediate-decode-diagnostic-20261012a`，保留原 immediate connector、策略、trace、模型和容量。新增 SessionManager 调用点与服务端 END-return 观察，仅写诊断事件，不提交额外 END 或清理。日志开启，耗时不进入性能结论。

再次 68/68 请求完成、HTTP errors=0，两个相同序号的 generation（17、21）残留，owned job/transfer 已清零。独立 timeline 显示：原 lookup session 存在→END handler 返回且 session 已删除→最后一个真实 STORE 提交→`EngineContext.resolve_obj_keys` 调用 `get_or_create`→STORE native terminal/retired→没有再次 END→退出前后 session 仍存在。第17代 END 到末批 STORE 提交间隔约 0.389ms，第21代约 0.662ms。第21代 END 之前也有已完成的 STORE，末批跨 chunk 的新 STORE 在 END 后重建 session。

144 个原 reservation 和101个原 native transfer 均逐身份闭合；资源问题在 session 终结顺序，未观察到 pin/transfer 泄漏。69 次服务端 END（含 warmup）全部返回。此轮没有退出 fatal，但严格资源门槛仍失败。独立复算见 `generation-matrix-evidence/immediate-decode-diagnostic-20261012a/timeline-audit.json`。

安装版 eager `request_finished()` 在请求结束时先 `scheduler_adapter.end_session(raw)`，再返回 `_can_store=True`；vLLM 后续 worker 才执行最后一个输出跨 chunk 的 STORE。native worker 的 `get_finished()` 已有等待原 STORE future 的完成逻辑，vLLM scheduler 收到 `finished_sending` 后才释放 blocks。当前证据定位了这套已适配堆栈的真实时序；不能据此直接认定所有 upstream 版本都存在该问题，也没有替换原长prefill的抢占代际缺口结论。

## 独立候选与准入

新增默认不启用的 `ImmediateReceiptConnector`，只调整 session END：native scheduler 已等待 lookup/status 后，将 exact-wire END 登记到队列；收到原 eager worker `finished_sending` 才提交一次 END。原 STORE future、复制、GPU block 延后释放均由 native adapter/vLLM 管理。END 提交不是服务端 ACK，仍需独立 exact-server 快照与 END-return 审计。

限定同步单卡TP1、一个endpoint、eager producer、零保护。原生抢占、worker unhealthy、raw ID 在旧 STORE 关闭前重用均 fail closed，不能称为生产级 eager 收尾方案。没有对 old baseline 打补丁，也没有把 lazy generation connector 直接套到不存在的 lazy manager 上。

CPU队列测试覆盖原身份、重复完成、提交失败保留和重用拒绝；真实安装库回调测试覆盖请求结束→等待→worker output→END，以及真实 MessagingFuture success/false 的延迟完成、无STORE请求单次报告。测试fixture把只读worker_id当可写的首次失败保留，修复fixture后重验。下一步独立候选GPU诊断→完整回归→新冻结四配置pilot；原失败永远不改写。候选诊断通过也不能作为性能改善。

## 证据

- 原pilot：`generation-matrix-evidence/generation-decode192-matrix-pilot-20261012a/`。
- 只读diagnostic：`generation-matrix-evidence/immediate-decode-diagnostic-20261012a/`。
- 原始source与回归日志：`generation-matrix-evidence/`。
- 复算命令：`python scripts/audit_immediate_decode.py CELL --output audit.json`。

## 候选GPU与后续门槛实测

独立 `immediate-decode-candidate-20261012a` 完成68请求、13,056输出、零HTTP错误；144原reservation、69 lookup、101 native transfer逐身份闭合，实际BlockPool恢复909 free且无残余引用/延后释放。exact sessions/jobs/transfers退出前后均空，无END后迟到STORE。原失败与候选成功均新增离线回归测试，防止审计把session残留或terminal闭合混为一谈。

全套834 passed、9 CUDA显式跳过、115 subtests；随后单独显式CUDA 9 passed，不能称为一次全套CUDA回归。关闭诊断后新四配置pilot已4/4通过，见 [pilot报告](DECODE_RECEIPT_PILOT.md)。formal十二格尚未开始，取消/抢占/worker故障和多worker范围仍未验收。
