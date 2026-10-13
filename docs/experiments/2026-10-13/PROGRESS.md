# 2026-10-13 推进记录

## 12:40–14:30：持续推进与截止收尾

用户授权今日北京时间14:30前持续推进；复用heartbeat cachepilot-15，14:25后入口不再启动新格，14:30安全收尾汇报。先检查本段最新状态及远端进程，避免重复实验。

- 12:40读取既有六session匿名结构来源和旧formal，确认输入是token ID数组、60请求/209,661tokens，自然抢占产生额外generation。
- 独立新增run_shared_structure_matrix.py / audit_shared_structure_matrix.py，原240项源不改；预注册SHARED_STRUCTURE_PREREGISTRATION.md。
- 新入口按61唯一raw请求（含warmup）及61+实际preemption代检查；来源/tokenizer文件SHA、ID范围、长度及完整重建审计。首次反篡改raw ID测试暴露仅计数不能验证wire绑定，补wire后缀身份检查后30项通过（结构11+原共享19）；原失败保留在本记录，不伪造原工具日志。
- 12:45已启动远端pilot：artifacts/shared-structure-pilot-20261013a，日志同名.log；工具会话48632。截止14:25。先等4/4及三处审计，全部通过才formal，不重复启动。
- 原入口/预注册/测试已提交推送1e45431。
- 12:57 pilot结束，3/4通过；shared-two0提交/7capacity回退，其他请求/资源/62代全部通过。one1次提交/2次抢占/63代。formal未启动，不能重跑失败矩阵筛选。
- 完整80文件/242项冻结源包已本地归档且三处复算一致：shared-structure-pilot-evidence.tar.gz。报告SHARED_STRUCTURE_PILOT.md，原output hash差异保留。
- 离线需求分析四格均52/60完成请求后有相同首256/512tokens需求，6个唯一prefix，不能等同实际受保护回载或收益；6项测试通过。
- 13:01起独立只读windows诊断one/two，原242源不改，仅新增3文件；目录artifacts/shared-structure-windows-20261013a，日志同名.log，工具会话36113。非性能数据，允许原result coverage=false但诊断资源/逐身份必须通过。先读日志，避免重复启动。


- 13:08 windows诊断两格结束，各1提交/7capacity回退；所有原请求/资源/62代及36/33批原回执闭合。没有16可放32不可放路径，不能据此改预算；245源/51文件三处复算一致。
- 13:21起新增只读actual allocation观测和审计，原245源不变；本地9 passed/1依赖文件skipped，远端真实BlockPool4+审计6合计10 passed。首次测试命令PATH转义错误未执行，绝对解释器重试通过。
- 约13:26–13:31 allocation两格完成，1,766/1,763步及3,219/3,337新分配块全部核对；one第794步0计算tokens却分配83块。reserve超过909块分别450/456步。完整51文件/248源三处复算一致；该诊断仍不能证明新reserve或性能。
- 13:32–13:33真实KVCacheManager容量契约首次因新版get_computed_blocks三返回值误按两个解包而3 failed/5 passed，修正测试接口后8 passed，原日志归档。纯回载/本地命中touch/混合命中及native保留门槛覆盖，无模型计算。
- 13:42 native每次准入观测相关远端18 passed（观测4+容量8+审计6），本地两类审计12 passed；248源上新增4文件。13:43启动artifacts/shared-structure-native-20261013a，工具会话43002，one/two各一格，非性能；原pilot保持失败。等待完成再独立归档，不重复启动。
- 后续native两格完成，各35/35检查通过：调用2,992/2,987，成功2,901/2,902、拒绝91/85；新分配3,219/3,324块、空闲命中touch7,933/7,376块。252项冻结源、51原始文件完整归档；服务器/本地/冻结源复算一致。补充请求身份/时间审计各61 raw IDs通过，报告SHARED_STRUCTURE_NATIVE.md。
- 真实manager顺序/驱逐契约4 passed，真实Scheduler reservation契约1 passed。约14:07收到Scheduler序列测试2 failed/2 passed，原因是用相同前缀模拟独立冷输入；中间观察预期版本4 passed，最终区分独立/共享输入6 passed。a/b/c日志全部归档，不将测试夹具错误解释成产品故障。
- 最终Scheduler证明：独立冷输入及外部回载中多占1块可阻止第二请求；共享前缀组复用本步32tokens而仅计算余下16，两种引用条件均准入。真实Scheduler、manager、BlockPool；无模型计算，外部命中回调是夹具。原生单调用余量不能替代整步reserve，设计NATIVE_ADMISSION_BOUNDARY.md已更新。
- 14:21本地窗口/allocation/native/identity审计回归19 passed。未新增GPU性能格、未降低reserve、未改变原pilot资格；当前进行最后证据与提交收尾。
- 14:26收尾：远端相关回归33 passed、66 dependency warnings（不同于本地19项范围），原始日志native-boundary-final-tests.txt归档；不重跑历史全套。GPU1MiB/0%、相关实验进程为空，/proc/net/tcp及tcp6确认8000/8080/5555无条目。ss不可用、python默认路径不存在及一次SSH引号错误后改用proc只读核验，未启动新服务。
- 主提交cefeeb5已推送origin/main，包含本轮67文件与原始失败；工作区干净。下一步按NEXT_WINDOW补混合命中/owner变化完整调度契约，先明确新候选安全边界。服务器保持开机；到14:30暂停heartbeat并汇报。


## 较早：推进下一阶段

1. 冻结四组历史输入预注册与240项源，新增pilot/formal入口及独立原始计时/资源审计。
2. 远端入口首次缺历史测试文件，服务未启动；补齐选择性同步fixture后27项通过，本地补零覆盖反篡改回归后37项通过，范围单列。
3. pilot4/4通过，272请求，one/two提交4/3批；70文件字节及服务器/本地/冻结源复算一致。
4. 不改源码/参数，formal轮转三重复12/12通过，816请求/39,168输出tokens；候选各4/4/4批，资源/generation闭合。
5. TTFT P95均值−1.52%/−1.58%，prefill和H2D不变，two在r1退化；输出差异和default-zero-r2关闭traceback保留，不宣称稳定/泛化收益。
6. 正式188文件/240源三处复算、成本分析及报告归档；两包共258文件。最终源未漂移，三个端口空闲、GPU1MiB/0%，容器未关闭，自动化未开启。
7. 更新TASKS/STATUS/PROJECT_PLAN与下一窗口；主线为既有匿名结构输入的共享候选pilot→formal，联合异常与完整抢占恢复继续独立待办。

详见 [正式报告](SHARED_STRATEGY_FORMAL.md)、[pilot](SHARED_STRATEGY_PILOT.md)。

## 之前：共享前缀限定正确性准入

前一轮用户授权“推进1–3”，该轮没有运行性能实验。

| 顺序 | 工作 | 结果 |
|---|---|---|
| 1 | 独立 SharedPrefixConnector/service helper 与引用日志 | 默认旧路径、CPU bind拒绝不变；76→85相关契约通过 |
| 2 | normal zero/one/two | 1,044请求；720/720/864层KV全等；原资源/generation闭合 |
| 3 | 共享取消、失败回执、自然共享受害请求抢占、raw ID复用 | 四格通过，936/864/144/936层全等；复用4次旧回执拒绝 |
| 4 | 全量同步KV历史trace及只读窗口 | 两次0保护失败，原68请求/资源闭合，保留反例 |
| 5 | 无KV探针的同trace覆盖 | 3次保护前已有共享引用，coverage通过、无bitwise声明 |
| 6 | 稀疏保护KV探针 | 4次提交但原trace不加载这些前缀，严格来源失败 |
| 7 | 原trace后追加诊断驱逐/恢复尾段 | 323额外请求；3次共享保护提交，1批/36层严格STORE→H2D全等；392代/131reservation/55transfer闭合 |
| 8 | 双共享候选原回调部分提交故障、篡改审计补强 | 最终CUDA/native961 passed、115 subtests、无skip；本地13项通过 |
| 9 | 原始/失败/源码归档，服务器/本地/冻结源复算 | 305原始文件SHA一致；七格及两项新文本补充结果一致 |
| 10 | 文档、计划和下一窗口更新 | 报告SHARED_PREFIX_ADMISSION.md；保持性能和联合异常组合待办 |

运行中服务器源码保持冻结；后续补充使用新目录和新签名。最后源包包含报告字段修正（reuse阶段/回执门控/fault范围），旧run plan原样保留。所有服务结束后GPU1MiB/0%、三个端口空闲；未关容器、未启用自动化。
