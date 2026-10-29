#!/usr/bin/env python3
"""Install the bundled workflow into a project without replacing local work."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets/template"
MANIFEST = ".ai-workflow-template.json"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def safe_target(root, relative):
    target = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("unsafe output path: " + relative)
    for item in (target, *target.parents):
        if item.is_symlink():
            raise ValueError("output path contains a symlink: " + relative)
        if item == root:
            break
    if target.exists() and not target.is_file():
        raise ValueError("output path is not a regular file: " + relative)
    return target


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".workflow-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def git(project, *args):
    result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def merge_defaults(existing, defaults):
    result = dict(existing)
    for key, value in defaults.items():
        if key not in result:
            result[key] = value
        elif isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = merge_defaults(result[key], value)
    return result


def block(old, body, name, prior_hash):
    if name == ".gitignore":
        start, end = "# >>> codex-claude-workflow", "# <<< codex-claude-workflow"
    else:
        start, end = "<!-- codex-claude-workflow:start -->", "<!-- codex-claude-workflow:end -->"
    replacement = start + "\n" + body.rstrip() + "\n" + end
    if start in old or end in old:
        if old.count(start) != 1 or old.count(end) != 1 or old.index(start) >= old.index(end):
            raise ValueError("ambiguous workflow block: " + name)
        first, last = old.index(start), old.index(end) + len(end)
        current = old[first:last]
        if current != replacement and digest(current.encode()) != prior_hash:
            raise ValueError("locally edited workflow block: " + name)
        return (old[:first] + replacement + old[last:]).encode(), digest(replacement.encode())
    return (old + ("\n\n" if old and not old.endswith("\n\n") else "") + replacement + "\n").encode(), digest(replacement.encode())


def install(args):
    project = Path(args.project).expanduser().resolve()
    code, top = git(project, "rev-parse", "--show-toplevel") if project.exists() else (1, "")
    has_git = code == 0
    if has_git and Path(top).resolve() != project:
        raise ValueError("--project must name the Git repository root")
    if not has_git and not args.init_git:
        raise ValueError("project needs Git; use --init-git when creating a new repository")
    config_path = safe_target(project, "scripts/ai_dispatch_config.json")
    existing_config = json.loads(config_path.read_bytes()) if config_path.exists() else {}
    if not isinstance(existing_config, dict):
        raise ValueError("existing dispatcher configuration must be an object")
    name = args.name or existing_config.get("project_name") or project.name
    if not isinstance(name, str) or not name.strip() or re.search(r"[\x00-\x1f]", name):
        raise ValueError("project name must be a single nonempty line")
    branch = args.base_branch or existing_config.get("base_branch")
    if not branch and has_git:
        code, remote = git(project, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        if code == 0:
            branch = remote.removeprefix("origin/")
        else:
            _, branch = git(project, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = branch or "main"
    if not isinstance(branch, str):
        raise ValueError("base branch must be a string")
    if subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True).returncode:
        raise ValueError("invalid base branch")
    manifest_path = safe_target(project, MANIFEST)
    prior = json.loads(manifest_path.read_bytes()) if manifest_path.exists() else {}
    defaults = json.loads((ASSETS / "scripts/ai_dispatch_config.json").read_bytes())
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "project"
    defaults.update(project_name=name, base_branch=branch, worktree_root="~/.claude-worktrees/" + slug)
    if args.claude_config_dir:
        defaults["claude_config_dir"] = args.claude_config_dir
    if args.surface:
        defaults["claude_surface"] = args.surface
    plans, hashes, blocks, conflicts = {}, {}, {}, []
    for source in sorted(ASSETS.rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        relative = source.relative_to(ASSETS).as_posix()
        if args.no_ci and relative.startswith(".github/"):
            continue
        target = safe_target(project, relative)
        raw = source.read_bytes()
        if relative.endswith(".md") or relative == ".gitignore":
            raw = raw.decode().replace("{{PROJECT_NAME}}", name).replace("{{BASE_BRANCH}}", branch).encode()
        old = target.read_bytes() if target.exists() else None
        try:
            if relative in ("AGENTS.md", "CLAUDE.md", ".gitignore"):
                raw, blocks[relative] = block((old or b"").decode(), raw.decode(), relative, prior.get("blocks", {}).get(relative))
            elif relative == "scripts/ai_dispatch_config.json":
                config = merge_defaults(json.loads(old) if old else {}, defaults)
                for option, key in ((args.name, "project_name"), (args.base_branch, "base_branch"),
                                    (args.claude_config_dir, "claude_config_dir"), (args.surface, "claude_surface")):
                    if option:
                        config[key] = option
                raw = old if old is not None and config == json.loads(old) else encoded(config)
            elif old is not None and old != raw and digest(old) != prior.get("files", {}).get(relative):
                raise ValueError("existing file differs from managed template: " + relative)
        except (ValueError, UnicodeError) as exc:
            conflicts.append(str(exc))
            continue
        hashes[relative] = digest(raw)
        if old != raw:
            plans[relative] = raw
    manifest = encoded({"template": "codex-claude-workflow", "version": VERSION, "files": hashes, "blocks": blocks})
    if not manifest_path.exists() or manifest_path.read_bytes() != manifest:
        plans[MANIFEST] = manifest
    result = {"status": "CONFLICT" if conflicts else ("PLANNED" if not args.apply else "INSTALLED"),
              "project": str(project), "version": VERSION, "changed_files": sorted(plans), "conflicts": conflicts,
              "note": "No model calls, credentials copied, commits, pushes or merges."}
    if conflicts:
        return result, 2
    if args.apply:
        project.mkdir(parents=True, exist_ok=True)
        if not has_git:
            subprocess.run(["git", "init", "--initial-branch", branch, str(project)], check=True, capture_output=True)
        for relative, raw in plans.items():
            atomic(safe_target(project, relative), raw)
        result["status"] = "INSTALLED" if plans else "UNCHANGED"
    return result, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--name")
    parser.add_argument("--base-branch")
    parser.add_argument("--claude-config-dir", help="existing personal subscription profile; never a token")
    parser.add_argument("--surface", choices=("cli", "desktop"))
    parser.add_argument("--init-git", action="store_true")
    parser.add_argument("--no-ci", action="store_true")
    parser.add_argument("--apply", action="store_true", help="write the plan; otherwise preview only")
    args = parser.parse_args(argv)
    try:
        result, code = install(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result, code = {"status": "BLOCKED", "reason": str(exc)}, 1
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
