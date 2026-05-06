#!/usr/bin/env bash
set -euo pipefail

pip install --user -r requirements.txt
npm install -g @anthropic-ai/claude-code
