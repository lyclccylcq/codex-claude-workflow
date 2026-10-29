# {{PROJECT_NAME}} 双模型工单流程

Codex 与用户讨论需求和设计，脚本创建工作树及最小工单，Claude 局部实现，宿主执行检查，Codex 判断与验收。每单新会话，同单修订续接，保留历史与 AI_WORK 记录；先看状态，不重复读过程。

- [调度、检查、恢复与交付](automation.md)
- [任务模板](templates/task.spec.json)
- [负责人审查](prompts/review.md)

项目真实业务检查在 scripts/ai_dispatch_config.json 接入；内置自检不能代替业务验收。
