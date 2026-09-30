# 真实文件部分写入与 EFBIG 错误

## 方法

在全新单客户端服务中，使用原生 `LMCacheMPConnector`、立即卸载和真实 FS L2，固定 Qwen3-4B、eager。实验 wrapper 在第一个真实 `_execute_store` coroutine 期间，将 server 进程的 `RLIMIT_FSIZE` 临时设置为 1 MiB，并忽略 SIGXFSZ，使内核把超限写入报告为 `OSError: [Errno 27] File too large`。原 coroutine 返回后恢复原限制。该限制只作用于本实验子进程，未填满磁盘，也未改写正式 LMCache 安装文件。

原始 FS adapter 自己捕获实际写入错误、删除临时文件并返回失败；wrapper 只记录删除前长度和 `pop_completed_store_tasks` 的真实结果，不覆盖 future 返回值。此为**受控真实操作系统写入失败**，不称自然磁盘损坏。

```bash
python scripts/l2_write_fault_smoke.py --output artifacts/l2-write-efbig-2026-09-30-r2
```

## 结果

第一个 STORE batch 有 8 个对象，每个应写 37,748,736 bytes，实际临时文件写至 1,048,576 bytes 后失败。8 个 `.tmp` 都由原 FS adapter 删除，对应 8 个 `.data` 均不存在；其后两个正常批次保存了另外 9 个对象。失败 batch 的真实 `L2StoreResult` 为 `success=false`、`bytes_transferred=0`，这个零值表示完整成功对象的计数字节，不能解释为操作系统没有写过部分字节。

| 检查点 | 读锁/写锁 | STORE pending/in-flight | lookup job/result | 磁盘 |
|---|---|---|---|---|
| 故障处理后 | 0 / 0 | 0 / 0 | 0 / 0 | 9 个完整后缀文件，0 临时文件 |
| 原 vLLM 正常退出后 | 0 / 0 | 0 / 0 | 0 / 0 | 同上 |
| 清空 L1、重启原生 server 并恢复请求后 | 0 / 0 | 0 / 0 | 0 / 0 | 17 个完整文件 |

恢复请求外部 prefix hit 为 0；存在后缀文件不允许跳过缺失前缀。32-token 输出与故障前一致，恢复后每个文件均为 37,748,736 bytes。验收 `passed=true`。见 [r2 结果](l2-write-efbig-2026-09-30-r2/result.json)、原始状态与事件；完整日志已备份本地 artifacts。

首轮 r1 的实际故障也命中，但实验错误假设一个 prompt 的 17 个对象会放在单个 STORE batch，断言失败。已保留 [r1 失败记录](l2-write-efbig-2026-09-30-r1/result.json)，并改成按真实注入 batch 的对象数验收，不把 r1 记作完整通过。

## 与 worker STORE 失败的区别

GPU→L1 已先成功，异步 L1→L2 才遭遇 EFBIG。因此生成正常、worker STORE 不必返回失败；L2 StoreController 独立释放其 L1 读锁，缓存降级为仅 L1 可用。这个结果验证了 L2 部分写入清理及重启后的 prefix 行为，**不覆盖 worker GPU DMA/传输失败、scheduler 失败回执或在途 STORE 抢占**。这些继续列为独立验收项。
