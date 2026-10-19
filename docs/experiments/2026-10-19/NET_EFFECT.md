# 净效果主线：保护是否有用，以及检查开销是否值得

2026-10-19，接续正式三重复。先分析已有coverage原始证据，不把诊断耗时并入正式性能数据。

## 已定位的问题

one/two各一次保护的来源均匹配trace_0057的前缀。排除warmup后，从最早原请求的generation注册到保护PREPARED相隔9,407.520/9,395.470ms；原请求注册必在回放开始之后，因此这是保护相对回放开始时间的严格下界，不是假定客户端与另一轮实验时钟对齐。主要尾请求4/5/8/9/10的首token均早于这个下界。保护不可能直接改善这些已经结束的TTFT。

两格原60请求内的7次RETRIEVE均不覆盖这个受保护前缀；严格KV准入中后来追加的恢复请求不能算作原轨迹中的保护收益。更早的default_manager已经提交相同前缀的0..1536 STORE并成功退休，早于保护约7.1s；这证明重复保存历史，但单凭历史成功不能证明保护时CPU条目仍驻留。

正式六个保护格中prepare各调用约1,900次，累计wall约248–268ms，仅提交1批。它是确定存在的开销，但累计时间不是请求关键路径，也尚不能证明它导致P95退化。现有数据已经足以定位“晚且未被后续回载使用”的机制，不另租GPU补同类诊断。

离线分析入口为`scripts/analyze_whole_step_net_effect.py`，逐前缀、逐回载、成功传输与时间下界的结果及输入文件SHA保存在[evidence/whole-step-net-effect-analysis.json](evidence/whole-step-net-effect-analysis.json)。同一前缀可以匹配多个turn，因此只确认会话/前缀，不按到达顺序猜测请求ID。

## 本轮唯一改进假设与预注册

**假设：缺少可操作STORE候选时仍进行整步包络和全free queue扫描，是可以消除的一部分策略开销。** 为共享引用运行时增加显式开关：健康/未结算PREPARED检查保持在前，仅在没有满足现有初步条件的finished前缀候选时提早返回；有候选仍执行原reserve、排序、token/hash证明、pin及回执流程。默认关闭，旧策略作为同源码对照。不放宽容量预留，不使用trace未来信息，不改变输入或保护预算。

先用真实manager/native契约验证：无候选时不改pool/pending；可操作候选时开关前后保护分区、共享引用、零计算回滚与成功/失败回执行为相同。再在同一原轨迹跑zero/legacy-two/filtered-two匹配pilot。保留原one负结果；本轮只考察two上的检查优化，不重新选择保护预算。

pilot需请求/token、原始计时、generation/回执/资源闭合全部通过，two两组有实际保护提交；filtered-two的prepare累计wall至少减少50%，否则该假设不进入formal。该阈值只判断开销实验是否值得扩大，不证明TTFT收益。通过才按三种轮转顺序做三重复formal；不按TTFT挑选重复或替换失败。主指标仍为TTFT P95，同时比较prepare开销、prefill、H2D/D2H、抢占与输出差异。若开销下降但TTFT没有一致改善，则仅承认开销优化，继续把“如何保护真正会复用且有丢失风险的KV”作为策略主问题。

旧formal与新数据分开报告。此实验只验证无候选时的早退优化，不声称修复保护目标过晚/无用的问题，也不能代替独立负载或原生/default基线。

## 实现与前置检查

`runtime/shared_prefix.py`仅新增默认关闭的`skip_ineligible`门；显式WholeStepConnector通过严格bool配置接线。有候选后原reserve、排序、prefix切分、hash证明、pin与回执代码未改；没有触碰LMCache安装目录。新实验入口为`scripts/run_whole_step_efficiency.py`（run/audit）；复用原审计并显式传入预注册配置，不放宽运行期错误、无探针或资源门槛。新隔离目录`/root/cachepilot-neteffect-20261019`，旧实验目录不改。

真实栈五文件42项全部通过，覆盖开关前后成功/失败回执、共享owner、零计算回滚、不可操作时无pool/pending变更以及健康/未结算保护guard；日志见[evidence/eligibility-contract-tests.txt](evidence/eligibility-contract-tests.txt)。Windows架构/源码/配置36passed、9skipped，见[evidence/eligibility-local-tests.txt](evidence/eligibility-local-tests.txt)。首次旧AST源码契约因新增guard失败（35passed/1failed/9skipped），适配为仅剔除明确位置的可选guard后，原body仍逐AST相等；失败说明在[evidence/eligibility-local-initial.json](evidence/eligibility-local-initial.json)。运行时未因该测试再次修改。旧3C formal也经新离线审计复算9/9。

pilot与formal均完成；正式源码冻结298项，旧结果未混入新比较。

## pilot结果与正式阶段

pilot三组3/3通过，180请求/8,640输出tokens完整，两保护组均提交1批并取得原成功回执，峰值32blocks；资源及generation闭合。zero/legacy-two/filtered-two P95为1859.574/1929.438/1848.001ms，仅为单轮描述。legacy/filtered的prepare累计wall为254.688/64.778ms，比例0.25434（下降74.57%），通过预设50%门槛。两者prefill均33,677、H2D均2,189,426,688bytes、D2H均3,510,632,448bytes、抢占均1；zero本轮prefill36,477且抢占2，继续保留基线自身波动。

同298项冻结源的formal九格9/9通过，共540请求/25,920输出tokens；每格原始计时、generation、回执、资源、源码、输入及无热路径探针均审计通过。zero/legacy-two/filtered-two的P95均值分别1941.014/1867.354/1847.470ms。核心配对比较filtered相对legacy每轮−1.101%、−0.904%、−1.191%，均值−1.065%；prepare累计wall均值247.989→63.699ms，每轮下降73.67–74.78%。三轮延迟均更快，但仅是单条匿名轨迹的描述性证据，不宣称统计显著或普遍因果收益。相对zero均值legacy−3.795%、filtered−4.819%；保留zero和策略本身的波动，不将其误当成外部原生/default基线。

filtered三轮computed prefill均33,677、H2D均2,189,426,688、D2H均3,510,632,448 bytes、抢占均1；legacy的r0分别36,477、2,000,683,008 bytes、3,623,878,656 bytes和0次，r1/r2与filtered一致。变化可能同时受并发调度、抢占与实际输出token差异影响；不能把1.065%直接归因为prepare省时。输出SHA差异仍按repeat逐请求保留：legacy-r0/r1/r2相对zero分别9/1/8条；filtered分别2/0/8条。filtered-r1出现服务完成且资源排空后的关闭期`EngineDeadError` traceback，命中既有窄关闭分类；没有请求期fatal，未隐藏或替换该格，CUDA IPC warning照常保留。

正式证据包571文件、SHA256 `2b92f990ed218460132a091435ab8e05cf929ee2f19956a9bb1c893ac97a4070`，位于[evidence/eligibility-formal-20261019a.tar.gz](evidence/eligibility-formal-20261019a.tar.gz)，逐文件清单位于[evidence/eligibility-formal-20261019a-inventory.json](evidence/eligibility-formal-20261019a-inventory.json)。服务器、当前和冻结源码三份汇总相同，全部SHA通过。服务器端口8000/8080/5555空闲、无实验进程、GPU 1MiB/0%，容器保持开机；快照见[evidence/eligibility-final-idle-20261019.json](evidence/eligibility-final-idle-20261019.json)。逐格正式结果见[evidence/eligibility-formal-local-audit.json](evidence/eligibility-formal-local-audit.json)。

## 唯一下一步：精确归因受保护前缀

本轮证明过滤无候选扫描大幅减少，TTFT出现小幅一致信号；核心问题仍未回答：受保护STORE属于哪条trace请求，其后同前缀请求究竟命中vLLM GPU prefix cache、LMCache CPU回载，还是重算？原始API请求ID随机，只能用重复token前缀映射多个turn；没有匹配LMCache RETRIEVE不能排除GPU热命中。

下一步只做一项机制诊断：沿用相同固定轨迹与two预算，在回放API中传入可审计的trace event ID请求标识，要求响应ID原样匹配，并把对应generation、computed prefix、LMCache lookup/retrieve和保护批次按请求串起来。zero/legacy-two/filtered-two只跑到完成该映射，不并入性能结果。若保护没有增加CPU回载且相关请求本来GPU命中，则停止扩展保护策略；若确有CPU回载及可见重算/TTFT获益，再定位触发时机与哪些前缀有收益，随后只提出一个策略改动并进行新冻结对照。

这不是第二条性能线，也不另建大套件；回放模块已有诊断request ID支持，先核实服务端到端身份，再只补所需的scheduler/worker对应记录。
