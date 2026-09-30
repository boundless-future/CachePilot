# 逐对象释放结果：回收候选的有限改进

2026-09-30 23:00 汇报后，用户继续授权推进。本轮完成了显式逐对象释放检查，并通过正常 L2 取消与真实文件短读两轮实机验证。**3B 仍未完成；本改动不解决无 END 客户端死亡和跨 TTL 后的锁归属。**

## 实现与边界

此前 `StorageManager.finish_read_prefetched()` 把逐 key 失败写入事件，但返回 `None`。候选若只判断调用未抛异常，就可能把部分失败也记为完成。

新增 `scripts/checked_prefetch_release.py`，在可选 `--reclaim --checked-release` 模式下直接调用固定版真实 `L1Manager.finish_read()`，核对返回结果覆盖全部请求 key，保留成功集、失败集和具体错误，并发布与原 StorageManager 相同的完成事件。安装时校验两个上游源文件 SHA256；没有修改已安装的 LMCache，旧对照模式仍可复现。

- controller 未完成时不释放；只使用完成 bitmap 中实际保留的对象。
- 部分失败保留 abandoned job、bitmap 和成功/失败结果，不增加 completed 计数。
- 事件通知失败时底层释放可能已成功，因此保留成功集与通知异常，不重复解锁。
- 释放中途抛异常或返回不完整结果时，标为状态不确定；不自动重试整组 key。尚无自动恢复流程。
- 仍使用匿名 key/count，无法辨别 TTL 后重新加锁或对象替换。该模式仅用于已登记 LOOKUP、明确 END、controller 可终结且未跨锁 TTL 的实验窗口，不能作为完整失联回收修复。

## 真实服务实验

固定环境同 [环境快照](environment-final.json)：RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0、LMCache `1a4b40b1`；eager、原生 `LMCacheMPConnector`、候选 server、真实 FS L2，使用屏障控制取消时机。

| 实验 | 逐对象结果 | 资源与功能 |
|---|---|---|
| [正常 L2 在途取消](l2-checked-release-2026-09-30-r1/result.json) | 17 个不同 key 全成功；失败集为空 | 锁/job/result/快照及队列清空，后续输出一致，引擎退出后 GPU 注册清空 |
| [第 9 个文件短读](l2-checked-short-read-2026-09-30-r1/result.json) | 36 MiB 文件截为 18 MiB，只释放前 8 个实际保留对象；失败集为空 | 资源同样清空；恢复文件后 follow-up 命中 4352 token，输出一致 |

短读由真实文件截断触发，但时序受控；后续请求是在恢复文件后发出。两轮均不代表自然磁盘故障或 GPU DMA 中断。短读中的“8 个对象全部释放”与“释放接口部分失败”是不同场景，后者由下述 native CPU 测试覆盖。

事件验收同时核对实际 load 对象、job token、request ID、时间顺序、完成 bitmap 对应 key 集和逐 key 释放结果，不能用数量相同代替对象一致。正常轮启动后审计逻辑曾加强，保留原始 result，并用最终逻辑对原始事件补做 [严格离线审计](l2-checked-release-2026-09-30-r1/strict-audit.json)；短读轮直接使用最终逻辑。两个目录的 `events.jsonl` 保存全部屏障、server 和 reclaim 事件；完整日志在两端 artifacts，未提交大体积 KV 文件。

复现命令（先确认实验端口空闲，在 cachepilot conda 环境）：

```bash
python scripts/l2_prefetch_cancel_smoke.py --reclaim --checked-release --output artifacts/l2-checked-release-new
python scripts/l2_prefetch_cancel_smoke.py --reclaim --checked-release --truncate-index 8 --output artifacts/l2-checked-short-read-new
```

## 回归与下一步

新增 9 项真实 native CPU 契约测试，使用真实 TTLLock、L1Manager 和 StorageManager；allocator/event sink 模拟。覆盖 pending→terminal、部分释放失败、成功后新 reader 到来不能被重复释放、通知失败、中途异常、非法输入、不完整结果及空 bitmap。另有 5 项事件审计测试，拒绝错误 key/job/顺序、重复事件与不完整结果。

完整服务器回归 **183 passed、26 subtests passed**（117 条上游弃用警告），见 [测试输出](checked-release-pytest.txt)；本地 **122 passed、61 skipped、26 subtests passed**，跳过项需要真实推理栈。

下一步将 release 绑定不可复用 reservation 身份与对象/锁 epoch，并规定 controller 终结后的消费/移交接口，再处理无 END 失联时由谁接管 job。保持已有 TTL 反例作为必须通过的回归；不把本轮显式结果检查表述为所有权修复或性能优化。3C GPU 接入仍受 3B 资源门槛约束。

23:24 收尾检查：8000/8080/5555 均无监听，GPU 无计算进程、显存 1 MiB、利用率 0%；实验服务已退出，服务器保持开机。26份结果索引的 SHA256 及更新文档链接检查通过。
