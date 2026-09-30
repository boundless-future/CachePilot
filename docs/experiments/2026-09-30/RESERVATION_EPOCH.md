# Reservation/epoch：原生锁与 L1 接口契约

本轮实现了独立 C++ 读引用锁，并用真实 LMCache L1Manager 的获取、写转读、删除方法验证接口。原型可以阻止过期旧引用、重复释放及同名对象重建后的旧释放影响新 reader。**未替换已安装 LMCache，未接入实际 prefetch controller/RPC，3B 未完成。** 本轮没有 GPU 或性能实验。

## 为什么需要更改锁接口

固定版 TTLLock 只有共享计数和过期时间；每次新加锁都会刷新共享 TTL。`finish_read(key, count)` 没有 reader 身份，无法识别旧请求。仅在回收层保存 job ID 或“获取时间 + TTL”不够：新 reader 会延长已有锁的期限，而且 Python 层的检查与 native 解锁不是一次原子操作。

`native/reservation_lock.cpp` 用 mutex 将获取、过期检测、epoch 更新、token 验证和释放串行化，pybind 调用释放 GIL，使多线程测试实际进入 C++ 竞争。token 字段为：

| 字段 | 意义 |
|---|---|
| lock_id | 本进程内单调分配，不复用；新对象获得新的锁身份 |
| epoch | 共享 TTL 到期或显式 reset 时递增，清除该代 live token |
| serial | 同一锁内单调递增；每个 reader slot 一个 token，不因释放重用 |

`acquire(count)` 返回 1–128 个独立 token，并刷新共享 TTL。`release(token)` 在同一个临界区返回 `RELEASED / STALE_EPOCH / INACTIVE / FOREIGN_LOCK`，不会盲目减少其他 token。只保存当前 live token，不保存永久完成 tombstone；无引用后重用同一锁也依靠新 serial 区分。计数/身份达到上限则拒绝继续分配，避免回绕复用。

这是**进程内身份**，不能原样当作重启后仍唯一的 wire ID；跨进程协议仍需 server/client incarnation。token也不是安全认证凭证。原型的获取操作复制live集合来保持更新原子性，未优化大规模引用下的成本，不作为高性能锁的结论。

## 真实 L1 方法的 CPU 契约

`scripts/reservation_l1_contract.py` 提供仅供 CPU 验证的 harness：分配器、事件投递用 mock；运行固定版真实 `reserve_write`、`reserve_read`、`finish_write_and_reserve_read`、`delete` 方法，在获取时捕获新原生锁 token。解锁路径是本项目新实现，**没有调用原来的匿名 `finish_read`**。

- `reserve_read_owned` / `finish_write_and_reserve_read_owned` 返回原生逐key结果及每个reader的token。通知失败发生在锁已获取之后时，仍保留token和错误。
- `finish_read_owned` 返回逐token结果；成功后发生事件或allocator错误时，保留已完成的释放，不能因后续异常再次解锁。
- 临时对象在最后一个有效引用释放后，交由真实 `delete` 删除。旧epoch或foreign token本身不触发删除；回调若重建同key对象，旧释放也不能删除新对象。
- 拒绝匿名读/释放和未实现的 `unsafe_read`，避免悄悄混用旧协议。它不能直接替换生产 L1Manager。

allocator失败的测试只证明结果不会误报或重复free，不证明资源已恢复；实际释放失败仍需要持久/可见的未解决记录。共享TTL和reset仍不证明底层I/O停止；过期后是否可以删除临时对象、write reservation身份、DMA内存生命期不在本原型已验证范围内。

## 验证与复现

11 项 C++ 测试涵盖：源文件/二进制SHA256、真实TTL过期（无需先poll）、共享TTL刷新、重复释放、多reader、reset/新锁身份、匿名接口拒绝、8线程重复释放只有一个成功、8线程共512个token、4,000步固定种子操作与参考状态核对。没有使用假时钟。

12 项 L1 契约测试涵盖：写转读、临时对象最后一个reader删除、真实reserve_read的部分命中、TTL后新读者、同key删除重建、获取/释放通知失败、allocator失败、混合逐项结果、非法输入、write-locked拒绝、批次预校验和回调期间对象重建。

原有 `test_ttl_lock_ownership.py` 继续复现安装版 TTLLock 的缺口；未删掉这个反例来获得全绿。完整服务器结果为 **206 passed、26 subtests passed**，117条上游弃用警告；本地 **122 passed、84 skipped、26 subtests passed**。新增23项均在服务器真实编译扩展下执行。本地缺少Linux扩展/推理栈，跳过不算通过。

证据：[构建记录](reservation-native-contract/build.json)、[完整测试输出](reservation-native-contract/full-pytest.txt)、[文件校验](reservation-native-contract/evidence.json)。构建仅用现有g++与torch附带的pybind11头文件，不重新编译/安装LMCache。

```bash
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:$PATH
python scripts/build_reservation_native.py
CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1 python -m pytest -q
```

扩展输出在被Git忽略的 `artifacts/reservation-native`。上述环境变量使扩展缺失时报错，避免服务器因跳过测试而误报通过。

## 下一步：贯穿 controller 的身份移交

源码核对得到的接入点如下（固定版文件路径相对于 `lmcache/v1/distributed`）。不能在完成bitmap出来后按key补造token，那时可能已是新对象/新reader。

| 接入点 | 要传递的所有权 |
|---|---|
| `storage_manager.py:452`，初始L1命中 | 获取时保存每个key/reader token，与handle关联 |
| `storage_controllers/prefetch_controller.py:854`，后续L1命中 | token随job保存；前缀/滑窗裁剪释放准确子集 |
| 同文件`:1349`，L2完成后写转读 | 将原始writer终结结果与新read token绑定；writer身份仍需独立设计 |
| 同文件`:1397` / `:1405`，WARM或未保留对象释放 | 按实际持有token释放；失败不混入retained完成集合 |
| controller完成结果 / LookupModule消费 | 返回带身份的retained集合；QUERY移交与abandon回收只能有一个所有者 |
| StorageManager数据访问及RETRIEVE | 每个worker消费自己的slot；读取与释放都校验token，禁止回退匿名接口 |

下一轮先做controller结果容器与唯一移交的CPU真实接口回归，再设计失联lease触发abandon。无END清理、server关闭和永久不终结I/O仍是开放门槛，3C GPU策略仍不启用。

23:53检查：8000/8080/5555均无监听，GPU无计算进程、显存1MiB、利用率0%；服务器保持开机。
