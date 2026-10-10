#!/bin/zsh
set -e

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Open README.md and follow the one-time setup steps."
  read -r "REPLY?Press Return to close this window. "
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "This app is not set up yet. Double-click setup.command first."
  read -r "REPLY?Press Return to close this window. "
  exit 1
fi

if ! uv run --no-sync python -c "import spacy; spacy.load('en_core_web_sm')" >/dev/null 2>&1; then
  echo "The English Presidio model is missing. Double-click setup.command to finish setup."
  read -r "REPLY?Press Return to close this window. "
  exit 1
fi

( for attempt in {1..30}; do
    if curl -fsS http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then
      open http://127.0.0.1:8501
      exit 0
    fi
    sleep 1
  done ) >/dev/null 2>&1 &

uv run --no-sync streamlit run app.py --server.address 127.0.0.1
