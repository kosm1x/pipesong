#!/usr/bin/env bash
# resilient.sh — run a long-lived command so an SSH/console cut-off can't kill it.
# Cloud/VPS connections drop often; a dropped terminal sends SIGHUP and kills child
# processes. This detaches the work so it survives, with a log you can tail and
# (under tmux) a session you can reattach to.
#
# Usage:
#   bash scripts/resilient.sh <name> <command ...>
#
# Examples:
#   bash scripts/resilient.sh vllm \
#     python -m vllm.entrypoints.openai.api_server \
#     --model Qwen/Qwen2.5-7B-Instruct-AWQ --quantization awq --port 8000
#
#   bash scripts/resilient.sh pipesong \
#     env PYTHONPATH=src python -m uvicorn pipesong.main:app --host 0.0.0.0 --port 8080
#
# Reconnect / manage later:
#   tmux ls                       # list running sessions
#   tmux attach -t <name>         # watch live   (detach: Ctrl-b then d)
#   tail -f logs/<name>.log       # watch output without attaching
#   tmux kill-session -t <name>   # stop it
set -euo pipefail

name="${1:?usage: resilient.sh <name> <command ...>}"; shift
[ "$#" -ge 1 ] || { echo "error: no command given" >&2; exit 1; }
mkdir -p logs
log="$PWD/logs/${name}.log"

# Prefer tmux (reattachable). Best-effort install if missing and apt is present.
if ! command -v tmux >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  apt-get install -y tmux >/dev/null 2>&1 || true
fi

if command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t "$name" 2>/dev/null; then
    echo "[resilient] session '$name' already running — attach: tmux attach -t $name"
    exit 0
  fi
  tmux new-session -d -s "$name" "$*"                       # runs the command string in a shell
  tmux pipe-pane -t "$name" -o "cat >> '$log'" 2>/dev/null || true
  echo "[resilient] '$name' started in tmux — survives disconnect."
  echo "  attach: tmux attach -t $name    (detach: Ctrl-b then d)"
  echo "  logs:   tail -f $log"
  echo "  stop:   tmux kill-session -t $name"
else
  # No tmux: fully detach so a hangup on disconnect can't reach it.
  setsid nohup bash -c "$*" >"$log" 2>&1 </dev/null &
  echo "[resilient] '$name' started via setsid+nohup (pid $!) — survives disconnect (no reattach)."
  echo "  logs: tail -f $log"
  echo "  stop: kill $!"
fi

# On reconnect, sanity-check before trusting it (per session-resilience habit):
#   tmux ls ; tail -n 40 logs/<name>.log ; curl -sf localhost:<port>/health
