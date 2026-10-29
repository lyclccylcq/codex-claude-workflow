---
name: implement-task
description: 执行 Codex 给出的局部 HANDOFF 工单，并返回状态在首项的简短结果。
disable-model-invocation: true
---

只读明确 HANDOFF.md 和指定局部范围，确认目录、分支、允许路径、AC 与返回方式。缺上下文返回 BLOCKED 与路径/行范围，不自行扫描全项目。

只使用明确允许的文件工具。Git、CLI、工作树、检查和项目监管由 Codex 管理。返回 JSON，首项 status=COMPLETED|BLOCKED，其后 summary、acceptance（id/status/evidence）和 risks；不声称尚未运行的检查通过。同单修订续接。
