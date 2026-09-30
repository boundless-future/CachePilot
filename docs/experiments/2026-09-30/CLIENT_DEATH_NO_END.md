# 无 END_SESSION 的客户端死亡：TTL 不等于完整回收

## 方法与范围

在固定 RTX 4090 24GB、Qwen3-4B、eager、原生 `LMCacheMPConnector` 上，使用真实 FS L2 和可选 server reclaim wrapper。在 lookup 屏障处对本实验创建的 vLLM 进程组发送 SIGKILL，不走正常 END_SESSION；随后释放 lookup 和 load 屏障，让真实文件读取完成，观察 630 秒。此为受控进程死亡，不是自然网络故障。

```bash
python scripts/l2_prefetch_cancel_smoke.py --reclaim --crash-client \
  --observe-seconds 630 --output artifacts/l2-client-death-2026-09-30-r1
```

原始摘要、42 次观察和状态快照见 [client-death-r1](client-death-r1/result.json)。完整日志已备份到本地 artifacts；612 MiB KV 文件不入库，result 中保留逐文件 SHA256。

## 结果

| 时间/事件 | 读锁 | session | job | completed result | 旧 GPU 注册 |
|---|---:|---:|---:|---:|---:|
| 实际 load 完成 | 17 | 1 | 1 | 1 | 1 |
| 300 秒观察点 | 0 | 1 | 1 | 1 | 1 |
| 615 秒观察点 | 0 | 1 | 1 | 1 | 1 |
| 630 秒观察点 | 0 | 0 | 1 | 1 | 1 |
| 重启 vLLM、follow-up、正常退出后 | 0 | 0 | 1 | 1 | 1 |

观察阶段没有 END_SESSION。读锁 TTL 为 300 秒，会话由其独立 TTL 清理；但 job、完成结果、候选 key snapshot 均仍为 1。630 秒时 L1 仍有 17 个 temporary 对象，占 641,728,512 bytes（612 MiB）；这表示观察窗口内没有回收，不能外推为永久不可驱逐。`registered_gpu_ids` 保留旧 ID，不能单凭这个计数推断仍占多少 CUDA 显存或判定注册泄漏：固定版有独立 worker reaper，默认已 PING worker 超时 120 秒、尚未 PING 的注册宽限 3,600 秒，本轮 630 秒未覆盖后者。注册回收必须另测，不能与 job 缺口合并归因。

恢复请求外部命中 4,352 tokens，32-token 输出与 warmup 完全一致。`passed=false`、`resource_passed=false`；功能恢复不能抵消资源验收失败。实验服务最终停止，GPU 回到 1 MiB 占用。

## 注册回收对照

追加 `--registration-grace-seconds 120 --observe-seconds 180`，其余保持相同，见 [对照结果](l2-client-death-grace120-2026-09-30-r1/result.json)。120 秒观察点旧 GPU 注册归零；180 秒时注册仍为 0，但 session/job/result/key snapshot 均为 1、读锁仍 17（还未到 300 秒 TTL）。follow-up 命中 4,352 token、输出一致。证明该版本的注册 reaper 在此配置下能工作，不能把前一轮宽限期内的注册存在误报为永久注册泄漏。它没有驱动 prefetch 的释放路径。

## 对项目的影响

当前候选依赖 END_SESSION 移交 job 所有权；客户端死亡没有发生该移交，所以候选不覆盖此路径。它不否定已经完成的短时受控取消回收实验，也不证明 CachePilot 保护策略不可行，但 3B 整体仍未通过。

不能简单把 session TTL 回调接成旧 key/count unlock：见 [TTL 所有权边界](TTL_OWNERSHIP_BOUNDARY.md)，旧读锁失效后新读者可能已获得同 key 的锁，迟到释放会误减新锁。需要 reader reservation/epoch、job generation、controller 终结及客户端存活协议；也需区分释放 KV、消费完成结果、删除会话和清除 GPU 注册四类资源。未来补丁必须明确部分释放失败，并处理无响应的 controller；本轮未修改安装的 LMCache 源码。
