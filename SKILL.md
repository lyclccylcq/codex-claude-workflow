---
name: codex-claude-workflow
description: 为新项目建立或升级 Codex 负责人、Claude 局部工单执行流程，生成工作树调度、最小交接、状态回执和审查证据。用于初始化或迁移开发流程；已有流程中的普通业务修改不需要重新安装。
---

# Codex 负责人 · Claude 执行

Codex 掌握需求、设计、Git、CLI、工作树、审查和验收；Claude 只执行局部工单。会话与项目记录落盘，负责人先读短状态，再按风险展开证据。

## 安装或升级

1. 确认目标 Git 根目录，保留现有项目约束、技术栈和未提交工作。只读安装所需的项目说明和测试配置。
2. 用本技能的脚本预览：`python3 <skill目录>/scripts/bootstrap.py --project <项目绝对路径>`。按实际项目使用 `--name`、`--base-branch`、`--claude-config-dir`、`--surface desktop`；新目录加 `--init-git`。
3. 预览无冲突后加 `--apply`。用户已授权的常规本地安装无需额外笼统确认。脚本保留 AGENTS/CLAUDE/.gitignore 原文和项目检查配置。冲突时比较具体差异，保留项目修改，不强制覆盖。
4. 在 scripts/ai_dispatch_config.json 接入项目真实检查的精确 argv，将本地检查脚本路径列入 check_helpers 以冻结可信基线。内置检查只验证工作流，不证明业务通过。明确约定 exit 2 表示环境缺失的包装检查才加入 environment_check_commands；不把 pytest 的 exit 2 当环境成功。
5. 运行 `python3 scripts/ai_workflow.py validate`、`python3 -m unittest discover -s scripts/tests -q`、`git diff --check`。这些离线检查不调用模型。仅要求安装时不启动 Claude，也不据此创建远端仓库、推送、PR、合并或部署；按当前任务实际授权执行这些操作。

## 派工与返回

- 读取生成的 docs/ai/automation.md 中与当前操作有关的部分。开始实现前按已有授权建立正确提交基线，不打包其他未提交工作。
- Codex 写 READY 工单：目标、允许修改、必要项目约束、worker_context 文件行范围、AC、可信检查、回滚与审查深度。新建文件可用空上下文。把全局规则提炼进工单，不让执行者再读整套项目。
- 只读 doctor 复用有效个人订阅认证；桌面与 CLI 登录分开核实。缺登录时说明具体目录和缺口，不反复打开登录页、不复制凭据、不回退网关/API。真实调用须有当前用户执行该工单的授权。
- start 创建独立工作树、持久命名会话和 HANDOFF.md。每单新会话，同单修订续接原 UUID。Claude 只读交接和必要局部范围；缺上下文返回 BLOCKED 与路径/行范围。
- Codex 先读 RESULT.md 或 manager-brief.json；正常只展开必要 AC 证据与相关差异，风险或异常再看诊断。Claude 自报完成和宿主检查通过不自动等于 GPT PASS。反馈集中返回，限额或环境问题保存检查点，不空转重试。
- 保留 AI_WORK 可读记录、私有原始输出及 Claude 历史。CLI 停止后可用 open-history 请求打开同一桌面会话；成功启动不证明历史已显示，首次实际核对。运行期间不轮询界面。

模板和脚本随 skill 保存，初始化新项目无需下载其他项目历史。更新时使用工作流仓库中的安装脚本，保留本地修改。
