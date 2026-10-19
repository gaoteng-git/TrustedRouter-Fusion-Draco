#!/usr/bin/env bash
# Reproduces the exact first-round request shape draco_agentic_solo.py sends
# to a panel model (system prompt + web_search/web_fetch/bash tool schemas +
# tool_choice=auto + max_tokens=8000 + temperature=0.2 + stream=false),
# against kimi-k3, with a trivial user question instead of a real DRACO task.
# Needs TR_API_KEY in the environment.
set -euo pipefail

if [ -z "${TR_API_KEY:-}" ]; then
  echo "TR_API_KEY is not set" >&2
  exit 1
fi

curl -s https://router-api.0g.ai/v1/chat/completions \
  -H "Authorization: Bearer $TR_API_KEY" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{"model": "kimi-k3", "messages": [{"role": "system", "content": "You are a deep research analyst. Answer the user's research task with a complete, source-grounded report. You have three tools: web_search (find sources), web_fetch (read a specific URL in full), and bash (run shell / python for any calculation or data manipulation). Search iteratively: start broad, then fetch the most authoritative primary sources and verify key figures with bash. Cite source URLs inline. Show quantitative work explicitly and state uncertainty plainly. Do not mention benchmark rubrics. When you have enough evidence, write the final report as plain text with no further tool calls. Your final report must contain only the report itself — no planning, reasoning narration, or scratchpad text."}, {"role": "user", "content": "Research task:\nWhat is the capital of France? Answer in one sentence."}], "tools": [{"type": "function", "function": {"name": "web_search", "description": "Search the web for current, authoritative sources. Returns titles, URLs, and extracts.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "The search query."}, "num_results": {"type": "integer", "description": "How many results (max 10)."}}, "required": ["query"]}}}, {"type": "function", "function": {"name": "web_fetch", "description": "Fetch and extract the readable text of a specific URL (HTML or PDF).", "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "The URL to fetch."}}, "required": ["url"]}}}, {"type": "function", "function": {"name": "bash", "description": "Run a shell command in an isolated sandbox (python3 available, no network). Use for calculations and data manipulation.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "The shell command to run."}}, "required": ["command"]}}}], "tool_choice": "auto", "max_tokens": 8000, "temperature": 0.2, "stream": false}
JSON
echo
