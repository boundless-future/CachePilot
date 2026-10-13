# 匿名结构零覆盖：独立只读窗口诊断

2026-10-13约13:00。原pilot双chunk0提交/7capacity回退，60请求/62代/资源通过，3/4矩阵失败；formal禁止启动。完整原包SHA `8869581a276bc390c4bc18e265de6e58927d651d7485f207bdd29457150fbdda`，三处复算一致。

单独运行one、two各一格，保持原60请求匿名结构、arrival、tokens、h5/d1、2GiB/16GiB与安全reserve。新增SharedStructureWindowConnector复用现有只读WindowProtection，直接继承普通SharedPrefixConnector，不混入ProbeMixin/同步KV或分配快照。原242项源不改，仅添加connector/runner/auditor三项。

在prepare前记录free/reserve/budget/used、pending是否live/finished/inflight、每块ref/rank/hash一致性。按原检查顺序统计request-ineligible、minimum-capacity、pressure-rank、metadata/layout、inflight-budget、selected-budget-capacity、block/hash、visible-checks-satisfied。统计是重复候选观测，不是独立请求；末项也不保证token-prefix-proof和同一步后续状态通过。

两格是独立诊断，不作为原pilot补跑，不计性能，不据是否提交而通过/失败；必须原请求、资源、原身份回执、generation/server END全闭合。零覆盖原result仍false，另标diagnostic_passed，不能覆盖原失败。若非覆盖类错误，停止下一格。原始窗口和failed marker归档。输入与source重建检查保持。

观测本身有开销，不能用此两格因果解释原无探针pilot。14:25后不启动新格；14:30收尾。诊断之后先报告机制限制，不能据此删reserve或改输入凑收益。
