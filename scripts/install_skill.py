#!/usr/bin/env python3
"""Install this skill locally; preserve changes to an existing installation."""
import argparse
import json
import os
from pathlib import Path
import sys

from bootstrap import ROOT, atomic, digest, encoded, safe_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", default=str(Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "skills/codex-claude-workflow"))
    args = parser.parse_args()
    target = Path(args.dest).expanduser().resolve()
    marker = safe_target(target, "installation.json")
    prior = json.loads(marker.read_bytes()) if marker.exists() else {}
    sources = [ROOT / "SKILL.md"]
    sources += [p for folder in ("agents", "assets", "scripts") for p in (ROOT / folder).rglob("*")
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    plans, hashes = {}, {}
    try:
        for source in sources:
            relative = source.relative_to(ROOT).as_posix()
            path = safe_target(target, relative)
            raw = source.read_bytes()
            if path.exists():
                old = path.read_bytes()
                if old != raw and digest(old) != prior.get("files", {}).get(relative):
                    raise ValueError("local skill file was modified: " + relative)
            if not path.exists() or path.read_bytes() != raw:
                plans[relative] = raw
            hashes[relative] = digest(raw)
        for relative, raw in plans.items():
            atomic(safe_target(target, relative), raw)
        atomic(marker, encoded({"source": "https://github.com/lyclccylcq/codex-claude-workflow", "files": hashes}))
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 1
    print(json.dumps({"status": "INSTALLED", "path": str(target), "changed_files": len(plans)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
