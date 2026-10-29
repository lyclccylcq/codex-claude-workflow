"""Readable local mirrors of dispatcher evidence; never a source of review authority."""
import json
import os
from pathlib import Path
import re
import shlex
from datetime import datetime, timezone


def stamp():
    return datetime.now(timezone.utc).isoformat()


def safe_file(directory, name):
    """Refuse links in host-written trace paths, including a replaced AI_WORK folder."""
    directory = Path(directory)
    base = directory
    missing = []
    while not base.exists() and not base.is_symlink():
        missing.append(base)
        base = base.parent
    if base.is_symlink() or not base.is_dir():
        raise OSError("trace directory must be a real directory")
    # Check every existing component, not only the last parent.
    for item in (directory, *directory.parents):
        if item.is_symlink():
            raise OSError("trace path cannot contain a symlink")
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    target = directory / name
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise OSError("trace target must be a regular file")
    return target


def put(directory, name, value):
    path = safe_file(directory, name)
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    # O_NOFOLLOW also protects the last component between inspection and open.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(value)


def worker_dir(state):
    return Path(state["workspace"]) / "AI_WORK" / Path(state["run_dir"]).name


def brief(state):
    """Status first; long reports and logs remain behind explicit artifact paths."""
    fields = ("status", "task_id", "round", "run_dir", "summary", "reason", "resume_error", "history_open_error",
              "workspace", "session_id", "session_name", "trace_dir", "handoff_path", "handoff_bytes", "review_mode",
              "packet_dir", "report_path", "review_path", "desktop_history_open_requested",
              "desktop_prompt_path", "desktop_session_title", "limits")
    result = {key: state[key] for key in fields if key in state}
    for key in ("summary", "reason", "resume_error", "history_open_error"):
        if key in result:
            value = str(result[key]).replace("\n", " ")
            result[key] = value[:400] + ("… (see saved record)" if len(value) > 400 else "")
    if state.get("check_round") == state.get("round"):
        result["checks"] = state.get("check_summary", {})
    result["risk_count"] = state.get("worker_risk_count")
    result["next_action"] = {"AWAITING_REVIEW": "controller_review", "READY_TO_PUBLISH": "deliver_reviewed_result",
        "RUNNING": "wait_for_worker", "CHECKING": "wait_for_host_checks", "AWAITING_DESKTOP": "desktop_handoff"}.get(
            state["status"], "inspect_checkpoint" if state["status"].startswith("PAUSED_") else "inspect_status")
    result["state_path"] = str(Path(state["run_dir"]) / "state.json")
    if state.get("trace_enabled"):
        result["result_path"] = str(Path(state["run_dir"]) / "RESULT.md")
    return result


def destinations(state):
    yield Path(state["run_dir"])
    if (Path(state["workspace"]) / ".git").exists():
        yield worker_dir(state)


def event(state, kind, message):
    if not state.get("trace_enabled"):
        return
    row = {"at": stamp(), "round": state["round"], "kind": kind, "message": message}
    for directory in destinations(state):
        for name, line in (("events.jsonl", json.dumps(row, ensure_ascii=False)),
                           ("activity.log", f'{row["at"]} [round {row["round"]}] {kind}: {message}')):
            target = safe_file(directory, name)
            fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a") as output:
                output.write(line + "\n")


def stream_event(state, raw):
    """Expose tool metadata only, never code arguments, tool output or hidden reasoning."""
    if not state.get("trace_enabled"):
        return
    try:
        item = json.loads(raw)
    except (ValueError, UnicodeError):
        return
    if not isinstance(item, dict):
        return
    kind = item.get("type")
    if kind == "system" and item.get("subtype") == "init":
        event(state, "claude", "Session initialized")
    if kind in ("assistant", "user"):
        message = item.get("message")
        blocks = message.get("content", []) if isinstance(message, dict) else []
        if not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name")
                if name not in ("Read", "Glob", "Grep", "Edit", "Write"):
                    name = "other tool"
                inputs = block.get("input")
                path = inputs.get("file_path", inputs.get("path")) if isinstance(inputs, dict) else None
                detail = ""
                if isinstance(path, str):
                    candidate = Path(path)
                    if not candidate.is_absolute():
                        candidate = Path(state["workspace"]) / candidate
                    try:
                        relative = str(candidate.resolve().relative_to(Path(state["workspace"]).resolve()))
                        detail = " " + relative[:180].replace("\n", " ").replace("\r", " ")
                    except (ValueError, OSError):
                        detail = " (outside workspace)"
                event(state, "tool", name + detail)
            elif block.get("type") == "tool_result":
                event(state, "tool_result", "error" if block.get("is_error") else "returned")
    elif kind == "result":
        event(state, "claude_result", "error; host verification pending" if item.get("is_error") else "returned; host verification pending")
    elif kind in ("rate_limit_event", "rate_limit_error"):
        event(state, "limit", "Claude emitted a limit event; dispatcher will classify it")


def sync(state):
    if not state.get("trace_enabled"):
        return
    run = Path(state["run_dir"])
    ident = run.name
    if not re.fullmatch(r"TASK-\d+-[0-9a-f]{10}", ident):
        raise OSError("invalid trace run identity")
    snapshot = {key: state.get(key) for key in (
        "task_id", "task_path", "status", "round", "head_sha", "base_sha", "workspace", "session_id",
        "session_name", "summary", "reason", "resume_error", "packet_dir", "report_path", "review_path",
        "desktop_history_open_requested", "history_open_error")}
    prior = safe_file(run, "progress.json")
    previous = json.loads(prior.read_text()) if prior.exists() else None
    if previous != snapshot:
        event(state, "host_state", state["status"] + (": " + str(state["reason"]) if state.get("reason") else ""))
    put(run, "progress.json", snapshot)
    receipt = brief(state)
    header = ('STATUS: ' + state["status"] + '\n'
              + 'SOURCE: host dispatcher; Claude completion alone is not a review verdict\n'
              + 'NEXT: ' + receipt["next_action"] + '\n')
    if receipt.get("summary"):
        header += 'SUMMARY: ' + receipt["summary"] + '\n'
    if receipt.get("checks") is not None:
        header += 'HOST_CHECKS: ' + json.dumps(receipt["checks"]) + '\n'
    header += '\nDetails: ' + str(run / 'state.json') + '\n'
    for directory in destinations(state):
        put(directory, "RESULT.md", header)
        put(directory, "manager-brief.json", receipt)
    controller = Path(state["controller"]) / "AI_WORK"
    pointer = controller / ident
    trace = worker_dir(state)
    put(pointer, "README.md", f'# {state["task_id"]}\n\n'
        f'工作记录：[打开工作树记录]({trace / "README.md"})\n\n'
        f'当前宿主状态：`{state["status"]}`，轮次：{state["round"]}。\n\n'
        f'权威状态：[state.json]({run / "state.json"})。此目录是可读索引，不能代替检查和 GPT 审查。\n')
    entries = sorted(p.name for p in controller.iterdir() if p.is_dir() and not p.is_symlink())
    put(controller, "README.md", "# " + state.get("project_name", "Project") + " AI 工作记录\n\n" + "\n".join(
        f'- [{name}]({name}/README.md)' for name in entries) + "\n")
    if not (Path(state["workspace"]) / ".git").exists():
        return
    put(trace, "progress.json", snapshot)
    if not safe_file(trace, "task.json").exists():
        put(trace, "task.json", (run / "spec.json").read_text())
    command = "python3 scripts/ai_dispatch.py open-history --run " + shlex.quote(str(run))
    if state.get("claude_surface") == "desktop":
        session_text = (f'原生桌面会话标题：{state.get("desktop_session_title", "")}\n\n'
                        "在 Claude Desktop Code 中查看并继续同一会话；不生成 CLI UUID，也不调用 CLI 历史导入。\n")
    else:
        session_text = (f'名称：{state.get("session_name", "")}\n\n'
            f'会话 ID：`{state.get("session_id")}`\n\n'
            f'账户配置目录：`{state["claude_config_dir"]}`\n\n'
            "CLI 启用会话持久化并按原始 ID 续接；运行结束后可执行以下命令打开同一桌面历史：\n\n"
            f'```sh\n{command}\n```\n\n'
            "只记录请求打开，不据此声称桌面已显示。打开后不要与 CLI 同时继续编辑。\n")
    put(trace, "SESSION.md", "# Claude 会话\n\n" + session_text)
    links = ['- [先读结果状态](RESULT.md)', '- [负责人简报](manager-brief.json)',
             f'- [冻结任务](task.json)', '- [操作摘要](activity.log)', '- [机器可读进度](progress.json)',
             '- [原始会话入口](SESSION.md)', f'- [完整宿主状态]({run / "state.json"})']
    for key, label in (("report_path", "检查报告"), ("packet_dir", "审查证据包"), ("review_path", "GPT 审查")):
        if state.get(key):
            links.append(f'- [{label}]({state[key]})')
    for folder in sorted(run.glob("round-*")):
        for name in ("HANDOFF.md", "WORKER_RESULT.md", "prompt.md", "claude.json", "claude.stderr", "completed-answer.json"):
            if (folder / name).is_file():
                links.append(f'- [{folder.name}/{name}]({folder / name})')
    put(trace, "README.md", f'# {state["task_id"]} 工作记录\n\n'
        f'宿主状态：**{state["status"]}**，轮次：{state["round"]}。\n\n'
        "Claude 返回只代表实现方交接；检查结果和 GPT 结论分别保留。当前目录由宿主生成，是证据的便捷镜像。\n\n"
        + (str(state["summary"]) + "\n\n" if state.get("summary") else "")
        + ("当前阻塞：" + str(state["reason"]) + "\n\n" if state.get("reason") else "")
        + "\n".join(links) + "\n")
