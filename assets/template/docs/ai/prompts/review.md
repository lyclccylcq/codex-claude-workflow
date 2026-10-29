# Codex 负责人审查

先读 RESULT.md 或 manager-brief.json。正常按风险展开必要验收材料，失败、缺上下文或异常才看对应诊断，不回读全项目或完整 Claude 会话。

核对 manifest 的任务、Spec 哈希、base/head 和证据。focused 结合范围检查、真实检查汇总、AC 和相关差异；full 展开完整 Diff 与必要调用链。数据写入、认证、模型工具和跨层改动使用 full。模板自检不能代替业务验收，不无理由重跑已可信通过的同一检查。

按 templates/gpt-review.json 写真实结论并覆盖全部 AC：PASS 要证据充分；REVISE 集中列齐具体问题；BLOCKED 说明关键缺口。自报完成和宿主通过不自动等于 GPT PASS；head/Spec/证据变化后旧结论失效。
