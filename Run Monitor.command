#!/bin/bash
# Double-click me to update the competitor monitor and open the dashboard.
# First run sets everything up automatically (needs internet; takes a few
# minutes longer). The API key lives in the .env file next to this script.
set -e
cd "$(dirname "$0")"

echo "── Competitor Monitor ──────────────────────────────"

if [ ! -d .venv ]; then
  echo "First run — setting things up (one-time, ~1 minute)..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet -r requirements.txt
fi

if [ -f .env ]; then
  set -a; source .env; set +a
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "⚠️  No API key found. Put it in a file named .env in this folder:"
  echo "    ANTHROPIC_API_KEY=sk-ant-..."
  read -r -p "Press Enter to close." _
  exit 1
fi

echo "Checking competitor sites — this takes about 10 minutes. Leave this
window open; the dashboard opens by itself when it finishes."
.venv/bin/python -m scraper

open site/index.html
echo "Done — dashboard opened in your browser."
sleep 3
