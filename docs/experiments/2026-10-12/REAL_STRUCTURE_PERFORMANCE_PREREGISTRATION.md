# 匿名结构：关闭额外采样后的重复预注册

2026-10-12 16:35，观测试点尚在运行，登记后续条件，不从单格速度选择配置。只有观测四格全部通过来源/资源/H2D/采样/立即卸载零抢占门槛，才允许下一阶段。

保持全部六session/60请求、trace SHA `445b05df9898232b97d2a0f7a0fc7633a42ab8a4d712943ee3f70ebb6c2acb83`、模型/硬件、2GiB KV、16GiB CPU L1、四配置和全部原测量/服务门槛。新签名仅将0.5秒额外metrics采样关闭，观測runner/auditor可增加阶段入口；此前所有服务/策略/原回放测量文件SHA必须与准入一致。不改变保护预算、horizon、drain、idle grace或源输入。

先 `--stage pilot --admission OBSERVED_DIR` 四格冷启动，保留完整源码签名和关闭采样的证据；即使之前观察pilot成功，也不能直接当作无采样formal准入。四格通过并离线复算后，才可 `--stage formal --admission OBSERVED_DIR --pilot UNOBSERVED_PILOT_DIR`。formal要求与无采样pilot完全相同的scripts/configs签名，四配置三次、每轮向左旋转3位。任何格失败停止，保留全部失败与未运行格，不输出部分矩阵均值。

收集请求/SSE计时、实际prefill/hits/preemption、H2D/D2H及资源、输出hash差异。指标以三次P95的均值和样本SD呈现，不把有观测开销的试点混入；无额外采样的formal也不借用试点峰值冒充本轮测得的峰值。不使用合成trace的early/pressure/revisit划分。需要阶段/会话描述时按原六个source session分别报告，不事后选赢家。

本窗口17:00截止；最多在观测四格完成后推进无采样pilot（预计约10分钟），16:50停止启动新格，预留归档收尾。formal十二格若不能安全完整结束则留待下一窗口，保留服务器的无采样pilot源码，不追加修改破坏其签名。

16:40启动记录：实际传入的 `stop-starting-at` 为16:52，较上述计划晚2分钟，明确记录这一执行差异；仍在用户17:00授权窗口内。若第四格不能在16:50前启动，优先按原计划停止新增格，不用这2分钟追加formal。
