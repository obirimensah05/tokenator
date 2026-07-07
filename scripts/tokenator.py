#!/usr/bin/env python3
"""tokenator - get more out of your tokens, tool-agnostically.

tokenator is a small setup helper for coding-agent CLIs (Claude Code, Codex,
Gemini CLI, Cursor, and friends). It interviews you once, then wires up the
token-saving strategies you approve so they run as defaults:

  headroom          time rolling-window resets to land inside work blocks
  caveman           ultra-compressed communication mode
  rtk               compress shell-command output (Rust Token Killer)
  model_routing     plan with a strong model, implement with a cheap one
  agent_splitting   fan independent work out to parallel subagents
  context_caching   cache repeated context so it is not reloaded again

Nothing here bypasses hard limits, spend caps, provider terms, auth, or rate
limits. It is timing hygiene, output compression, and prompting discipline.

Usage:
  tokenator setup                 run the interactive interview
  tokenator status                show what is enabled
  tokenator explain [strategy]    explain a strategy (or all)
  tokenator enable  <strategy>    enable one strategy non-interactively
  tokenator disable <strategy>    disable one strategy
  tokenator headroom              run the headroom ping now (for cron)
  tokenator apply                 re-materialize assets from current config
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths / config
# --------------------------------------------------------------------------- #

HOME = Path.home()
CONFIG_DIR = Path(os.getenv("TOKENATOR_CONFIG_DIR", str(HOME / ".config" / "tokenator")))
CONFIG_PATH = CONFIG_DIR / "config.json"
BLOCK_START = "<!-- tokenator:start (managed - do not edit inside) -->"
BLOCK_END = "<!-- tokenator:end -->"

STRATEGIES = ["headroom", "caveman", "rtk", "model_routing", "agent_splitting", "context_caching"]

# Where each known tool keeps its always-on instruction file, relative to $HOME.
TOOLS = {
    "claude-code": {"label": "Claude Code", "instructions": ".claude/CLAUDE.md", "rtk_agent": None},
    "codex": {"label": "OpenAI Codex CLI", "instructions": ".codex/AGENTS.md", "rtk_agent": "cursor"},
    "gemini": {"label": "Gemini CLI", "instructions": ".gemini/GEMINI.md", "rtk_agent": "gemini"},
    "cursor": {"label": "Cursor", "instructions": ".cursorrules", "rtk_agent": "cursor"},
    "other": {"label": "Other / generic agent", "instructions": ".config/tokenator/AGENTS.md", "rtk_agent": None},
}

DEFAULT_CONFIG = {
    "tool": "claude-code",
    "instructions_file": str(HOME / ".claude" / "CLAUDE.md"),
    "strategies": {
        "headroom": {"enabled": False, "cadence": "0 9,14,19 * * *", "clis": ["codex", "claude-code"]},
        "caveman": {"enabled": False},
        "rtk": {"enabled": False},
        "model_routing": {"enabled": False, "strong": "the strongest model", "cheap": "a cheaper/faster model"},
        "agent_splitting": {"enabled": False},
        "context_caching": {"enabled": False},
    },
}

EXPLAIN = {
    "headroom": (
        "HEADROOM - timing, not bypassing.\n"
        "  Many providers meter you on a *rolling window* that starts the moment you\n"
        "  first use the CLI. If you first touch it at 16:00 and hit the cap at 16:02,\n"
        "  your reset lands late at night when you are asleep. tokenator sends tiny\n"
        "  no-op pings at the start of your work blocks (e.g. 09:00, 14:00, 19:00) so\n"
        "  resets line up with when you actually work. It does NOT raise any limit."
    ),
    "caveman": (
        "CAVEMAN - compressed communication.\n"
        "  Tells the agent to drop filler, articles, and pleasantries while keeping\n"
        "  full technical accuracy. Roughly ~75% fewer tokens on the model's replies,\n"
        "  which also leaves more room in the context window. You can always ask it to\n"
        "  switch back to full prose for a specific answer."
    ),
    "rtk": (
        "RTK - Rust Token Killer (github.com/rtk-ai/rtk).\n"
        "  A tiny local proxy that compresses the OUTPUT of shell commands the agent\n"
        "  runs (git, tests, docker, ls, file reads, ...). It strips noise, groups and\n"
        "  dedups, and typically cuts 60-90% of the tokens those command outputs would\n"
        "  otherwise burn. Installs a hook so `git status` transparently becomes\n"
        "  `rtk git status`. Needs the `rtk` binary (brew install rtk)."
    ),
    "model_routing": (
        "MODEL ROUTING - plan strong, implement cheap.\n"
        "  Use the most capable (expensive) model for planning, architecture, and hard\n"
        "  reasoning, then switch to a cheaper/faster model for the mechanical typing:\n"
        "  edits, boilerplate, renames, test scaffolding. You pay premium rates only\n"
        "  for the thinking, not for every keystroke."
    ),
    "agent_splitting": (
        "AGENT SPLITTING - parallel subagents.\n"
        "  For work that decomposes into independent pieces (search across N dirs,\n"
        "  review M files, migrate K call-sites), fan it out to parallel subagents\n"
        "  instead of dragging everything through one giant context. Each subagent\n"
        "  keeps only its slice, returns the conclusion, and the main thread stays lean."
    ),
    "context_caching": (
        "CONTEXT CACHING - do not reload the same thing twice.\n"
        "  Put stable, repeated context (specs, schemas, house rules, big files you\n"
        "  keep referencing) up front and reuse it so providers can serve it from a\n"
        "  prompt cache instead of re-reading and re-billing it every turn. tokenator\n"
        "  writes a small context-bundle convention and tells the agent to prefer\n"
        "  cached bundles over re-pasting."
    ),
}

# --------------------------------------------------------------------------- #
# tiny io helpers
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    # merge onto defaults so new keys always exist
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg.update({k: v for k, v in data.items() if k != "strategies"})
    for s, sv in data.get("strategies", {}).items():
        cfg["strategies"].setdefault(s, {}).update(sv)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def ask_yes_no(prompt: str, explain_key: str | None = None) -> bool:
    """Yes/no with '?' -> print explanation and re-ask. Default No."""
    while True:
        raw = input(f"{prompt} [y/n/?] ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no", ""):
            return False
        if raw in ("?", "explain", "help"):
            if explain_key:
                print("\n" + EXPLAIN[explain_key] + "\n")
            else:
                print("  y = yes, n = no, ? = explain")
            continue
        print("  please answer y, n, or ? for an explanation")


# --------------------------------------------------------------------------- #
# instruction-file block management (tool-agnostic behavioral strategies)
# --------------------------------------------------------------------------- #

def behavioral_block(cfg: dict) -> str:
    """Build the managed instruction block from enabled behavioral strategies."""
    s = cfg["strategies"]
    lines = [
        BLOCK_START,
        "# tokenator - active token strategies",
        "The following defaults were chosen by the user via `tokenator setup`.",
        "Apply them by default; the user can opt out per-message.",
        "",
    ]
    if s["caveman"]["enabled"]:
        lines += [
            "## Caveman mode (default ON)",
            "- Answer in a compressed style: drop filler, articles, hedging, and pleasantries.",
            "- Keep every technical fact, path, flag, and number exact. Terse != vague.",
            "- Full prose only when the user asks for it.",
            "",
        ]
    if s["model_routing"]["enabled"]:
        strong = s["model_routing"].get("strong", "the strongest model")
        cheap = s["model_routing"].get("cheap", "a cheaper model")
        lines += [
            "## Model routing (default ON)",
            f"- Plan / architect / reason hard with {strong}.",
            f"- Do the mechanical implementation (edits, boilerplate, renames, scaffolding) with {cheap}.",
            "- Announce the switch briefly; do not pay premium rates for keystrokes.",
            "",
        ]
    if s["agent_splitting"]["enabled"]:
        lines += [
            "## Split work across agents (default ON)",
            "- When work decomposes into independent pieces, fan out to parallel subagents.",
            "- Each subagent keeps only its slice of context and returns the conclusion, not raw dumps.",
            "- Keep the main thread lean; do not drag every file through one context.",
            "",
        ]
    if s["context_caching"]["enabled"]:
        lines += [
            "## Cache repeated context (default ON)",
            "- Put stable, reused context (specs, schemas, house rules, large reference files) up front and keep it verbatim so it can be served from the provider prompt cache.",
            "- Prefer referencing a stable context bundle over re-pasting the same information every turn.",
            "- Do not re-read a file you already loaded this session unless it changed.",
            "",
        ]
    if s["rtk"]["enabled"]:
        lines += [
            "## RTK (default ON)",
            "- Prefer running noisy shell commands through `rtk` (e.g. `rtk git status`, `rtk cargo test`, `rtk ls .`) so their output is compressed before it enters context.",
            "",
        ]
    lines.append(BLOCK_END)
    return "\n".join(lines).rstrip() + "\n"


def write_block(cfg: dict) -> Path:
    target = Path(cfg["instructions_file"]).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text() if target.exists() else ""
    block = behavioral_block(cfg)

    has_behavioral = any(
        cfg["strategies"][k]["enabled"]
        for k in ("caveman", "model_routing", "agent_splitting", "context_caching", "rtk")
    )

    if BLOCK_START in existing and BLOCK_END in existing:
        pre = existing.split(BLOCK_START)[0].rstrip("\n")
        post = existing.split(BLOCK_END, 1)[1].lstrip("\n")
        parts = [p for p in (pre, block.rstrip("\n") if has_behavioral else "", post) if p]
        new = "\n\n".join(parts).rstrip() + "\n"
    elif has_behavioral:
        new = (existing.rstrip() + "\n\n" if existing.strip() else "") + block
    else:
        new = existing

    target.write_text(new)
    return target


# --------------------------------------------------------------------------- #
# per-strategy enablers (side effects beyond the instruction block)
# --------------------------------------------------------------------------- #

def enable_headroom(cfg: dict, interactive: bool) -> None:
    hc = cfg["strategies"]["headroom"]
    script = Path(__file__).resolve().parent.parent / "scripts" / "tokenator.py"
    cron = f'{hc["cadence"]} {script} headroom >/tmp/tokenator-headroom.out 2>&1'
    print("\n  headroom: add this to `crontab -e` to ping your work blocks:")
    print(f"    {cron}")
    if interactive and shutil.which("crontab") and ask_yes_no("  install this crontab line now?"):
        try:
            current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        except Exception:
            current = ""
        if "tokenator.py headroom" in current or "tokenator headroom" in current:
            print("  headroom cron already present, leaving it.")
        else:
            new = (current.rstrip() + "\n" if current.strip() else "") + cron + "\n"
            subprocess.run(["crontab", "-"], input=new, text=True)
            print("  installed.")


def enable_rtk(cfg: dict, interactive: bool) -> None:
    if shutil.which("rtk"):
        print("  rtk binary found.")
    else:
        print("\n  rtk binary not found. Install it with one of:")
        print("    brew install rtk")
        print("    curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh")
        if not interactive:
            return
        if shutil.which("brew") and ask_yes_no("  run `brew install rtk` now?"):
            subprocess.run(["brew", "install", "rtk"])
    if shutil.which("rtk"):
        agent = TOOLS.get(cfg["tool"], {}).get("rtk_agent")
        cmd = ["rtk", "init", "-g"] + (["--agent", agent] if agent else [])
        if not interactive or ask_yes_no(f"  run `{' '.join(cmd)}` to install the rtk hook?"):
            subprocess.run(cmd)


ENABLERS = {"headroom": enable_headroom, "rtk": enable_rtk}


# --------------------------------------------------------------------------- #
# headroom ping runner (config-driven)
# --------------------------------------------------------------------------- #

def run_headroom(cfg: dict) -> int:
    import datetime as _dt

    ping = os.getenv(
        "TOKENATOR_PING_PROMPT",
        "ping: start or advance the rolling usage window; reply OK only; no work needed",
    )
    timeout = int(os.getenv("TOKENATOR_PING_TIMEOUT", "120"))
    log = Path(os.getenv("TOKENATOR_HEADROOM_LOG", str(HOME / ".tokenator-headroom.log")))
    log.parent.mkdir(parents=True, exist_ok=True)

    commands = {
        "codex": ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", ping],
        "claude-code": ["claude", "--print", "--permission-mode", "plan",
                        "--model", os.getenv("TOKENATOR_CLAUDE_MODEL", "sonnet"), ping],
    }
    wanted = cfg["strategies"]["headroom"].get("clis", ["codex", "claude-code"])
    results = []
    for name in wanted:
        cmd = commands.get(name)
        if not cmd or not shutil.which(cmd[0]):
            continue
        started = _dt.datetime.now(_dt.timezone.utc)
        try:
            p = subprocess.run(cmd, cwd=str(HOME), text=True, capture_output=True, timeout=timeout)
            out = (p.stdout + p.stderr)[-4000:]
            ok = p.returncode == 0 and bool(out.strip())
            rc = p.returncode
        except subprocess.TimeoutExpired as e:
            out, ok, rc = ((e.stdout or "") + (e.stderr or ""))[-4000:], False, "timeout"
        except Exception as e:  # noqa: BLE001
            out, ok, rc = repr(e), False, "exception"
        results.append({"target": name, "ok": ok, "returncode": rc,
                        "started_at": started.isoformat(),
                        "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                        "output_tail": out})

    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(), "results": results},
                           ensure_ascii=False) + "\n")
    if results and not any(r["ok"] for r in results):
        print(f"tokenator headroom: all enabled pings failed. See {log}")
        return 1
    return 0


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_setup(_argv: list[str]) -> int:
    cfg = load_config()
    print("tokenator - get more out of your tokens.\n")

    if not ask_yes_no("Do you want to get effectively more out of your tokens?"):
        print("No problem. Nothing changed. Run `tokenator setup` any time.")
        return 0

    print(
        "\nGreat. tokenator can set up a handful of token-saving strategies as your\n"
        "defaults. None of them bypass limits, spend caps, provider terms, or auth -\n"
        "they save tokens through timing, output compression, and prompting discipline.\n"
        "\nI'll ask about each one. Answer y, n, or ? for an explanation.\n"
    )

    # which tool are we wiring behavioral defaults into
    tools = list(TOOLS.keys())
    print("Which coding-agent CLI should I set the defaults for?")
    for i, t in enumerate(tools, 1):
        print(f"  {i}) {TOOLS[t]['label']}")
    while True:
        raw = input(f"Choose 1-{len(tools)} [1]: ").strip() or "1"
        if raw.isdigit() and 1 <= int(raw) <= len(tools):
            cfg["tool"] = tools[int(raw) - 1]
            break
        print("  invalid choice")
    cfg["instructions_file"] = str(HOME / TOOLS[cfg["tool"]]["instructions"])
    print(f"  -> defaults will be written to {cfg['instructions_file']}\n")

    prompts = {
        "headroom": "Enable HEADROOM (time your rolling-window resets to your work blocks)?",
        "caveman": "Enable CAVEMAN (compressed communication, ~75% fewer reply tokens)?",
        "rtk": "Enable RTK (compress shell-command output, -60 to -90% tokens)?",
        "model_routing": "Enable MODEL ROUTING (plan with a strong model, implement with a cheap one)?",
        "agent_splitting": "Enable AGENT SPLITTING (fan independent work out to parallel subagents)?",
        "context_caching": "Enable CONTEXT CACHING (don't reload the same context again and again)?",
    }
    for s in STRATEGIES:
        cfg["strategies"][s]["enabled"] = ask_yes_no(prompts[s], explain_key=s)

    save_config(cfg)
    print("\nApplying...")
    target = write_block(cfg)
    if any(cfg["strategies"][k]["enabled"] for k in
           ("caveman", "model_routing", "agent_splitting", "context_caching", "rtk")):
        print(f"  wrote behavioral defaults to {target}")
    for s in ("headroom", "rtk"):
        if cfg["strategies"][s]["enabled"]:
            ENABLERS[s](cfg, interactive=True)

    print("\nDone. Enabled strategies:")
    for s in STRATEGIES:
        if cfg["strategies"][s]["enabled"]:
            print(f"  - {s}")
    print(f"\nConfig: {CONFIG_PATH}\nChange later with `tokenator enable/disable <strategy>`.")
    return 0


def cmd_status(_argv: list[str]) -> int:
    cfg = load_config()
    print(f"tool: {TOOLS.get(cfg['tool'], {}).get('label', cfg['tool'])}")
    print(f"instructions file: {cfg['instructions_file']}")
    print("strategies:")
    for s in STRATEGIES:
        print(f"  {'[x]' if cfg['strategies'][s]['enabled'] else '[ ]'} {s}")
    return 0


def cmd_explain(argv: list[str]) -> int:
    keys = argv or STRATEGIES
    for k in keys:
        if k not in EXPLAIN:
            print(f"unknown strategy: {k}\nknown: {', '.join(STRATEGIES)}")
            return 1
        print(EXPLAIN[k] + "\n")
    return 0


def cmd_toggle(argv: list[str], enabled: bool) -> int:
    if not argv or argv[0] not in STRATEGIES:
        print(f"usage: tokenator {'enable' if enabled else 'disable'} <{'|'.join(STRATEGIES)}>")
        return 1
    cfg = load_config()
    cfg["strategies"][argv[0]]["enabled"] = enabled
    save_config(cfg)
    write_block(cfg)
    if enabled and argv[0] in ENABLERS:
        ENABLERS[argv[0]](cfg, interactive=sys.stdin.isatty())
    print(f"{argv[0]} {'enabled' if enabled else 'disabled'}.")
    return 0


def cmd_apply(_argv: list[str]) -> int:
    cfg = load_config()
    target = write_block(cfg)
    print(f"re-applied behavioral defaults to {target}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "setup":
        return cmd_setup(rest)
    if cmd == "status":
        return cmd_status(rest)
    if cmd == "explain":
        return cmd_explain(rest)
    if cmd == "enable":
        return cmd_toggle(rest, True)
    if cmd == "disable":
        return cmd_toggle(rest, False)
    if cmd == "apply":
        return cmd_apply(rest)
    if cmd == "headroom":
        return run_headroom(load_config())
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    print(f"unknown command: {cmd}\n")
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
