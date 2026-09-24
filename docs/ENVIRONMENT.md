# 环境要求与租机清单

更新：2026-09-24。本文区分通用建议和已实测配置。

## 2026-09-24 源码环境更新

已在同一个 `cachepilot` 环境中将 PyPI LMCache 0.5.5 替换为固定研究提交 `1a4b40b1d79b0e76244f127f96ee0982f8bd270f`，针对现有 PyTorch 2.13.0 / CUDA 13.0 重编扩展，未升级 vLLM 或 PyTorch。归档源码的本地包标签为 `0.5.5+g1a4b40b1d`；以完整 commit 和归档校验值作为版本依据。

构建方式见 [build-lmcache.sh](../scripts/build-lmcache.sh)，来源清单见 [lmcache-source.json](../configs/lmcache-source.json)，本轮结果见 [2026-09-24 验收记录](validation/2026-09-24/README.md)。启动与自动验收以 [INTEGRATION.md](INTEGRATION.md) 为当前入口。下方 2026-09-23 的版本状态保留为历史记录，不能当作当前安装版本。

四种模式（原生 vLLM、immediate、FIFO、EVICTION_AWARE）在 torch.compile/CUDA Graph 模式下通过功能验收。三种 LMCache 模式均在 vLLM 重启后实现 GPU hit=0、CPU 回载 1536 tokens，单条 greedy 输出与原生基线一致。EVICTION_AWARE 压力运行完成 14 次 store，最终 ledger 为 admitted=18/emitted=14/pending=4/dropped_evicted=0。停机后 GPU 注册、对象读写锁均清空，但 FIFO/EVICTION_AWARE 分别留下 1/4 个会话记录。

后续 TTL 专项实测：FIFO 遗留的 1 个会话在停止引擎后约 630 秒清零，符合 600 秒 TTL 与 60 秒清理周期；详见 [TTL 结果](experiments/2026-09-24/ttl-result.json)。这不等于 EVICTION_AWARE 和取消/抢占等所有路径均已完成生命周期回归。

后续 24 组合成基线中出现跨配置输出文本差异；前述单条 smoke 一致不能推广为全部输入正确。完整性能数据、输出回归与限制见 [第一轮实验报告](experiments/2026-09-24/REPORT.md)。

## 0. 当前容器候选环境（2026-09-23）

容器硬件与基础软件检查已完成，并创建隔离环境 `cachepilot`：RTX 4090 24GB、Ubuntu 22.04.5、AMD EPYC 7543（16 vCPU）、约 92 GiB 系统内存；NVIDIA Driver 595.80。当前环境元组为 Python 3.12.14、PyTorch 2.13.0+cu130、vLLM 0.30.0、LMCache 0.5.5。

已验证：`pip check`、CUDA 运算、vLLM 独立生成、外部 `LMCacheMPConnector` 实际加载、GPU→LMCache CPU store，以及 vLLM 重启后 GPU miss / CPU→GPU retrieve。Qwen3-4B 同一 prompt 的回载 smoke 命中 1536 tokens，生成请求返回 HTTP 200。该验证以 `--enforce-eager` 运行，只能证明功能链路，不代表生产性能或优化编译路径。

已知限制：LMCache 0.5.5 wheel 的 FIFO lazy-offload 配置在 vLLM 0.30.0 上可能让 EngineCore 在 `mark_req_finished` 崩溃（pending request 已不存在）。不启用 lazy-offload 的基础 connector store/retrieve 已通过；因此目前可用作项目开发基线，但不能把 lazy-offload 策略标成可用。LMCache 公布的兼容表也未明确覆盖本版本组合。完整结果和日志位置见 [environment-record-2026-09-23.json](../configs/environment-record-2026-09-23.json)。

常规 KV 回载 smoke 使用 [lmcache-0.5.5-retrieve.json](../configs/lmcache-0.5.5-retrieve.json)；FIFO 故障复现配置保留在 [lmcache-0.5.5-smoke.json](../configs/lmcache-0.5.5-smoke.json)。

登录后激活：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate cachepilot
```

原 `py312` 环境保持未修改。

## 0.1 在容器中启动 smoke 服务

在两个 SSH 终端分别运行。LMCache 的 IPC/HTTP 服务仅绑定容器回环地址；vLLM API 也仅本机监听。

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate cachepilot
lmcache server --host 127.0.0.1 --port 5555 \
  --http-host 127.0.0.1 --http-port 8080 \
  --l1-size-gb 16 --l1-init-size-gb 16 \
  --eviction-policy LRU --enable-extra-logging
```

第二个终端：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate cachepilot
export CUDA_HOME=/usr/local/cuda-13.0
vllm serve /root/CachePilot/models/Qwen3-4B \
  --host 127.0.0.1 --port 8000 --dtype bfloat16 \
  --max-model-len 8192 --enable-prefix-caching \
  --gpu-memory-utilization 0.88 --max-num-seqs 4 --enforce-eager \
  --kv-transfer-config "$(cat /root/CachePilot/configs/lmcache-0.5.5-retrieve.json)"
```

从本机访问 API 时建立 SSH 隧道：`ssh -L 8000:127.0.0.1:8000 -L 8080:127.0.0.1:8080 <ssh-host-alias>`（替换为本机 SSH 配置中的主机别名）。LMCache 的 5555 端口供容器内 vLLM connector 使用，不需要转发到本机。

## 1. 推荐首台机器

| 项目 | 起步建议 | 说明 |
|---|---|---|
| GPU | RTX 4090 24GB，单卡 | 用户可租选项中最便宜；Qwen3-4B 基线 |
| 系统内存 | 至少 64GB，128GB 更宽裕 | CPU KV 池先 16GB；还需模型加载、进程、数据和编译空间 |
| CPU | 建议 8 个可用 vCPU 起，16 更宽裕 | 不是官方最低值；避免负载客户端/哈希/分词抢占调度器 |
| 存储 | 建议 100GB 可用 SSD，200GB 更宽裕 | 模型、镜像层、构建缓存与实验输出；不下载全套 SWE-bench 镜像 |
| 系统 | x86_64 Linux；优先 Ubuntu 22.04/24.04 | 正式结果在租用 Linux GPU 机器跑；本地 Windows 用于编辑与资料 |
| 权限 | SSH、长进程管理；优先可用 Docker＋NVIDIA Container Toolkit | 不能运行 Docker 时用平台现成匹配环境，不要求嵌套 Docker |
| GPU 链路 | 记录实际 PCIe 代际与宽度 | 不指定必须某代；搬运性能依赖真实链路，不能只看 GPU 名称 |

首阶段使用 8K/16K 上下文、BF16 权重和 KV。Qwen3-4B 权重理论约 7.49GiB，16K 单条 KV 约 2.25GiB，实际还需激活、工作区与图缓存。可用 KV 池由引擎启动 profiling 决定。

后续：5090 32GB＋Qwen3-8B。4090 48GB 和 A800 暂不必需。A800 具体显存规格未确认。

## 2. 软件栈：先选同一套运行时，再锁版本

建议开发 Python 3.12，但以选定镜像/ABI 为准；不要独立升级镜像中的 Python 或 torch。

需要：

- NVIDIA 驱动：必须支持所选容器 CUDA runtime 和 GPU；在镜像选择后检查官方兼容要求，不提前写一个通用最低驱动版本。
- vLLM：官方兼容文档给出 **>=0.20.0 才支持此处需要的外部 MP connector 加载**。这只是接口门槛，不证明任意后续版本支持本项目全部 hooks。
- LMCache：必须包含我们调研的 `LazyOffloadManager`、`OffloadPolicy` 和 EVICTION_AWARE 路径，server/client 使用同一兼容代码版本。
- PyTorch、CUDA runtime、LMCache native extension：匹配 Python 小版本和 ABI。不要在已集成镜像里随意安装另一套 torch。
- 开发工具：git、uv/pip、pytest；若构建 native extension，再准备匹配 CUDA toolkit/nvcc、编译器、cmake/ninja 等，以实际仓库构建说明为准。
- 测量工具：引擎/LMCache 日志和指标、nvidia-smi；定位数据路径时用 PyTorch profiler，必要时 Nsight Systems。第一版无须 Grafana/Kubernetes。

`nvidia-smi` 显示的 CUDA 版本是驱动支持能力，不等于当前 torch 的 CUDA build；两者分开记录。运行预编译容器不一定需要宿主机安装 nvcc。

## 3. 已核实的兼容条件

来源：[官方兼容文档](https://docs.lmcache.ai/getting_started/compatibility.html)，[本地快照](sources/lmcache-compatibility.txt)。

必须显式配置：

```json
"kv_connector_module_path": "lmcache.integration.vllm.lmcache_mp_connector"
```

否则即使 connector 名称相同，vLLM 仍可能选择其自带版本，导致我们改了 LMCache 包却没有被实际执行。

本次调研的 LMCache commit：`1a4b40b1d79b0e76244f127f96ee0982f8bd270f`。它是研究候选，不是经过 GPU 验证的版本锁。待核对 vLLM hooks、对应镜像和 smoke 结果后再冻结安装方案。

官方文档明确：不存在仅靠一个版本对就保证所有设备、模型和存储后端兼容的矩阵。兼容验证分为 runtime/ABI、connector 加载、模型与功能三层。

## 4. 如何安装的决策

优先使用官方集成镜像建立基线，选择具体 tag 后解析并记录 digest，不在最终实验依赖 `latest`。

若集成镜像缺少所需功能：寻找匹配的 nightly/source 组合，再运行验证。若需要源码安装 LMCache，按其说明针对已安装的 torch 构建，避免依赖解析悄悄替换 torch/vLLM。

这次不生成假装已验证的 requirements.lock 或一键安装脚本。环境成功后导出实际 package 列表、源码提交、镜像 digest 与模型 revision。

## 5. 租机前需要确认

- [ ] GPU 型号/显存及是否独占。
- [ ] 主存容量、CPU 资源与空闲磁盘满足上表。
- [ ] 有可用 NVIDIA 驱动、Linux GPU 环境。
- [ ] 可用 Docker GPU 容器，或平台可提供匹配的预装环境。
- [ ] 如用容器，CUDA IPC、共享内存及 pinned memory 限制允许同机多进程运行；具体参数按锁定镜像说明配置。
- [ ] 可以访问模型/仓库下载源，或有可信镜像与可校验缓存。
- [ ] 支持 SSH 转发、后台进程；停止实例后数据是否保留。
- [ ] 记录小时费用和存储费用；本轮尚无价格，无法计算总预算。

推理 API 只需本机或 SSH 隧道访问，不要求公网开放 8000/LMCache 内部端口。不在环境记录中保存 token、密码等凭据。

## 6. 到机器后只读检查

```bash
nvidia-smi
nvidia-smi -q
lscpu
free -h
df -h
python --version
python -m pip show torch vllm lmcache
python -c "import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

未安装依赖时 `pip show`/import 失败属于环境待安装，不能将其当作硬件故障。Docker 场景需分别记录宿主机硬件和容器内运行时。

## 7. 环境通过标准

能生成文本 → 确认外部 Connector 模块 → 冷请求保存 → GPU 热命中 → GPU 不再持有时 CPU 回载 → 输出回归 → 延迟卸载策略日志与正确的引用释放。

这一阶段完成后填写 [环境记录模板](../configs/environment-record.example.json)，保留日志，才开始性能实验。
