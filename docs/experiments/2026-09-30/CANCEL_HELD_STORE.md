# 取消请求与迟到 STORE 回执

## 方法

在单请求生成期间使用EVICTION_AWARE（max_deferral_seconds=0.05、每步最多drain一请求）尽早发起STORE。`LateStoreReceiptConnector`把真实future的可见回执延后5秒，不改变其布尔结果；观察到该批次已提交且请求尚未结束后，主动断开流式HTTP客户端。此后直到scheduler资源闭合，不发送tick或follow-up。

这是受控回执延迟和客户端取消，**不是中断GPU DMA**。GPU→L1可能早已完成；验证目标是scheduler继续持有pin期间的取消和终结清理。模型Qwen3-4B，RTX4090 24GB，eager，GPU KV 2GiB。

```bash
python scripts/cancel_held_store_smoke.py --output artifacts/cancel-held-store-2026-09-30-r1
python scripts/cancel_held_store_smoke.py --observe-after-stop 630 \
  --output artifacts/cancel-held-store-2026-09-30-r2
```

## 已完成结果

r1通过：唯一请求进入 `FINISHED_ABORTED`；取消约4.825秒后scheduler收到真实成功回执，128个物理block的pin引用各减1，空闲块从909恢复909，请求表与deferred frees归零。此前没有第二个请求，证明此路径不依赖follow-up触发收尾。之后短follow-up生成32tokens，引擎退出后注册、锁、job、result和队列归零。见 [结果](cancel-held-store-2026-09-30-r1/result.json)。

结束时尚有1个session条目，r1没有等待TTL，不能称所有条目立即清空。r2重复通过，128个pin闭合、909free恢复、回执在取消后4.825秒到达；完整630秒观察中600秒session仍为1，615秒首次采样为0，630秒仍为0，锁/job/result/注册始终已清零，`ttl_passed=true`。见 [r2结果](cancel-held-store-2026-09-30-r2/result.json) 和 [TTL采样](cancel-held-store-2026-09-30-r2/post-stop-observations.json)。`audit()`另有5项单元测试（含7个子场景），拒绝正常长度结束、回执早于取消、额外tick请求、重复批次和不完整资源释放等假通过情形。
