# tokenator

Get more out of your tokens. Tool-agnostic.

`tokenator` is a small setup helper for coding-agent CLIs (Claude Code, OpenAI
Codex CLI, Gemini CLI, Cursor, and friends). It interviews you once, then wires
up the token-saving strategies you approve so they run as your defaults.

It does **not** bypass hard limits, monthly spend caps, provider terms, auth, or
rate limits. It saves tokens through three honest levers: timing, output
compression, and prompting discipline.

## The interview

Run it and it asks one gateway question first, then one question per strategy.
Answer `y`, `n`, or `?` for an explanation of that strategy before you decide.

```text
Do you want to get effectively more out of your tokens? [y/n/?] y

Which coding-agent CLI should I set the defaults for?
  1) Claude Code   2) Codex   3) Gemini CLI   4) Cursor   5) Other

Enable HEADROOM (time your rolling-window resets to your work blocks)? [y/n/?]
Enable CAVEMAN (compressed communication, ~75% fewer reply tokens)?     [y/n/?]
Enable RTK (compress shell-command output, -60 to -90% tokens)?         [y/n/?]
Enable MODEL ROUTING (plan strong, implement cheap)?                    [y/n/?]
Enable AGENT SPLITTING (fan work out to parallel subagents)?            [y/n/?]
Enable CONTEXT CACHING (don't reload the same context again)?           [y/n/?]
```

Whatever you say yes to becomes a default. Say no and nothing is touched.

## The six strategies

| Strategy | Lever | What it does |
|---|---|---|
| `headroom` | timing | Sends tiny no-op pings at the start of your work blocks so rolling-window resets land while you are working, not while you sleep. |
| `caveman` | discipline | Tells the agent to answer in a compressed style (no filler) while keeping every fact exact. |
| `rtk` | compression | Installs [rtk](https://github.com/rtk-ai/rtk) (Rust Token Killer), a local proxy that compresses shell-command output by 60-90% before it enters context. |
| `model_routing` | discipline | Plan and reason with a strong model, implement the mechanical parts with a cheaper one. |
| `agent_splitting` | discipline | Fan independent work out to parallel subagents so no single context carries everything. |
| `context_caching` | discipline | Keep stable, repeated context up front so it is served from a prompt cache instead of re-billed every turn. |

The four discipline strategies are written as a managed block into your tool's
always-on instructions file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`,
`.cursorrules`, ...). `headroom` prints or installs a cron line. `rtk` installs
the binary and its hook.

## Install

```bash
git clone https://github.com/obirimensah05/tokenator.git
cd tokenator
chmod +x scripts/tokenator.py

# optional: put it on your PATH
ln -s "$PWD/scripts/tokenator.py" /usr/local/bin/tokenator

tokenator setup
```

Only Python 3 is required for the interview itself. Individual strategies pull
in what they need (`rtk`, a `crontab`) at the moment you enable them.

## Let your agent set it up for you

tokenator is designed so a coding agent can run the interview *for* you. Point
your agent at [`AGENTS.md`](AGENTS.md) (or just say "set up tokenator") and it
will ask you the same yes/no questions, explain any you want explained, and
enable only what you approve. That is why it is tool-agnostic: the agent doing
the asking can be any of them.

## Commands

```bash
tokenator setup                 # run the interview
tokenator status                # show what is enabled
tokenator explain [strategy]    # explain one strategy, or all of them
tokenator enable  <strategy>    # turn one on non-interactively
tokenator disable <strategy>    # turn one off
tokenator apply                 # re-write the instruction block from config
tokenator headroom              # run the headroom ping now (this is the cron entry)
```

Config lives at `~/.config/tokenator/config.json`. Edit it by hand or with the
`enable`/`disable` commands; run `tokenator apply` to re-materialize.

## headroom details

The `headroom` strategy runs via `tokenator headroom`.

```cron
0 9,14,19 * * * /path/to/tokenator/scripts/tokenator.py headroom >/tmp/tokenator-headroom.out 2>&1
```

It runs tiny read-only, plan-mode pings, logs to `~/.tokenator-headroom.log`,
and stays quiet unless every enabled ping fails so cron does not spam you. Tune
the cadence in the config to your real work blocks. Do not run it every hour;
the pings themselves have session overhead.

Environment overrides: `TOKENATOR_PING_PROMPT`, `TOKENATOR_PING_TIMEOUT`,
`TOKENATOR_HEADROOM_LOG`, `TOKENATOR_CLAUDE_MODEL`, `TOKENATOR_CONFIG_DIR`.

## Safety notes

- Nothing here defeats a provider's limits, caps, or terms. Read them.
- `headroom` pings are read-only, plan-mode, and ask for `OK only`.
- The instruction block is fenced by markers and fully reversible (`tokenator disable`).
- You are responsible for your own account usage.

## License

MIT. See [LICENSE](LICENSE).
