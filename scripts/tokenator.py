#!/usr/bin/env python3
"""tokenator - get more out of your tokens, tool-agnostically.

tokenator is a small setup helper for coding-agent CLIs (Claude Code, Codex,
Gemini CLI, Cursor, and friends). It interviews you once, then wires up the
token-saving strategies you approve so they run as defaults:

  rolling_ping      time rolling-window resets to land inside work blocks
  headroom          context compression layer (headroomlabs-ai/headroom)
  caveman           compressed-communication skill (juliusbrussee/caveman)
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
  tokenator rolling-ping          run the rolling-window ping now (for cron)
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

STRATEGIES = [
    "rolling_ping",
    "headroom",
    "caveman",
    "rtk",
    "model_routing",
    "agent_splitting",
    "context_caching",
]

# Strategies whose behavior is a managed block in the tool's instructions file.
BEHAVIORAL = ["model_routing", "agent_splitting", "context_caching"]

# Per-tool details: where the always-on instructions file lives (relative to
# $HOME), how `headroom wrap` names the tool, and how `rtk init` targets it.
TOOLS = {
    "claude-code": {"label": "Claude Code", "instructions": ".claude/CLAUDE.md",
                    "headroom_wrap": "claude", "rtk_init": ["rtk", "init", "-g"]},
    "codex": {"label": "OpenAI Codex CLI", "instructions": ".codex/AGENTS.md",
              "headroom_wrap": "codex", "rtk_init": ["rtk", "init", "-g"]},
    "gemini": {"label": "Gemini CLI", "instructions": ".gemini/GEMINI.md",
               "headroom_wrap": "gemini", "rtk_init": ["rtk", "init", "-g", "--gemini"]},
    "cursor": {"label": "Cursor", "instructions": ".cursorrules",
               "headroom_wrap": "cursor", "rtk_init": ["rtk", "init", "-g", "--agent", "cursor"]},
    "other": {"label": "Other / generic agent", "instructions": ".config/tokenator/AGENTS.md",
              "headroom_wrap": None, "rtk_init": ["rtk", "init", "-g"]},
}

DEFAULT_CONFIG = {
    "tool": "claude-code",
    "instructions_file": str(HOME / ".claude" / "CLAUDE.md"),
    "strategies": {
        "rolling_ping": {"enabled": False, "cadence": "0 9,14,19 * * *", "clis": ["codex", "claude-code"]},
        "headroom": {"enabled": False},
        "caveman": {"enabled": False},
        "rtk": {"enabled": False},
        "model_routing": {"enabled": False, "strong": "the strongest model", "cheap": "a cheaper/faster model"},
        "agent_splitting": {"enabled": False},
        "context_caching": {"enabled": False},
    },
}

EXPLAIN = {
    "rolling_ping": (
        "ROLLING PING - timing, not bypassing.\n"
        "  Many providers meter you on a *rolling window* that starts the moment you\n"
        "  first use the CLI. If you first touch it at 16:00 and hit the cap at 16:02,\n"
        "  your reset lands late at night when you are asleep. tokenator sends tiny\n"
        "  no-op pings at the start of your work blocks (e.g. 09:00, 14:00, 19:00) so\n"
        "  resets line up with when you actually work. It does NOT raise any limit."
    ),
    "headroom": (
        "HEADROOM - context compression layer (github.com/headroomlabs-ai/headroom).\n"
        "  A local compression layer that shrinks what gets sent to the model: 60-95%\n"
        "  on JSON, 15-20% on coding-agent context, while preserving accuracy.\n"
        "  Compression is reversible (originals cached locally, retrievable on demand)\n"
        "  and it can share context across Claude, Codex, and Gemini. Installs with\n"
        "  `pip install headroom-ai[all]`, then `headroom wrap <tool>` to inject it."
    ),
    "caveman": (
        "CAVEMAN - compressed-communication skill (github.com/juliusbrussee/caveman).\n"
        "  A skill/plugin that makes the agent answer in terse, fragment-based language\n"
        "  (\"why use many token when few token do trick\") while keeping code, commands,\n"
        "  and errors exact. Around 65% fewer output tokens. Installs itself into every\n"
        "  detected agent; toggle in-session with `/caveman` and `normal mode`, pick a\n"
        "  level with `/caveman [lite|full|ultra]`, see savings with `/caveman-stats`."
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
# instruction-file block (the pure-discipline behavioral strategies)
# --------------------------------------------------------------------------- #

def behavioral_block(cfg: dict) -> str:
    s = cfg["strategies"]
    lines = [
        BLOCK_START,
        "# tokenator - active token strategies",
        "The following defaults were chosen by the user via `tokenator setup`.",
        "Apply them by default; the user can opt out per-message.",
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
    lines.append(BLOCK_END)
    return "\n".join(lines).rstrip() + "\n"


def write_block(cfg: dict) -> Path:
    target = Path(cfg["instructions_file"]).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text() if target.exists() else ""
    block = behavioral_block(cfg)
    has_behavioral = any(cfg["strategies"][k]["enabled"] for k in BEHAVIORAL)

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
# per-strategy enablers (external installs / side effects)
# --------------------------------------------------------------------------- #

def enable_rolling_ping(cfg: dict, interactive: bool) -> None:
    rc = cfg["strategies"]["rolling_ping"]
    script = Path(__file__).resolve()
    cron = f'{rc["cadence"]} {script} rolling-ping >/tmp/tokenator-rolling-ping.out 2>&1'
    print("\n  rolling_ping: add this to `crontab -e` to ping your work blocks:")
    print(f"    {cron}")
    if interactive and shutil.which("crontab") and ask_yes_no("  install this crontab line now?"):
        try:
            current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        except Exception:
            current = ""
        if "tokenator.py rolling-ping" in current or "tokenator rolling-ping" in current:
            print("  rolling-ping cron already present, leaving it.")
        else:
            new = (current.rstrip() + "\n" if current.strip() else "") + cron + "\n"
            subprocess.run(["crontab", "-"], input=new, text=True)
            print("  installed.")


def _py_installer() -> list[str] | None:
    """Pick an available Python installer, preferring isolated pipx."""
    if shutil.which("pipx"):
        return ["pipx", "install"]
    for pip in ("pip3", "pip"):
        if shutil.which(pip):
            return [pip, "install"]
    return None


def enable_headroom(cfg: dict, interactive: bool) -> None:
    if not shutil.which("headroom"):
        installer = _py_installer()
        print("\n  headroom not found. Install it with (pipx keeps it isolated):")
        print('    pipx install "headroom-ai[all]"   # or: pip3 install "headroom-ai[all]"')
        if interactive and installer and ask_yes_no(f'  run `{" ".join(installer)} "headroom-ai[all]"` now?'):
            subprocess.run(installer + ["headroom-ai[all]"])
    if shutil.which("headroom"):
        wrap = TOOLS.get(cfg["tool"], {}).get("headroom_wrap")
        if not wrap:
            print("  run `headroom wrap <your-tool>` to inject the compression layer.")
        elif not interactive or ask_yes_no(f"  run `headroom wrap {wrap}` now?"):
            subprocess.run(["headroom", "wrap", wrap])
        print("  (undo any time with `headroom unwrap <tool>`.)")


def enable_caveman(cfg: dict, interactive: bool) -> None:
    if shutil.which("caveman"):
        print("  caveman already installed.")
        return
    url = "https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh"
    print("\n  caveman installs itself into every detected agent via:")
    print(f"    curl -fsSL {url} | bash")
    if not interactive:
        return
    if shutil.which("curl") and shutil.which("bash") and ask_yes_no("  run the caveman installer now?"):
        subprocess.run(f"curl -fsSL {url} | bash", shell=True)
        print("  toggle in-session with `/caveman` and `normal mode`.")


def enable_rtk(cfg: dict, interactive: bool) -> None:
    if not shutil.which("rtk"):
        print("\n  rtk binary not found. Install it with one of:")
        print("    brew install rtk")
        print("    curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh")
        if interactive and shutil.which("brew") and ask_yes_no("  run `brew install rtk` now?"):
            subprocess.run(["brew", "install", "rtk"])
    if shutil.which("rtk"):
        cmd = TOOLS.get(cfg["tool"], {}).get("rtk_init", ["rtk", "init", "-g"])
        if not interactive or ask_yes_no(f"  run `{' '.join(cmd)}` to install the rtk hook?"):
            subprocess.run(cmd)


# Strategies with an install/side-effect beyond the instruction block.
ENABLERS = {
    "rolling_ping": enable_rolling_ping,
    "headroom": enable_headroom,
    "caveman": enable_caveman,
    "rtk": enable_rtk,
}


# --------------------------------------------------------------------------- #
# rolling-ping runner (config-driven)
# --------------------------------------------------------------------------- #

def run_rolling_ping(cfg: dict) -> int:
    import datetime as _dt

    ping = os.getenv(
        "TOKENATOR_PING_PROMPT",
        "ping: start or advance the rolling usage window; reply OK only; no work needed",
    )
    timeout = int(os.getenv("TOKENATOR_PING_TIMEOUT", "120"))
    log = Path(os.getenv("TOKENATOR_ROLLING_PING_LOG", str(HOME / ".tokenator-rolling-ping.log")))
    log.parent.mkdir(parents=True, exist_ok=True)

    commands = {
        "codex": ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", ping],
        "claude-code": ["claude", "--print", "--permission-mode", "plan",
                        "--model", os.getenv("TOKENATOR_CLAUDE_MODEL", "sonnet"), ping],
    }
    wanted = cfg["strategies"]["rolling_ping"].get("clis", ["codex", "claude-code"])
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
        print(f"tokenator rolling-ping: all enabled pings failed. See {log}")
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
    print(f"  -> discipline defaults will be written to {cfg['instructions_file']}\n")

    prompts = {
        "rolling_ping": "Enable ROLLING PING (time your rolling-window resets to your work blocks)?",
        "headroom": "Enable HEADROOM (context compression layer, headroomlabs-ai/headroom)?",
        "caveman": "Enable CAVEMAN (compressed-communication skill, juliusbrussee/caveman)?",
        "rtk": "Enable RTK (compress shell-command output, -60 to -90% tokens)?",
        "model_routing": "Enable MODEL ROUTING (plan with a strong model, implement with a cheap one)?",
        "agent_splitting": "Enable AGENT SPLITTING (fan independent work out to parallel subagents)?",
        "context_caching": "Enable CONTEXT CACHING (don't reload the same context again and again)?",
    }
    for s in STRATEGIES:
        cfg["strategies"][s]["enabled"] = ask_yes_no(prompts[s], explain_key=s)

    save_config(cfg)
    print("\nApplying...")
    if any(cfg["strategies"][k]["enabled"] for k in BEHAVIORAL):
        target = write_block(cfg)
        print(f"  wrote discipline defaults to {target}")
    else:
        write_block(cfg)
    for s in STRATEGIES:
        if cfg["strategies"][s]["enabled"] and s in ENABLERS:
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
    print(f"re-applied discipline defaults to {target}")
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
    if cmd in ("rolling-ping", "rolling_ping"):
        return run_rolling_ping(load_config())
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    print(f"unknown command: {cmd}\n")
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
