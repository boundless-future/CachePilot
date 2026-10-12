# 真实结构扩展：CPU目录审计，尚未启动GPU

固定 `callanjfox/kv-cache-tester` commit `94f6046dfd9c0c8bc0cdb24a5aa6579bac8669bf`，原初版只扫描编号1–20，只有一条完整session符合小卡约束。此次扫描独立编号21–100，不按任何性能结果筛选。80个文件下载成功；完整SHA及排除原因见 `REAL_STRUCTURE_CATALOGUE.json`，候选原文件见 `real-structure-source/`。

保持原结构准入：无嵌套subagent、源block=64、完整session含3–32请求、输入tokens/8后加48输出不超过8192、每请求有hash blocks。全部符合的六条session共60请求：

| 编号 | 请求数 | 原最大输入tokens | /8最大输入 | 原session时长秒 |
|---|---:|---:|---:|---:|
| 21 | 14 | 35,645 | 4,455 | 512 |
| 22 | 7 | 43,710 | 5,463 | 400 |
| 26 | 3 | 20,068 | 2,508 | 146 |
| 57 | 8 | 15,225 | 1,903 | 109 |
| 58 | 4 | 56,921 | 7,115 | 45 |
| 86 | 24 | 37,356 | 4,669 | 861 |

下一实验拟使用全部六条、源hash到token的固定匿名重建、tokens/8、时间/10、固定48输出；每session第一请求对齐0，内部到达和think-time按同比例缩放。跨原session的真实时间关系未知，不能称为原始生产并发分布；匿名hash重建不等于原文本、真实tool执行或输出反馈。不会追加人工压力、padding、复制session或按结果选择子集。源64-token block变成8token，而LMCache chunk为256token，复用需要实际完整前缀匹配，不能把源hash数量直接当可命中的cache chunk数量。

14:39 CPU生成与独立审计已完成：实际Qwen3-4B tokenizer的16个单token词元、tokenizer文件SHA、builder与trace SHA归档在 `real-structure-built/manifest.json`。60请求共209,661输入tokens、2,880输出预算，末次计划到达86.1秒。3,262个源block身份均保持跨轮一致且无重建碰撞；54次相邻轮前缀审计通过，任意跨session prompt的公共前缀最大为0。完整源SHA、prompt hash、长度、顺序、到达、参数和256-token完整chunk机会见 `real-structure-built/audit.json`。生成器重建等于归档、源字节篡改拒绝、更新时间摘要后的到达篡改拒绝三项测试通过。尚未注册GPU试点或测到物理压力；CPU前缀机会不等于实际缓存命中。

回放采用open arrival，后继轮不等待前轮生成结束，实际执行顺序可能因排队而不同；只重放匿名请求结构，不构成因果执行的live Agent。每会话首到达人工对齐0，时间/10和输入/8均为受小卡约束的显式变换。后续只有观察到容量、抢占或恢复行为才能报告“压力”。decode正式重复优先，不平行启动GPU实验。

复查入口：`python scripts/catalogue_real_traces.py --source-dir DOWNLOADED_FIRST21_TO100 --output catalogue.json`。该脚本只读取CPU源文件，不启动推理。下载固定commit仍须与归档manifest SHA逐项比对。
