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
