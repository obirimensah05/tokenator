# tokenator - instructions for the installing agent

You are an AI coding agent helping a user install **tokenator**. Your job is to
run the interview yourself, in your own words, and enable only what the user
approves. This works from any agent CLI, which is what makes tokenator
tool-agnostic.

## Golden rule

Ask, do not assume. Every strategy is opt-in. Enable nothing the user has not
said yes to. If the user asks what something means, explain it plainly, then ask
again.

## Flow

1. **Gateway question, ask this first:**
   > "Do you want to get effectively more out of your tokens?"

   If no: stop. Change nothing.
   If yes: give the one-line framing before the details:
   > "tokenator sets up token-saving strategies as your defaults. Nothing bypasses
   > limits, spend caps, provider terms, or auth. It saves tokens through timing,
   > output compression, and prompting discipline. I'll ask about each one. Say
   > yes, no, or ask me to explain."

2. **Pick the target tool.** Ask which coding-agent CLI the defaults are for
   (Claude Code, Codex, Gemini CLI, Cursor, other). This decides which always-on
   instructions file the behavioral defaults are written to.

3. **Ask about each strategy, one yes/no at a time.** If the user wants an
   explanation, give the matching blurb below, then re-ask. Do not batch them
   into one wall of questions; let the user decide each.

   - `headroom` - time rolling-window resets to land in work blocks
   - `caveman` - compressed communication, keep every fact exact
   - `rtk` - compress shell-command output (Rust Token Killer)
   - `model_routing` - plan strong, implement cheap
   - `agent_splitting` - fan independent work to parallel subagents
   - `context_caching` - do not reload the same context again

4. **Enable only the approved ones.** For each yes, run:
   ```bash
   scripts/tokenator.py enable <strategy>
   ```
   Or set the tool and enable in one interview via `scripts/tokenator.py setup`
   if the user would rather answer the prompts directly.

5. **Confirm.** Run `scripts/tokenator.py status` and show the user what is on.

## Explanations to use verbatim if asked

Run `scripts/tokenator.py explain <strategy>` to print the canonical text, or
paraphrase these:

- **headroom** Providers often meter a rolling window that starts the moment you
  first use the CLI. Ping at the start of work blocks so resets land while you
  work, not overnight. Does not raise any limit.
- **caveman** Agent drops filler and pleasantries, keeps every path, flag, and
  number exact. Around 75% fewer reply tokens. Reversible per message.
- **rtk** Local proxy that compresses shell-command output 60-90% before it hits
  context. Needs the `rtk` binary (`brew install rtk`).
- **model_routing** Strong model for planning and hard reasoning, cheap model for
  mechanical edits and boilerplate. Pay premium only for thinking.
- **agent_splitting** Decompose independent work and fan it to parallel
  subagents, each holding only its slice, returning conclusions not raw dumps.
- **context_caching** Keep stable repeated context up front so it is served from
  a prompt cache instead of re-billed every turn. Do not re-read unchanged files.

## Notes

- The four discipline strategies (`caveman`, `model_routing`, `agent_splitting`,
  `context_caching`, plus an `rtk` usage hint) are written as a single fenced,
  reversible block into the tool's instructions file.
- `headroom` prints a cron line and can install it with consent.
- `rtk` needs its binary and a hook; enabling it will offer to install both.
- Never enable a strategy the user did not approve. Reversal is
  `scripts/tokenator.py disable <strategy>`.
