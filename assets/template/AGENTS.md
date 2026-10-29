## {{PROJECT_NAME}}：Codex 负责人，Claude 执行

- Codex 负责需求、设计、拆单、Git、CLI、工作树、检查、审查与交付。Claude 负责普通实现和集中修订；仅调度基础设施自身开发或用户明确指定时由 Codex 实现。
- 使用 docs/ai/templates/task.spec.json 写 READY 工单：必要项目约束、允许路径、worker_context 文件行范围、AC、可信检查、风险和回滚。仅讨论保持 DRAFT；READY 需真实执行授权。
- 每单独立工作树和持久会话，同单修订复用 UUID。Claude 只读 HANDOFF.md 与必要局部范围，不默认读全局说明；缺上下文返回 BLOCKED 与路径/行范围。
- 先读 RESULT.md / manager-brief.json。正常按风险查看必要证据，异常再展开诊断；不持续读过程或巡检界面。Claude 自报完成和宿主通过不自动产生 GPT PASS。
- CLI 使用隔离的官方个人订阅认证，无网关/API 回退。复用有效登录，缺认证先说明具体缺口，不反复授权。原生桌面为显式选项，其登录、权限和运行限制另行核实。
- 保留 Claude 历史、AI_WORK 可读记录和私有原始证据，不提交日志或凭据。限额、中断、环境阻塞保存现场，不空转重试。
- 工作流须在正确提交基线中；保留其他改动。精确 argv 配置由负责人接入真实业务检查，内置工作流自检不等于产品验收。
- 远端操作和部署依照当前项目实际授权，默认无自动合并授权。操作见 docs/ai/automation.md。
