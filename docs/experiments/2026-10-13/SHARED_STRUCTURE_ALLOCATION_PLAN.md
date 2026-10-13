# 匿名结构：实际物理分配独立诊断

2026-10-13，北京时间约13:28预注册。原四组结构pilot仍为3/4，双chunk零覆盖失败；不启动formal。此前只读窗口诊断两格闭合，仅用于进一步机制观测。

保持原60请求匿名结构、arrival、48输出token、Qwen3-4B、2GiB GPU KV/16GiB CPU、h5/d1、one16/two32预算与现有reserve完全不变。one/two各运行一次，顺序固定；仅在245项冻结源之上新增allocation connector/runner/auditor三项，不改变既有策略。禁止根据结果调reserve、改输入或补跑求覆盖。

在每次prepare至build_connector_meta返回之间，包装真实BlockPool.get_new_blocks，保留参数、返回对象和异常，只记录成功调用的block IDs、调用前后free及总数量。同时记录实际usable pool容量、原reserve、步编号、总调度token数。零token步也必须闭合；边界外调用或上一步未结束直接报错，不静默遗漏。失败调用不计成功分配。此诊断有记录开销，不计性能。

审计核对窗口/分配记录逐步一一对应、时间先后、free/reserve、成功分配数量及唯一IDs、物理空闲队列变化、原请求与资源、generation及原END/回执。报告gross分配总量、零token分配步数、reserve超过物理容量的步数和reserve小于gross分配的步数。后者仅是观测，不等同于安全性失败或安全上界证明。

gross成功分配不是净消耗、峰值占用或反事实可pin余量；未覆盖touch/free的逐时序、未来分配、异步多worker及其他输入，不据此缩小安全reserve。不能以此有探针运行因果解释先前无探针零覆盖，也不能替代失败pilot。

本地审计9 passed、真实LMCache依赖测试文件1 skipped；远端真实BlockPool观测器4项和审计6项合计10 passed，66条依赖弃用warning。远端测试不加载模型，未重跑历史961项GPU/native基线。初次远端测试命令因PowerShell PATH转义失败未执行测试，改用绝对解释器后通过。

14:25后不启动新格，非覆盖类故障停止；原零覆盖result保持失败、诊断只在完整资源/证据门槛下单列。完整日志/source/manifest归档，服务器、本地、冻结源独立复算。
