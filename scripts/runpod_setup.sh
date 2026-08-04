#!/usr/bin/env bash
# RunPod bootstrap for pipesong Flux/Pipecat-1.x LIVE-CALL validation (runbook Step 4).
# TensorDock fallback per PLAN.md §5.8. Run on a fresh RunPod instance with:
#   - a CUDA GPU with >=16-24 GB VRAM (RTX 4090 / 3090 / A5000 / L4 all fit Qwen2.5-7B-AWQ)
#   - root, and a PUBLIC TCP port for :8080 (Telnyx must reach the webhook)
#
# This is BEST-EFFORT and UNTESTED here (no GPU in the authoring env). Read before running.
#
# Provide these as env vars before running (no secrets are hard-coded):
#   GH_TOKEN              GitHub PAT with read on the PRIVATE repo kosm1x/pipesong
#   DEEPGRAM_API_KEY      Deepgram key
#   TELNYX_API_KEY        (for the live call)
#   TELNYX_PHONE_NUMBER   e.g. +12678840093
#   APP_PUBLIC_URL        the box's public URL RunPod gives you, e.g. wss://<pod>-8080.proxy.runpod.net
#
# Usage:  GH_TOKEN=... DEEPGRAM_API_KEY=... [TELNYX_*=...] bash runpod_setup.sh
set -euo pipefail

: "${GH_TOKEN:?set GH_TOKEN (PAT with repo read for kosm1x/pipesong)}"
: "${DEEPGRAM_API_KEY:?set DEEPGRAM_API_KEY}"

echo "== 0. system deps =="
apt-get update -y
apt-get install -y git python3.11 python3.11-venv ffmpeg docker.io docker-compose-plugin curl

echo "== 1. clone the private branch via token (no SSH keys) =="
if [ ! -d pipesong ]; then
  git clone --branch flux-pipecat1x "https://${GH_TOKEN}@github.com/kosm1x/pipesong.git"
fi
cd pipesong
git fetch origin && git checkout flux-pipecat1x && git pull --ff-only

echo "== 2. python 3.11+ venv + deps (Pipecat 1.x) =="
python3.11 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt          # pipecat-ai==1.7.0 (+ kokoro, deepgram, websocket, openai)
pip install "vllm==0.6.6"                 # match prod LLM server; this branch does NOT change the LLM

echo "== 3. infra: Postgres + MinIO =="
docker compose up -d

echo "== 4. .env (fill any blanks before the live call) =="
cat > .env <<EOF
DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY}
TELNYX_API_KEY=${TELNYX_API_KEY:-}
TELNYX_PHONE_NUMBER=${TELNYX_PHONE_NUMBER:-}
TELNYX_CONNECTION_ID=${TELNYX_CONNECTION_ID:-}
TELNYX_WEBHOOK_SECRET=${TELNYX_WEBHOOK_SECRET:-}
APP_PUBLIC_URL=${APP_PUBLIC_URL:-}
EOF

echo "== 5. import smoke (catches Pipecat 1.x drift, e.g. audit item W1 Kokoro Language enum) =="
PYTHONPATH=src python -c "import pipesong.pipeline, pipesong.main, pipesong.processors; print('imports OK')"

echo "== 6. start vLLM (Qwen2.5-7B-AWQ) in the background =="
nohup python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-7B-Instruct-AWQ --quantization awq --port 8000 \
  > vllm.log 2>&1 &
echo "   waiting for vLLM /health ..."
for i in $(seq 1 60); do curl -sf http://localhost:8000/health && break || sleep 5; done

echo "== 7. (optional, no GPU needed) C1 probe — does Flux accept 8 kHz? =="
echo "   provide an 8 kHz mono PCM16 sample, then: python scripts/flux_8k_probe.py sample_8k.wav"

echo "== 8. start pipesong; point the Telnyx TeXML webhook at \$APP_PUBLIC_URL/telnyx/webhook, then call in Spanish =="
PYTHONPATH=src python -m uvicorn pipesong.main:app --host 0.0.0.0 --port 8080

# After the call: check turn-taking + transcripts; GET /calls/{id}/latency for per-turn timing.
# If all good -> merge flux-pipecat1x (the watch flips to MERGED). See docs/validate-flux-8khz.md §5.
