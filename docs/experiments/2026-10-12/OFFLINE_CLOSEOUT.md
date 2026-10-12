# 无GPU复核新增实验

从仓库根目录执行；以下不启动服务、不下载模型。

```bash
python scripts/summarize_generation_matrix.py docs/experiments/2026-10-12/generation-matrix-evidence/generation-decode192-receipt-formal-20261012a --output decode-audit.json
python scripts/audit_real_structure_pilot.py docs/experiments/2026-10-12/real-structure-evidence/real-structure-pilot-20261012a --output structure-audit.json
```

预期分别为12/12与4/4。decode为正式三重复，结构为有额外metrics观测的准入诊断；后者无正式均值聚合。依赖Python、requests/aiohttp，不需要vLLM、LMCache或CUDA。

分别解压对应source tar后用`verify_source_snapshot.py PLAN SOURCE_ROOT --output SOURCE_REPORT`检查每个原文件的字节SHA。额外文件会单列，不能用“原文件通过”替代formal要求的完整签名相等。

从新Git archive运行测试也需要历史`docs/experiments/2026-10-11/unprotected-reference-evidence/unprotected-references-formal-20261011a.tar.gz`，该fixture用于验证decode变换保持原prompt/arrival。不要仅导出tests和scripts后把缺fixture误当运行栈故障。
