# CachePilot 2026-09-30 进展报告

当前结论：**3B仍未全面通过；已把资源缺口收敛到具体所有权边界。3C已推进到真实库CPU契约，GPU保护策略尚未启用。** 不需要更换GPU或由用户决定新的技术方向。

## 今日完成的工作

1. **真实FS L2取消与失败对照。** 两轮回收候选在load终结后逐对象释放17个锁、消费job/result；关闭候选的对照保留17锁和1个job/result。真实文件短读进一步验证只回收成功保留的前8对象；对照残留8锁。follow-up输出一致。
2. **LOOKUP/END顺序。** 真实客户端adapter保留pending ack/status，让原END等待确认，一轮组合服务实验及契约测试通过；这是同步诊断候选，会阻塞scheduler，不能当作生产顺序协议。
3. **无END死亡、TTL与关闭。** 杀死实验vLLM进程组后观察630秒，锁/session虽到期，但job/result/key snapshot仍各1，17 temporary对象占612MiB。另将注册grace设120秒确认旧GPU注册能清除，但不能带动prefetch回收。可取消controller关闭释放L1资源，但候选abandoned bookkeeping未终结，仍记失败；早期telemetry超时轮保留。
4. **真实OS部分写入失败。** server临时RLIMIT_FSIZE导致EFBIG，8个36MiB目标文件实际各写1MiB后失败，由原FS adapter清理；重启后prefix miss、输出一致并恢复17完整文件。这是L1→L2故障，未冒充GPU worker STORE失败。
5. **自然抢占与迟到回执。** 无reset API的容量压力引发抢占。普通两轮11/12次；5秒回执延迟两轮各16次、8请求均完成、各1120 pin引用闭合。新增manager reset时序解释旧代批次为何成为orphan。最新轮引擎退出后630秒TTL观察通过。
6. **取消期间迟到回执。** 两轮在STORE回执被持有时断开客户端；FINISHED_ABORTED后约4.825秒收到回执，128个pin释放、909free恢复。期间不发tick，重复轮615秒首次观察session归零，630秒保持清空。
7. **实际KV内容。** 自然抢占后重发prompt，首轮40个回载完整chunk、1440层比较全部逐字节一致；严格来源审计确认1个chunk/36层来自orphan批次，且中间没有再次STORE。1秒延迟轮1296层比较也全等，但没有覆盖目标orphan源，因此专项失败、原始证据保留。最终5秒重复再次通过40chunk/1440层比较及1chunk/36层严格来源，见专项报告。
8. **3C接口实现。** 独立状态机、16:1 chunk映射、真实BlockPool的CPU桥接、双hash token provenance、真实LMCache STORE metadata交接契约已实现。真实Request追加decode与原生tracker的两个连续STORE范围对照通过。修正本项目CPU桥接误读累计`block_hash_num_tokens`的缺陷。

## 仍未解决的核心问题

无END客户端死亡时，没有安全协议把prefetch job、controller完成结果和read reservation交给回收方。简单按session TTL解锁不安全：原生锁测试证明旧key/count释放可能误减新reader的锁；另两项真实接口测试确认部分释放失败只写入事件，上层返回None。23:00后续推进已为可选候选增加逐对象结果检查，部分失败保留且不自动重试，详见下方补充；reservation/epoch仍未实现。

因此下一轮以 **reservation身份/epoch、job generation、controller终结和逐key释放结果** 为主线，形成可维护补丁与真实契约回归，见 [所有权协议](../../LOOKUP_OWNERSHIP_PROTOCOL.md)。实际GPU传输中止、DMA执行时抢占、不可取消底层I/O和更广输出正确性仍未覆盖，不能删掉这些验收项来宣布3B通过。

3C可以继续CPU接口工作；GPU接入、保护预算调优、公平性与性能消融仍在3B门槛之后。当前没有新的TTFT提升或最终策略收益结论。

## 证据和验证

- 逐项状态与链接：[验收矩阵](VALIDATION_MATRIX.md)。结果文件及SHA256：[结果索引](result-index.json)。
- 环境版本与9个关键上游源码文件SHA256已保存于 [最终环境快照](environment-final.json)。
- 完整项目测试：服务器169通过，18子测试通过；117条警告来自上游弃用API。本地117通过、52项因缺少真实推理栈而跳过，18子测试通过。
- 新增脚本通过实机执行和相应审计测试，配置JSON、文档链接、`git diff --check`已检查。
- 原始日志、完整KV探针记录在本地/服务器artifacts；Git保留摘要、关键事件及失败结果，不提交模型或大体积KV文件。
- 上游仅只读复核issue/PR状态，没有发布评论、issue或PR；最终是否贡献上游仍按项目阶段收尾计划执行。

22:48检查：本轮服务进程已退出，8000/8080/5555均未监听；GPU 1MiB、利用率0%，服务器保持开机。功能代码与实验报告提交 `b5d67a7` / `27da66a` 已推送GitHub，服务器121个脚本/测试/配置的SHA256与代码提交一致；24份结果索引通过校验。22:58最终复查结果相同，见 [收尾状态](closeout-state.json)。今晚续跑提醒已暂停；23:00按约定汇报，下一轮等待用户继续。

## 23:00 后继续推进

用户随后授权继续。已实现可选逐对象释放检查，9项native CPU回归与5项审计测试通过；真实L2取消释放17对象、短读释放前8对象两轮通过，资源清空且后续输出一致。详见 [CHECKED_RELEASE.md](CHECKED_RELEASE.md)。最新完整回归为服务器183通过，本地122通过/61跳过，均26子测试通过；这是增量交付，不改变3B未完成的结论。提醒保持暂停，服务器保持开机。
