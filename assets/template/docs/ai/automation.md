# {{PROJECT_NAME}} 工单调度

Codex 管需求、设计、Git、CLI、工作树和验收，Claude 执行局部工单。默认基线分支 `{{BASE_BRANCH}}`，可配置。需要 Python 3.10+、Git、macOS/Linux；真实调用另需支持对应参数的 Claude Code 和个人订阅。

## 安装验证

```sh
python3 scripts/ai_workflow.py validate
python3 -m unittest discover -s scripts/tests -q
git diff --check
```

这些只验证工作流。把真实业务检查加入 allowed_checks，键是命令标签、值为精确 argv；宿主不执行任意 Spec shell 字符串。将本地检查脚本路径列入 check_helpers，确保它们在正确提交基线中。只有明确约定 exit 2 表示环境缺失的包装检查才加入 environment_check_commands。

## 派工

1. 确认已提交基线含工作流与任务依赖，保留其他未提交工作。
2. 建唯一 READY Spec，写目标、允许路径、必要约束、worker_context 文件行范围、AC、检查和回滚。新建文件可用空上下文。focused 用于边界清楚的小任务；数据写入、认证、模型工具和跨层变更用 full。
3. 当前用户已授权实现后执行预检与启动：

```sh
python3 scripts/ai_dispatch.py doctor
python3 scripts/ai_dispatch.py start --task docs/ai/tasks/TASK-NNNN.json --base HEAD
```

远端交付前核对最新基线，使用实际 origin/{{BASE_BRANCH}}。不要无声排除需求依赖的未提交内容。

脚本创建 codex/ 分支与独立工作树。HANDOFF.md 绑定目录、分支、起始 head、代码片段、AC 和返回方式，并归档哈希。默认 24 KB、3 轮，每轮 30 turns、900 秒；超上下文预算先拆单或缩小。Claude 初始提示只引用交接，不附全项目历史或完整 Spec。其他上下文不足时返回 BLOCKED 与路径/行范围。

## 回执与审查

AI_WORK/README.md 链接工作树记录。完整原始流在 .ai-workflow/runs/；两者被 Git 忽略。WORKER_RESULT.md 首行为 Claude 自报状态；RESULT.md 首行为宿主状态，manager-brief.json 是短机器入口。

Codex 先看短回执，正常按风险展开必要 AC 证据与相关差异；异常才读对应诊断。AWAITING_REVIEW 需核对当前任务、head 和证据，full 展开完整 Diff 与必要调用链；宿主通过不自动生成 GPT PASS。

```sh
python3 scripts/ai_dispatch.py status --run <RUN_DIR>
python3 scripts/ai_dispatch.py review --run <RUN_DIR> --review <GPT_REVIEW_JSON>
```

完整状态加 --full。REVISE 集中交回同一 UUID，重跑必要检查生成新证据；head/Spec/基线变化后旧结论失效。READY_TO_PUBLISH 仅表示技术审查通过，不代替用户验收或远端授权。

## 登录、历史和恢复

CLI 使用明确的 claude_config_dir，清除继承凭据/代理，排除 user/project/local 设置，固定官方地址并确认 Pro/Max；safe-mode 单独不能隔离认证。managed policy 出现时先停止核查。doctor 只做预检，不证明剩余额度或真实订阅调用。

复用有效个人登录；缺认证先说明目录和缺口，仅需用户授权时才执行 login，不复制桌面凭据或回退网关/API。CLI 与桌面登录分开核实。

会话命名并启用持久化，同单按原 UUID 续接。CLI 停止后，在 TTY 用 open-history --run <RUN_DIR> 通过官方 claude --desktop --resume <ID> 请求打开同一历史；成功启动不等于 UI 已显示，首次实际核对。平台/版本支持由官方 CLI 决定。运行中不巡检界面，不同时在两端编辑。

显式 start --surface desktop 可准备原生交接；open-desktop 仅预填，负责人在真实 UI 核实目录、账户并发送，完成后以真实宿主 assessment 调用 collect-desktop。原生模式不受 CLI 工具白名单、turns 或 timeout 控制。

限额为 PAUSED_LIMIT，中断为 PAUSED_INTERRUPTED，环境缺失为 PAUSED_ENVIRONMENT。保留现场，处理原因后 resume；环境恢复先只重跑宿主检查。同单修订续接，不重开会话绕额度，不切计费方式。budget 可记人工观察，不能按时间推断满额；聊天结束后不会自动唤醒。

## 交付边界

默认无发布、合并或部署授权。获得当前项目实际授权后才 publish/finalize；发布绑定受审 head/base 的 draft PR，合并要求冻结策略和真实 CI。目标分支由 base_branch 配置。工作流 CI 不替代业务门禁。

离线测试使用假 Claude，不证明当前机器真实登录、额度或桌面历史导入已通过。首次真实联调用无敏感数据的小工单。
