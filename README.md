# Codex → Claude Workflow

让 Codex 担任项目负责人，让 Claude 通过最小工单在独立工作树中实现。将同一套调度、记录和验收方式带到新项目。

```text
需求 / 设计 → Codex 写工单 → 脚本建工作树 + HANDOFF
                                  ↓
                           Claude 局部实现
                                  ↓
                 宿主检查 → 短状态回执 → Codex 验收
                                  ↖ 集中修订
```

- **Codex 管项目**：需求、架构、拆单、Git、CLI、工作树、审查和交付。
- **Claude 做局部实现**：只拿明确目录、分支、允许路径、必要代码行、AC 和返回协议。每单新会话，同单修订复用 UUID。
- **先看状态**：负责人先读短回执，正常按风险展开必要证据，异常再读诊断；不反复读取完整过程。
- **留下记录**：命名持久会话、AI_WORK 可读索引、原始流、检查与受提交哈希绑定的审查证据。

## 本地安装 skill

需要 Python 3.10+、Git，支持 macOS/Linux。安装本身只用标准库，不要求 Claude、API Key 或联网。

```sh
gh repo clone lyclccylcq/codex-claude-workflow
cd codex-claude-workflow
python3 scripts/install_skill.py
```

默认安装到 `~/.codex/skills/codex-claude-workflow`；设置 CODEX_HOME 时跟随该目录。也可用 --dest 指定完整 skill 目录。安装器保留被修改的已有文件，不强制覆盖。

随后在新项目中使用：

> 使用 $codex-claude-workflow，为当前项目建立 Codex 管理、Claude 执行的统一工作流。

skill 自带完整模板，不需再下载其他项目历史；更新仓库后重跑安装器更新本地 skill。

## 直接初始化项目

```sh
# 预览，不写入
python3 scripts/bootstrap.py --project /path/to/project

# 应用；新目录再加 --init-git
python3 scripts/bootstrap.py --project /path/to/project --apply
```

支持 --name、--base-branch、--claude-config-dir、--surface cli|desktop、--no-ci。默认探测项目基线分支；需要不同分支时明确指定。CLAUDE_CONFIG_DIR 是已有个人订阅配置目录，不是 token。不会读取或复制其他项目凭据。

安装合并 AGENTS.md、CLAUDE.md 和 .gitignore 的受管块，保留原文；保留项目自己的检查配置。文件冲突或受管代码被改动时停止，先解决差异。重复应用不会重复插入内容、提交或启动模型。

生成的 scripts/ai_dispatch_config.json 必须接入项目真实测试 argv；内置检查仅验证工作流。默认不授权自动合并，不会继承其他项目的发布或部署权限。

## 开始派工

生成目录中的 docs/ai/automation.md 说明工单、认证、运行、恢复和审查；docs/ai/templates/ 提供 JSON 模板。负责人在授权实现后建立正确已提交基线，先 doctor，再 start。

HANDOFF 默认最多 24 KB，文件上下文按行限定；BLOCKED 返回所需路径/行范围。宿主运行精确可信检查，RESULT/manager-brief 为负责人入口，完整证据留盘。CLI 与桌面登录分开确认；CLI 只接受隔离后的官方个人订阅，不回退第三方网关/API。

可用官方 --desktop --resume 按原 ID 请求打开已停止的 CLI 会话。该能力取决于本机 Claude 版本及平台；真实导入需单独验证。原生实时模式也保留，但不受 CLI 工具白名单或运行限额控制。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

测试包含新项目实际安装、原文及自定义检查保留、幂等、冲突和链接防护、本地 skill 安装，以及在生成项目中执行完整离线调度测试。使用假 Claude 和本地 Git，不发送真实模型请求。测试通过不证明当前账户登录、订阅额度、业务功能或桌面历史导入通过。

仓库只包含可复用的工作流、模板和测试，不包含原应用代码、历史任务、凭据或私人运行日志。
