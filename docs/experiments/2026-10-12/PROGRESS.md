# 2026-10-12 持续推进记录

用户授权推进至北京时间 15:00，阶段完成后继续下一阶段；到时安全收尾并汇报。

| 北京时间 | 完成步骤 | 证据与下一步 |
|---|---|---|
| 13:09 前 | 真实 generation manager/connector/RPC 接线，完整 CUDA/native 805 测试通过 | `GENERATION_BRIDGE.md`；进入独立 GPU 诊断 |
| 13:16 | 诊断 b/c 通过；c 包含逐 wire END、原 reservation/native terminal 和物理 BlockPool 恢复 | 78 代际、589 reservation、69 lookup、75 transfer、909 free；原失败单列 |
| 13:20 | 新栈四配置长 prefill 比较预注册，关闭热路径日志，增加一次性关闭摘要 | 完整 CUDA/native 824 passed、115 subtests；本地审计/配置 26 passed |
| 13:27 | 新四配置 pilot 全部通过，272 请求；本地再次独立复算 4/4 | pilot 不计 formal；启动相同源码签名 12 格 formal |
| 13:44前 | 离线复现CLI、194文件源快照校验、decode192变换测试、SSE观测分析、正式绘图和请求/transfer成本工具完成 | 运行中formal源冻结，工具延后同步 |
| 13:48 | 长prefill正式12/12完成，本地独立审计及阶段/请求成本复算完成，图已视觉检查 | 816正式请求；见GENERATION_LONG_PREFILL.md |
| 13:50 | decode新runner同步，补齐缺失历史测试archive；全套822 passed、115 subtests，显式CUDA另9 passed | 原缺fixture失败及9个显式跳过保留；Windows cost UTF-8读取修复后复算完成 |
| 13:51 | 按13:47预注册启动decode192四格pilot | 14:55启动截止；不改长prefill冻结源码证据 |
| 13:55 | 原decode pilot native通过、immediate两session残留失败，后两格和formal未运行 | 原日志、199文件冻结源码归档；无部分聚合 |
| 14:03 | 原immediate只读复现再次两session残留，定位END后末批STORE重新创建session | 144 reservation/101 transfer闭合；不改门槛 |
| 14:27 | 独立ImmediateReceiptConnector诊断68请求通过，exact session/job/transfer空，909 free恢复 | 原生future与回调契约通过；fixture首次失败单列 |
| 14:30 | 新矩阵门槛与完整回归834 passed/9 skipped/115 subtests，CUDA另9 passed | 首次启动prerequisite路径笔误、未启动GPU；日志保留 |
| 14:31 | 新decode四配置pilot启动；冻结全部源码，关闭热路径日志 | 新预注册，原失败不改写；候选仅同步TP1零保护 |
| 14:32 | 真实结构80文件CPU审计及全部六session/60请求源文件归档 | 尚未GPU验证，不宣称真实压力 |
| 14:39 | 实际Qwen3-4B tokenizer生成六session/60请求，来源/时序/54前缀转换审计通过 | 209,661输入tokens、2,880输出预算、3,262源block无碰撞，跨session前缀为0 |
| 14:40 | 新decode receipt pilot四格全部通过 | 272请求/52,224输出tokens，退出前后exact sets为空 |
| 14:42 | 服务器、本地当前、205项冻结源收集器独立报告完全相同 | 原失败保留，formal十二格尚未启动 |
| 14:48 | 新审计/重建篡改回归、报告与代码导航完善；54份原始文件字节SHA全部一致 | 本地专项15 passed；下一窗口同签名formal，再结构GPU准入 |
| 14:50 | 实现、GPU原始/失败证据与报告提交推送4ed5fc2 | 服务器保留205项冻结源，禁止直接pull破坏formal签名 |
| 14:56 | 新Git archive复核暴露旧fixture未纳入及catalogue换行归一化；补原fixture并保留raw字节后15/15通过 | catalogue原SHA保持不变，修复已推送；GPU源/门槛未修改 |

核心范围不变：单卡同步 text-only，generation 连接器仅零保护；当前
比较原生、立即卸载、默认 fixed 和已选 fixed 的整套配置，不宣称保护收益。
尚未支持多 worker/异步或真实 driver fault。源代码、运行原始日志和失败证据一并归档；服务器仍保留pilot冻结源，不同步后续CPU工具以免破坏formal签名。

14:59安全收尾：端口8000/8080/5555无监听，无GPU计算进程，显存1MiB/利用率0%。服务器未关机，205项pilot冻结源码保留。完整实现/失败原始证据已推送；下一窗口入口见 [NEXT_WINDOW](NEXT_WINDOW.md)。截止不再启动新实验。

## 用户续授的15:56–17:00窗口

- 15:56：重新检查时钟、TASKS、端口/GPU，无其他实验；205项冻结源逐项一致。
- 15:58：同签名decode receipt formal十二格启动，目录 `artifacts/generation-decode192-receipt-formal-20261012a`，16:55停止启动新格。服务器源码继续冻结，不同步CPU开发。
- 16:03：六session真实匿名结构GPU准入试点预注册；新增独立runner、离线审计和0.5秒metrics观测，不把单次诊断速度作为正式收益。
- 16:04：本地结构/来源/矩阵/源快照专项16 passed，包含tokenizer字节漂移拒绝、缺指标为unknown、抢占未知/非零拒绝、原始采样篡改与资源失败不能被通过标志覆盖。结构GPU尚未开始。
- 16:10：新增真实localhost HTTP observer正常/回放异常收尾契约；首次测试把自身scenario名称误匹配为observer而2失败，改为精确后缀识别后18/18通过。新Git导出独立目录18/18再次通过，catalogue及trace原字节保持；代码已推送9794d44。失败是测试任务识别错误，未改变服务/资源门槛。
- 16:28：同签名decode formal十二格完成，816请求/156,672输出tokens。154文件/14,798,960 bytes与服务器一致，服务器/本地/205项冻结源三处独立报告一致；事后成本、阶段分析和图完成。native最低，缓存整套配置+2.29%至+5.85%，一格正常关闭traceback单列；见DECODE_RECEIPT_FORMAL.md。
- 16:29：确认GPU/端口无占用后，只补入五个新增脚本及原结构输入，原205项字节仍一致；启动独立真实结构GPU试点 `real-structure-pilot-20261012a`。有0.5秒观测开销，16:50停止启动新格，不作为正式性能。
- 16:39：真实匿名结构观测4/4通过，240请求/11,520输出，KV采样94.94–99.67%，lazy各2次抢占，exact资源/账本闭合；80文件/63,993,803 bytes与服务器/本地/Git一致，证据推送bed3597。
- 16:40：按16:35预注册启动关闭额外采样的新签名pilot，原服务/策略栈SHA不变。runner新增阶段和无采样证据门槛、本地20项通过；实际cutoff16:52比计划16:50晚2分钟已明确登记，仍按16:50前启动最后格的保守目标推进。formal留下一窗口。

阶段完成后继续下一步，17:00安全收尾汇报；续授窗口的结果另行追加，不改写14:59历史收尾。

- 16:51：无采样结构pilot4/4完成，另240请求/11,520输出，lazy各1次抢占且资源闭合；72文件/19,800,020 bytes服务器/本地/Git一致，三处独立报告一致。210源冻结包已归档，formal未开始。
- 16:53：GPU利用率0%、显存1MiB，无compute进程，8000/8080/5555无监听；容器未关闭。更新NEXT_WINDOW为结构formal确切入口，保留冻结源，不新增GPU实验。

- 16:55：全部新增证据推送82176d2，工作区干净；最终结构/来源/矩阵/源快照专项20 passed。累计本续授窗口完成20个GPU cell、1,296请求、179,712输出tokens；其中正式decode816请求，两个结构pilot各240，不能混作同一性能样本。

## 18:16继续窗口

- 18:16：用户再次授权继续；服务器GPU/端口空闲，210项无采样pilot源码完全相同、无额外文件。同签名匿名结构formal十二格启动，目录`real-structure-formal-20261012a`；19:30为本次runner的操作性超时截止，不是新的用户定时授权。
- 18:18：本地添加全部六source session的事后描述工具；请求遗漏/重复、失败或错误会话归属均拒绝，专项15 passed。仅本地开发，服务器源码保持冻结。

- 18:46：匿名结构formal12/12完成，720请求/34,560输出tokens；服务器210源完全一致，186文件/47,173,551 bytes逐项核对通过。
- 18:49：服务器/本地/冻结源报告完全相同；全六source session与请求阶段/搬运成本分析完成，图已视觉检查。native TTFT P95最低，缓存组高5.76–11.33%；一格退出traceback及输出hash差异保留。
- 18:50：服务器GPU空闲、端口无监听。更新总体计划，补generation与非零保护交接设计；本轮未修改GPU策略或删除零预算guard。临时复查命令默认GBK失败已显式UTF-8复核通过，正式分析工具原本已使用UTF-8，无源码修复。

## 19:23–19:29 generation与保护交接

- 新增独立CPU候选脚本与18项组合测试，未修改原零保护服务链。a/b/c/d逐步回归分别7/40/59/60 passed，原日志及SHA归档；最终仅d对应本次最终源码。
- 完成prefix/suffix原票据交接、原回执、跨代success/failure、idle suffix drop、异常后unresolved。发现native注册后helper仍PREPARED的回滚窗口，新增poison隔离及真实scheduler异常包装契约，保留pin且禁止END，必须重建实例。
- 本轮未运行GPU推理；19:29 GPU 0%/1MiB。端口检查首次使用ss但环境无该命令，不能以该次输出证明无监听，随后使用/proc/net核查。服务器仅新增候选及测试，历史210源冻结包继续保留。
- 更新状态、任务、阶段计划和下一窗口入口；Connector接线、GPU bitwise与资源准入、策略消融仍开放。
- 收尾：/proc/net/tcp与tcp6确认8000/8080/5555无监听；源码及四份日志远端/本地SHA一致。首次staged whitespace检查发现本地manifest使用CRLF（原日志未改），追加统一manifest为LF；重新检查通过。

## 20:38 独立Connector接线窗口

- 新增GenerationProtectionConnector及显式opt-in，父类增加保持默认行为的工厂；保护helper bind实际bridge，原native metadata/worker envelope/scheduler receipt交接。旧zero类guard保留。
- 新增12项接线测试，含one/two成功失败、wrong/duplicate、suffix默认drain、双reset旧代回执、zero步、constructor工厂、注册/封装/native free后异常隔离。transport与model/copy在接线测试中stub，不宣称真实服务完成。
- 迭代回归35、157、863/9 skipped/115 subtests；最终显式CUDA/native全套873 passed、115 subtests、121 warnings，18.57s，无跳过。四份原日志逐字节核对归档，不能相加；最终新增receipt异常测试包含于873。
- GPU回归结束0%/1MiB，未启动新组合模型服务。多候选部分失败、服务进程故障退出及shutdown snapshot失败路径仍须补齐，再独立GPU bitwise/resource诊断和策略消融。

## 20:52 故障门槛推进

- 新增真实双候选prepare与第二次register前/后故障，验证先前提交与尚未完成helper状态的pin均不被错误回滚。shutdown新增只读失败snapshot保存；写证据失败以note保留原错误。
- 首轮3 failed/32 passed是新fixture sequence碰撞，独立sequence修正后显式CUDA/native全套878 passed、115 subtests，19.74s。原失败及最终日志SHA归档。
- 回归后GPU 0%/1MiB，未运行模型服务；CPU shutdown测试不等于实际EngineCore故障退出。下一步具体GPU诊断入口要求写入GENERATION_PROTECTION_FAULTS.md。

## 21:04 新组合正常GPU准入

- 新诊断probe/runner沿用固定348请求输入，原始plan先记录预算/模型/214源码签名。a-zero的请求/KV/END通过，但旧status摘要过滤observer，精确集合证据缺失，停止one/two并保留失败。
- b补只读observer采集，新目录zero/one/two全部通过：1,044请求，720/720/864层全等，one/two保护11/9次，最终pin0、BlockPool909 free，精确generation资源闭合。三格同payload，输出文本差异0。
- 84原始文件16,611,799bytes与服务器逐项一致，214源码tar冻结；本地独立重算a=false、b=true。新增篡改测试与generation审计14 passed；GPU空闲0%/1MiB。无服务fault、长prefix专项或性能结论。

## 21:11 真实服务故障窗口

- after-commit探针在原native注册后/worker handoff前抛一次主机异常。a实际EngineCore/API自行退出但无snapshot，整体失败保留。将只读快照前移至异常捕获，再b验证通过，保留16个在途引用/原票据，无目标rollback/END/worker handoff。
- 回调测试fixture import初次遗漏导致14 pass/1 error，修正后15 pass；完整服务器显式CUDA/native879 pass、115 subtests，19.34s。本地独立/篡改审计18 pass单列。
- a/b27原始文件906,696bytes远端/本地一致，b216源码冻结tar归档；回归后GPU0%/1MiB。当前为受控主机异常，不是driver/DMA故障、正常归零或自动恢复。下一步长prefix/取消抢占/失败回执GPU专项。

## 21:21 长prefix与失败回执窗口

- runner添加互斥long-prefix/failed-receipt独立分支，原正常默认三格不变。长prefix348请求/1800层全等、9split/8严格四chunk链/9派生票据；54 reservation/36 transfer闭合。22原文件5,044,642bytes、216源码核对冻结。
- failed-receipt独立348请求/864层全等、9保护STORE中1成功后报告失败，32pin回执前存在、最终0，26 reservation/26 transfer闭合；预期adapter ERROR对应目标，原日志保留。22文件4,222,630bytes、217源码核对冻结。
- 两格generation各348精确END、BlockPool各909；独立本地审计通过。离线lineage/split/normal/fault测试19 passed，不重复全套CPU。GPU0%/1MiB。下一步共享取消/实际抢占；本次无长suffix失败联合或性能结论。
