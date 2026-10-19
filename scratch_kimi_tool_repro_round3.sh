#!/usr/bin/env bash
# Round 3: same request shape as round 1, but using the REAL task 1
# (0c2c668a, the DiD econometrics question) problem text as the user
# message, instead of the trivial "capital of France" question -- to test
# whether the real task's length/content is what triggers the 400.
set -euo pipefail

if [ -z "${TR_API_KEY:-}" ]; then
  echo "TR_API_KEY is not set" >&2
  exit 1
fi

curl -s https://router-api.0g.ai/v1/chat/completions \
  -H "Authorization: Bearer $TR_API_KEY" \
  -H "Content-Type: application/json" \
  -d @scratch_kimi_round3_body.json
echo
