# 新组合长前缀 GPU 拆分专项

2026-10-12。`run_generation_protection_smoke.py --long-prefix` 在固定1088-token来源、two预算、原bounded负载上运行独立诊断；Qwen3-4B/BF16、4090 24GB、同步eager TP1、2GiB KV。plan在启动前记录216项源码SHA，未修改reserve或策略。不是性能实验。

348请求通过，348代generation注册→scheduler END→server END精确对应，23个STORE批次；实际9次保护拆分，峰值32个blocks，最终pin0、BlockPool恢复909，54个原reservation和36次native transfer逐身份终结。总计1800层回载KV bitwise全等。

独立审计确认8条 `[0,512)`保护STORE→原回执→`[512,1024)`默认suffix→原回执的链；8条均具有四个256-token chunk完整的STORE/native terminal/H2D来源和每链144层相等。总计9张派生suffix票据，parent/child同generation且身份不同，父票据terminal，子票据terminal或dropped；完整回载链对应子票据必须terminal。9次拆分不等于9条全部成功回载链，报告保留实际覆盖数。

原runner及本地联合normal/split/lineage独立复算通过。新增归档证据篡改测试覆盖缺lineage、自指parent及未终结child，与既有split/normal审计合计15 passed。服务器源码在运行结束后逐项核对并tar归档；22原始文件5,044,642bytes与服务器manifest一致。证据位于 `generation-protection-long-prefix/`。

运行后GPU 0%/1MiB，日志未检出ERROR/Traceback，服务已停止。没有注入失败或取消抢占，不替代其他专项。后续继续独立失败回执与共享取消/抢占；性能仍需新签名pilot/formal。
