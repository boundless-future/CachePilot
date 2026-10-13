# 最小接线示例

这是当前受控单卡研究栈的启动示例，不是性能验收或生产部署。
固定环境和上游源码要求见 `docs/ENVIRONMENT.md`；原有Connector中的源码校验仍然生效。

从仓库根目录，在已配置的Linux Conda环境中安装自己的包（不更新上游）：

```bash
python -m pip install --no-deps --no-build-isolation -e .
python scripts/build_reservation_native.py
cachepilot doctor
```

`doctor`只列依赖和native模块位置，不证明源码匹配或GPU可用。
wheel不包含native二进制；非editable安装时应设置
`CACHEPILOT_NATIVE_DIR=/absolute/path/to/reservation-native`，复用与当前Python/CUDA匹配的编译产物。

终端一：启动带所有权保护与generation观察的LMCache服务。

```bash
cachepilot server --events runs/manual/owned-events -- \
  --host 127.0.0.1 --port 5555 --http-host 127.0.0.1 --http-port 8080 \
  --l1-size-gb 16 --l1-init-size-gb 16 --eviction-policy LRU
```

终端二：生成统一配置后接入vLLM。默认零保护；更改为1/2仅切换现有实验预算，不代表相应工作负载已验收。

```bash
mkdir -p runs/manual
cachepilot config --max-chunks 0 > runs/manual/kv-transfer.json
export CACHEPILOT_PROTECTION_SUMMARY_PATH="$PWD/runs/manual/protection-summary"
vllm serve /root/CachePilot/models/Qwen3-4B \
  --host 127.0.0.1 --port 8000 --dtype bfloat16 --max-model-len 8192 \
  --enable-prefix-caching --generation-config vllm --max-num-seqs 4 \
  --shutdown-timeout 10 --gpu-memory-utilization 0.80 \
  --kv-cache-memory-bytes 2147483648 --enforce-eager --no-async-scheduling \
  --kv-transfer-config "$(cat runs/manual/kv-transfer.json)"
```

应用通过vLLM的OpenAI兼容HTTP接口发请求；CachePilot作为Connector在scheduler/worker里执行，
不额外部署一个HTTP代理。LMCache MP独立进程承担CPU缓存与搬运。
停机和资源核对仍按现有实验harness执行；此示例没有替代完整资源审计。
