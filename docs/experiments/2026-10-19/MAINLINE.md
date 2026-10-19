# 整步候选端到端主线

2026-10-19。目标：在固定匿名压力轨迹上，先回答保护是否实际执行且正确，再回答是否减少重算、改善 TTFT，以及代价是否值得。当前3C，禁止用边界测试数量替代模型实验。本轮主实验闭环已完成，正式9/9通过但稳定性能收益未成立，详见[正式结果与下一主线](WHOLE_STEP_FORMAL.md)。

## 主实验条件

- RTX4090 24GB；Qwen3-4B BF16；GPU KV 2 GiB、CPU KV 16 GiB；同步 eager、单 worker、max-num-seqs=4。
- 原六会话60请求、48输出tokens/请求的匿名整数token轨迹，未改到达时间/长度；SHA256 `445b05df9898232b97d2a0f7a0fc7633a42ab8a4d712943ee3f70ebb6c2acb83`，分词器文件哈希复核通过。
- h5/d1/grace15，显式 `WholeStepConnector`。只改变整步reserve和相应排名窗口，pin/回执/generation/前缀证明复用统一运行时。
- Python3.12.14、vLLM0.30.0、LMCache0.5.5+g1a4b40b1d、torch2.13.0+cu130。原历史目录未改，新源码/运行目录 `/root/cachepilot-mainline-20261019`。
- native源 SHA `4060f83d4d5fb7b88620e303d9b7961bd371e208f916099ae54fc1e3bc5ac84c`，二进制 SHA `3eb61c696490f34f80ce4f9c452769dc6988c705b099d50a5c37c51d0d8f3b8a` 与既有build记录一致。

## 已完成：服务覆盖

运行目录 `runs/whole-step-coverage-20261019a`，代码冻结293文件，one→two→zero，每格全新服务。此轮启用handoff/service事件，属于正确性/机制覆盖诊断，计时不用于收益判定。

| 格 | 请求/输出tokens | 保护提交/成功回执 | pin峰值 | generation/END | prefill tokens | H2D bytes | D2H bytes | 抢占 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| one | 60/2,880 | 1/1 | 16 | 62/62 | 33,677 | 2,189,426,688 | 3,510,632,448 | 1 |
| two | 60/2,880 | 1/1 | 32 | 62/62 | 33,677 | 2,189,426,688 | 3,510,632,448 | 1 |
| zero | 60/2,880 | 0/0 | 0 | 61/61 | 36,477 | 2,000,683,008 | 3,623,878,656 | 0 |

三格请求/长度、原批次回执、实际受保护STORE提交及成功退休、allocation前保护、预算、generation/server END、锁/job/传输/GPU注册回收、运行期日志均通过。one/two各7次capacity fallback；每格结束保护引用为0。保留关闭时CUDA IPC warning；没有fatal ERROR，也没有触发历史关闭traceback排除。

本候选解决了此次固定轨迹上双块零提交的覆盖阻断；不能据此改写旧策略原pilot3/4失败，不能把保护STORE与整体H2D同时存在视作同块回载证明。prefill较低同时伴随抢占，尚不是净性能收益结论。

证据包本地 `artifacts/whole-step-coverage-20261019a.tar.gz`，SHA256 `1ebbcfb9eb114ee2ac02cc6b2c66a8fdfb2ef070b8b1a75e7b9c011c8aea5da9`，69文件；解包到 `artifacts/mainline-evidence/whole-step-coverage-20261019a`。服务器及本地独立审计均3/3；本地报告 `artifacts/whole-step-coverage-local-audit.json`。冻结源、输入、配置、逐请求原始计时与输出、服务事件、generation/保护摘要均保留。

## 已完成：保护来源KV准入

入口 `scripts/run_whole_step_mainline.py --stage kv`，显式 `WholeStepSparseProbeConnector` 的MRO确认经过WholeStepConnector/WholeStepAdmission。要求已完成三格coverage，运行时源码必须完全一致；仅允许变更两个实验入口/审计文件和新增稀疏probe。前置覆盖原始证据一并归档。

先原60请求，再独立追加80条224tokens驱逐请求和各目标受保护前缀的恢复请求；追加步骤只验证正确性，不并入原轨迹/性能数据。捕获保护STORE逐层内容，记录所有STORE覆盖标记，严格审计实际服务提交、终结、无中间STORE的来源，以及后来RETRIEVE逐层SHA/bitwise全等。one通过才two；任何失败停止后续扩展，保存原证据并针对该阻断修复。

实际两格全部通过：one为1 chunk/36层，two为2 chunks/72层，所有比较全等，均对应本候选实际保护的原STORE来源。每格原60请求及81追加请求完整，one/two各2/1次自然抢占，144/143个generation全部原server END返回，pin最终0、所有资源闭合；运行时源码与coverage完全相同。追加请求生成和bitwise探针均有观测开销，prefill/搬运/计时不进入性能比较。

远端 `runs/whole-step-kv-20261019a`，本地 `artifacts/mainline-evidence/whole-step-kv-20261019a`；证据包SHA256 `496520273bfad49bc8ceceebcedb2341897abe4a1e772022c6fe1520de581bb4`。服务器/本地审计均2/2通过，本地报告 `artifacts/whole-step-kv-local-audit.json`。

## 已完成：无逐步探针匹配pilot

入口 `scripts/run_whole_step_matrix.py`，只新增两个matrix入口/审计脚本，前置KV准入运行时保持完全一致；前置证据全量随本轮归档。zero→one→two三格各一轮，每格全新服务，不启用handoff JSONL、scheduler快照或KV同步探针。原60请求及8token warmup；同模型、h5/d1、预算、到达计划。pilot全部通过后才启动同源码formal三重复，顺序zero/one/two、one/two/zero、two/zero/one。任一格失败停止后续扩展，不筛选重跑。

审计核对所有原始请求/计时/输出SHA、配置与源码签名、无逐步诊断、generation计数/原END、资源与保护闭合。主指标TTFT P95，结合prefill/H2D/D2H/抢占/覆盖，保留所有重复及输出差异；pilot不混入formal。负向验证：把已通过的coverage诊断格送入性能门槛，因存在逐步JSONL被正确拒绝。

pilot 三格全部通过，原180请求/8,640输出tokens完整，保护提交/回执各1/1，pin峰值16/32；zero 61代、one/two各62代全部原END闭合，无热路径JSONL。所有资源回收，无fatal日志或关闭traceback。运行时冻结296文件，服务器/本地/冻结源审计均3/3通过。单轮结果仅作pilot描述：

| 模式 | TTFT P95 ms | computed prefill tokens | H2D bytes | D2H bytes | 保护批次 | 抢占 |
|---|---:|---:|---:|---:|---:|---:|
| zero | 1886.623 | 36,477 | 2,000,683,008 | 3,623,878,656 | 0 | 0 |
| one | 1747.666 | 33,677 | 2,189,426,688 | 3,510,632,448 | 1 | 1 |
| two | 1875.675 | 33,677 | 2,189,426,688 | 3,510,632,448 | 1 | 1 |

相对zero输出SHA差异one 8/60、two 7/60，原事件编号保存在审计报告中；固定输入/到达并不保证不同调度下decode完全一致，不能用长度完整或稀疏KV正确性替代语义/输出全等。原匿名token压力轨迹也不是Agent任务质量数据集。

证据 `evidence/whole-step-pilot-20261019a.tar.gz`，SHA256 `c08a176a0dae8e7e2cfa4454b65afe5ad29310a46930e58095f9c6eb12cd6543`，189文件；SHA清单、本地和冻结源审计同目录。

## 已完成：同源正式三重复

`runs/whole-step-formal-20261019a`，完全复用pilot的296文件冻结源和原输入，按预注册的三个轮转顺序共9格。每格完整通过后才进入下一格，最终全部通过才聚合；不挑选更快的重复，不把pilot或探针结果并入formal。实际全部9/9通过，540请求/25,920输出tokens完整，296项源未漂移，所有资源与558代闭合。one/two的TTFT P95均值相对zero为+2.97%/−2.46%，two在r1退化，不能宣称稳定收益。348文件（含前置）SHA校验及服务器/本地/冻结源三份复算完全一致；正式证据包SHA `f0cc30694e9c8609c23e2b596c5fe260e8b1011fb269f07f97fb0843fda247af`。逐格、输出差异、关闭期异常及解释见[正式报告](WHOLE_STEP_FORMAL.md)。23:00仅为本入口停止启动新格的内部上限，并非用户指定的汇报时间。

## 检查记录与下一步

本地语法/入口检查及配置测试1passed；服务测试因无上游库1skipped，不作为验证。远端五文件测试33passed：whole_step_config/service/envelope/protection、shared_prefix_connector，覆盖真实native回执、共享owner引用与拒绝伪造gate。native产物哈希与固定环境一致。

手动前置校验第一次遗漏scripts的PYTHONPATH而失败，补入后293源/原始输入/分词器复核通过；未启动模型、未修改策略。后续主入口显式设置src/scripts/root路径。

KV、pilot及formal均已完成并归档。TTFT P95是主指标，结合重算/搬运/抢占，保留全部结果；三重复只作描述性比较。下一主线为保护与尾延迟关键请求/排队/抢占的关联，先利用现有数据，不开展无限调参。最终端口8000/8080/5555空闲，GPU1MiB/0%，容器保持开机。

## 可复现入口

服务器在已固定的Conda环境、原模型/tokenizer及native产物下运行，`PYTHONPATH`包含`src`、仓库根和`scripts`。阶段入口只允许推进本阶段准入：

```bash
# out必须为新目录；deadline为带时区的未来时间
python scripts/run_whole_step_mainline.py --stage coverage \
  --input "$INPUT" --output "$COVERAGE" --stop-starting-at "$DEADLINE"
python scripts/run_whole_step_mainline.py --stage kv --admission "$COVERAGE" \
  --input "$INPUT" --output "$KV" --stop-starting-at "$DEADLINE"
python scripts/run_whole_step_matrix.py --stage pilot --prerequisite "$KV" \
  --input "$INPUT" --output "$PILOT" --stop-starting-at "$DEADLINE"
python scripts/run_whole_step_matrix.py --stage formal --prerequisite "$PILOT" \
  --input "$INPUT" --output "$FORMAL" --stop-starting-at "$DEADLINE"
```

当前已归档的coverage→KV→pilot之间存在显式记录的实验工具增量；以上命令说明依赖关系，不能把最终296文件源直接当成最初293文件源重新执行整链。复算既有证据无需GPU：解包证据，检查inventory，分别运行其冻结源中的`audit_whole_step_mainline.py`或`audit_whole_step_matrix.py`，`--output`指定新的报告。所有审计读取原始请求/计时、日志、摘要与前置证据，不只接受归档中的passed标记。

正式比较的核心运行时不在这些harness中重新实现：`runtime/whole_step.py`负责容量包络，`runtime/shared_prefix.py`负责引用保护，`integration/vllm/whole_step.py`显式接线，既有generation与LMCache适配器负责身份/传输终结；scripts仅负责编排、探针和离线审计。
