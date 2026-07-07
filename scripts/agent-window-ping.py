#!/usr/bin/env python3
"""Start/advance rolling usage windows for external coding-agent CLIs.

This intentionally sends a tiny no-op prompt to tools like Codex CLI and Claude
Code on a schedule, so their rolling usage windows start before your actual work
block.

It does not bypass hard limits, monthly spend caps, provider ToS, or auth. It is
just timing hygiene for rolling windows.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import subprocess
from pathlib import Path

PROMPT = os.getenv(
    "AGENT_WINDOW_PING_PROMPT",
    "ping: start or advance the rolling usage window; reply OK only; no work needed",
)
TIMEOUT = int(os.getenv("AGENT_WINDOW_PING_TIMEOUT", "120"))
LOG = Path(os.getenv("AGENT_WINDOW_PING_LOG", str(Path.home() / ".agent-window-ping.log"))).expanduser()
LOG.parent.mkdir(parents=True, exist_ok=True)


def split_cmd(env_key: str, fallback: list[str]) -> list[str]:
    raw = os.getenv(env_key)
    return shlex.split(raw) if raw else fallback


COMMANDS: dict[str, list[str] | None] = {
    "codex": split_cmd(
        "AGENT_WINDOW_PING_CODEX_CMD",
        [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            PROMPT,
        ],
    ),
    "claude-code": split_cmd(
        "AGENT_WINDOW_PING_CLAUDE_CMD",
        [
            "claude",
            "--print",
            "--permission-mode",
            "plan",
            "--model",
            os.getenv("AGENT_WINDOW_PING_CLAUDE_MODEL", "sonnet"),
            PROMPT,
        ],
    ),
}

# Disable a target by setting e.g. AGENT_WINDOW_PING_CODEX_ENABLED=0.
ENABLED = {
    "codex": os.getenv("AGENT_WINDOW_PING_CODEX_ENABLED", "1") != "0",
    "claude-code": os.getenv("AGENT_WINDOW_PING_CLAUDE_ENABLED", "1") != "0",
}


def run(name: str, cmd: list[str]) -> dict:
    started = dt.datetime.now(dt.timezone.utc)
    try:
        p = subprocess.run(
            cmd,
            cwd=str(Path.home()),
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
        )
        output = (p.stdout + p.stderr)[-4000:]
        ok = p.returncode == 0 and ("OK" in output or output.strip())
        return {
            "target": name,
            "ok": ok,
            "returncode": p.returncode,
            "started_at": started.isoformat(),
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "output_tail": output,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "target": name,
            "ok": False,
            "returncode": "timeout",
            "started_at": started.isoformat(),
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "output_tail": ((e.stdout or "") + (e.stderr or ""))[-4000:],
        }
    except Exception as e:
        return {
            "target": name,
            "ok": False,
            "returncode": "exception",
            "started_at": started.isoformat(),
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "output_tail": repr(e),
        }


def main() -> int:
    results = []
    for name, cmd in COMMANDS.items():
        if not ENABLED.get(name, True) or not cmd:
            continue
        results.append(run(name, cmd))

    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "results": results}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Stay quiet on partial success, so cron does not spam you. Print only if
    # every enabled target failed.
    if results and not any(r["ok"] for r in results):
        print(f"agent-window-ping: all enabled pings failed. See {LOG}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
