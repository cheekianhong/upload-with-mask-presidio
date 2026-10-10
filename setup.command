#!/bin/zsh
set -e

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/ and reopen Terminal."
  read -r "REPLY?Press Return to close this window. "
  exit 1
fi

if ! command -v tesseract >/dev/null 2>&1; then
  echo "Tesseract is required for photos and scanned PDF pages. On macOS, install it with: brew install tesseract"
  read -r "REPLY?Press Return to close this window. "
  exit 1
fi

uv python install 3.12.12
uv sync
uv run python -m spacy download en_core_web_sm

echo
echo "Setup complete. Double-click start.command to open the app."
read -r "REPLY?Press Return to close this window. "
