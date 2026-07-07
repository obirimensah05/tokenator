# Agent Window Ping

A tiny scheduler script for **rolling usage windows** on coding-agent CLIs like **OpenAI Codex CLI** and **Claude Code**.

The idea:

> Do not wait until deep work starts to open the model window.  
> Start or advance the window before your work block, so the reset lands when you are actually working.

This is the operational version of the “unlimited tokens through time-window manipulation” idea.

Important: this does **not** bypass hard limits, monthly spend caps, provider terms, auth, or rate limits. It only helps with timing when a provider uses rolling windows.

## Why this exists

If your provider gives you a rolling window, the moment you first use the CLI matters.

Bad pattern:

```text
16:00 start work
16:02 hit limit
reset lands late at night
```

Better pattern:

```text
09:00 tiny ping
14:00 tiny ping
19:00 tiny ping
```

Now your resets are more likely to land inside useful work blocks instead of while you are asleep.

## What the script does

`scripts/agent-window-ping.py` runs tiny no-op prompts against enabled CLIs:

- `codex exec ... "ping: ... reply OK only"`
- `claude --print ... "ping: ... reply OK only"`

It writes logs to a file and stays quiet on normal/partial success so cron does not spam you.

Default log path:

```text
~/.agent-window-ping.log
```

## Install

```bash
git clone https://github.com/obirimensah05/agent-window-ping.git
cd agent-window-ping
chmod +x scripts/agent-window-ping.py
```

Verify your CLIs work:

```bash
codex --version
claude --version
```

Run once manually:

```bash
./scripts/agent-window-ping.py

tail -n 1 ~/.agent-window-ping.log
```

## Schedule with normal cron

Edit crontab:

```bash
crontab -e
```

Example cadence:

```cron
0 9,14,19 * * * /path/to/agent-window-ping/scripts/agent-window-ping.py >/tmp/agent-window-ping.out 2>&1
```

That runs daily at:

```text
09:00
14:00
19:00
```

Tune the times to your actual work blocks.

## Schedule with Hermes Agent cron

If you use [Hermes Agent](https://github.com/NousResearch/hermes-agent), you can run it as a script-only cron job.

Copy the script into Hermes' script directory:

```bash
mkdir -p ~/.hermes/scripts
cp scripts/agent-window-ping.py ~/.hermes/scripts/
chmod +x ~/.hermes/scripts/agent-window-ping.py
```

Then create a Hermes cron job with:

```text
schedule: 0 9,14,19 * * *
script: agent-window-ping.py
no_agent: true
```

`no_agent: true` matters because you do not want to spend Hermes model tokens just to run a shell script.

## Configuration

Everything is controlled by environment variables.

| Env var | Default | Purpose |
|---|---|---|
| `AGENT_WINDOW_PING_LOG` | `~/.agent-window-ping.log` | Log file path |
| `AGENT_WINDOW_PING_TIMEOUT` | `120` | Per-command timeout in seconds |
| `AGENT_WINDOW_PING_PROMPT` | no-op ping prompt | Prompt sent to each CLI |
| `AGENT_WINDOW_PING_CODEX_ENABLED` | `1` | Set `0` to disable Codex |
| `AGENT_WINDOW_PING_CLAUDE_ENABLED` | `1` | Set `0` to disable Claude Code |
| `AGENT_WINDOW_PING_CLAUDE_MODEL` | `sonnet` | Claude Code model alias |
| `AGENT_WINDOW_PING_CODEX_CMD` | built-in safe command | Override Codex command entirely |
| `AGENT_WINDOW_PING_CLAUDE_CMD` | built-in safe command | Override Claude command entirely |

Example: Codex only.

```bash
AGENT_WINDOW_PING_CLAUDE_ENABLED=0 ./scripts/agent-window-ping.py
```

Example: custom Codex model/profile.

```bash
AGENT_WINDOW_PING_CODEX_CMD='codex exec --skip-git-repo-check --ephemeral --sandbox read-only -m gpt-5.4 "ping: reply OK only"' \
  ./scripts/agent-window-ping.py
```

## Recommended cadence

For a typical operator day:

```text
09:00 morning open
14:00 afternoon reset alignment
19:00 evening deep-work alignment
```

Do not run it every hour by default. These CLIs still have session overhead, so the ping itself may consume tokens.

## Safety notes

The default commands are intentionally low-risk:

- Codex uses `--sandbox read-only`
- Claude uses `--permission-mode plan`
- The prompt asks for `OK only`
- The script does not modify repositories

But you are still responsible for your provider usage and account limits.
