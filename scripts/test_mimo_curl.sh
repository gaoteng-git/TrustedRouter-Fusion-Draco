#!/usr/bin/env bash
# Smoke test: send mimo-v2.5-pro the EXACT same request shape the eval harness
# sends for a panel member's first research-phase call (same system prompt, all
# 4 tool schemas verbatim, max_tokens=8000, temperature=0.2, tool_choice=auto,
# stream=false). Only the task text is a short sample.
#
# Usage:
#   export TR_API_KEY=...
#   bash scripts/test_mimo_curl.sh
#
# PASS criteria:
#   - HTTP 200 with choices[0].message.tool_calls containing a well-formed call
#     (ideal), or a plain text answer (acceptable: format-compatible).
#   - HTTP 400 complaining about temperature / tools / tool_choice etc. means a
#     model-specific exception is needed in agentic_tools.py (one-line fix).

if [ -z "$TR_API_KEY" ]; then
  echo "FATAL: TR_API_KEY is not set." >&2
  exit 2
fi

curl -sS --max-time 120 -X POST "https://router-api.0g.ai/v1/chat/completions" \
  -H "Authorization: Bearer $TR_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @- <<'JSON'
{
  "model": "mimo-v2.5-pro",
  "messages": [
    {
      "role": "system",
      "content": "You are a deep research analyst. Answer the user's research task with a complete, source-grounded report. You have three tools: web_search (find sources), web_fetch (read a specific URL in full), and bash (run shell / python for any calculation or data manipulation). Search iteratively: start broad, then fetch the most authoritative primary sources and verify key figures with bash. Cite source URLs inline. Show quantitative work explicitly and state uncertainty plainly. Do not mention benchmark rubrics. When you have enough evidence, write the final report as plain text with no further tool calls. Your final report must contain only the report itself — no planning, reasoning narration, or scratchpad text."
    },
    {
      "role": "user",
      "content": "Research task:\nWhat was Apple's net income in fiscal year 2024, and how did it compare to fiscal year 2023? Cite authoritative sources."
    }
  ],
  "max_tokens": 8000,
  "temperature": 0.2,
  "tool_choice": "auto",
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "web_search",
        "description": "Search the web for current, authoritative sources. Returns titles, URLs, and extracts.",
        "parameters": {
          "type": "object",
          "properties": {
            "query": {"type": "string", "description": "The search query."},
            "num_results": {"type": "integer", "description": "How many results (max 10)."}
          },
          "required": ["query"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "web_fetch",
        "description": "Fetch and extract the readable text of a specific URL (HTML or PDF).",
        "parameters": {
          "type": "object",
          "properties": {
            "url": {"type": "string", "description": "The URL to fetch."}
          },
          "required": ["url"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "bash",
        "description": "Run a shell command in an isolated sandbox (python3 available, no network). Use for calculations and data manipulation.",
        "parameters": {
          "type": "object",
          "properties": {
            "command": {"type": "string", "description": "The shell command to run."}
          },
          "required": ["command"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "sec_facts",
        "description": "Fetch EXACT, as-filed financial figures for a U.S. SEC filer straight from EDGAR XBRL (operating cash flow, net income, cash, capex, share repurchases, dividends, debt issuance/repayment, share-based comp, stockholders' equity, revenue). Returns dollar-exact values with their unit, period end date, fiscal year/quarter, and form. Use this INSTEAD of web_fetch for any reported number from a named U.S. public company — the values are scale- and period-unambiguous, so it avoids thousands-vs-millions and prior-year-column mistakes.",
        "parameters": {
          "type": "object",
          "properties": {
            "ticker": {"type": "string", "description": "Ticker symbol or 10-digit CIK of the U.S. filer."},
            "metric": {"type": "string", "description": "Optional. One of: operating_cash_flow, net_income, cash, capex, repurchases, dividends, debt_issued, debt_repaid, share_based_comp, stockholders_equity, revenue. Omit to get the full capital-allocation set."},
            "fiscal_year": {"type": "integer", "description": "Optional fiscal year filter (e.g. 2024)."}
          },
          "required": ["ticker"]
        }
      }
    }
  ],
  "stream": false
}
JSON
echo ""
