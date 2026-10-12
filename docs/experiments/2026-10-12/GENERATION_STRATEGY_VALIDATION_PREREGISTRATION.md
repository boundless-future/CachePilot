# 新组合历史新文本覆盖准入

2026-10-12，启动前冻结。前置为 generation-strategy-formal-20261012a 完整开发矩阵。沿用原四组 default-zero h2.5/d64、selected-zero/one/two h5/d1，预算0/16/32 blocks；保持模型、2GiB GPU KV、16GiB CPU KV、15秒idle grace、同步eager和计时/收尾规则。

输入从历史 fixed-baseline-validation-20261011a-partial.tar.gz 的顶层trace原样提取。规范摘要为 `015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b`；68请求、75,808输入tokens、3,264输出tokens/格。与开发输入逐项核对结构/到达/种子/输出预算、prompt哈希与零交集，实际Qwen tokenizer复核长度。该输入已被历史实验使用，不是全新盲测或真实Agent数据；保留历史无保护覆盖失败，不能重写为通过。

本轮仅独立pilot，顺序default-zero、selected-zero、selected-one、selected-two，每格独立冷启动。全部四格都须满足原配置/源签名、原始SSE计时、请求token、generation/原回执/资源关闭、H2D及非零保护实际提交。任何失败立即停止，原始证据与未运行格保留；没有完整矩阵不给总体均值。不得改reserve、文本、到达、预算或参数追求覆盖。四格通过之后才另行冻结三重复formal。

开发源224项逐字节不变，只新增validation runner与auditor两项，共226项。性能轮关闭逐步/bitwise/物理BlockPool探针；正常退出已知traceback按原精确分类单列。若没有保护提交但请求资源通过，记录为触发范围反例，离线分析容量回退，不继续性能矩阵或误称KV损坏。
