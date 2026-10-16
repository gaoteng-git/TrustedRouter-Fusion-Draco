#!/usr/bin/env bash
# Task-major baseline-only run for the prompt-parity experiment (branch
# baseline-prompt-parity): for EACH of the 100 tasks, generate the baseline
# (gpt-5.6-sol, upgraded SYNTHESIS_INSTRUCTION) and score it immediately,
# before moving to the next task -- same task-major rhythm as
# draco_task_major_pipeline.py, minus panels/fusion.
#
# Fresh output dir out/baseline_parity/ -- never touches out/task_major_v2/
# (the old-prompt baseline stays untouched as the historical comparison arm).
#
# Usage:
#   export TR_API_KEY=...   EXA_API_KEY=...
#   nohup bash scripts/run_baseline_parity.sh \
#     > out/baseline_parity_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &
#
# Safe to interrupt and re-run: both steps are --resume'd (append-only files,
# failed rows retried on the next invocation). A single task's failure does
# NOT abort the loop (mirrors the orchestrator's warn-and-continue behavior);
# re-running this script afterwards picks up only the failures.

cd "$(dirname "$0")/.." || exit 1

MANIFEST=data/draco-full-100.manifest.json
OUT_DIR=out/baseline_parity
IDS_FILE="$OUT_DIR/task_ids.txt"
PY=.venv/bin/python3
BASE_URL=https://router-api.0g.ai/v1

mkdir -p "$OUT_DIR"

# --- Preflight: fail fast on a bad/missing API key instead of spinning through
# all 100 tasks writing instant-401 failure rows (which is exactly what happened
# on the 2026-10-15 run: TR_API_KEY wasn't set in the launching shell, and the
# loop "processed" tasks for an hour, polluting the output files with hundreds
# of failed rows while spending nothing). One ~1-token call on the cheapest
# panel model (~$0.000002) buys certainty the key works before anything runs.
if [ -z "$TR_API_KEY" ]; then
  echo "FATAL: TR_API_KEY is not set. Export it before launching." >&2
  exit 2
fi
if [ -z "$EXA_API_KEY" ]; then
  echo "FATAL: EXA_API_KEY is not set. Export it before launching." >&2
  exit 2
fi
preflight_status=$(curl -sS --max-time 30 -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $TR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":1,"stream":false}')
if [ "$preflight_status" != "200" ]; then
  echo "FATAL: preflight auth check against $BASE_URL returned HTTP $preflight_status (expected 200)." >&2
  echo "TR_API_KEY is likely invalid/expired, or the gateway is unreachable. Aborting before any task runs." >&2
  exit 2
fi
echo "preflight OK: gateway auth verified (HTTP 200)"

if [ ! -f "$IDS_FILE" ]; then
  "$PY" -c "
import json
d = json.load(open('$MANIFEST'))
tasks = d if isinstance(d, list) else d.get('tasks', d)
with open('$IDS_FILE','w') as f:
    for t in tasks:
        f.write(t['id'] + '\n')
print('wrote', len(tasks), 'task ids to $IDS_FILE')
"
fi

total=$(wc -l < "$IDS_FILE")
i=0
while read -r tid; do
  i=$((i+1))
  echo ""
  echo "===== task $i/$total  id=$tid ====="
  "$PY" scripts/draco_agentic_solo.py \
    --manifest "$MANIFEST" \
    --output "$OUT_DIR/baseline-gpt-5.6-sol.jsonl" \
    --model gpt-5.6-sol \
    --config-id baseline \
    --task-id "$tid" \
    --base-url "$BASE_URL" \
    --doc-parser markitdown \
    --max-tool-calls 16 \
    --synthesis-max-tokens 12000 \
    --workers 1 \
    --resume --execute
  # --max-consecutive-failures 0: this run has known ok-but-empty rows
  # (finish=length, all 12000 tokens consumed by hidden reasoning, zero visible
  # content -- 10 of them, deliberately left in place / excluded from scoring).
  # Each one fails scoring with a pre-API ValueError (free, no judge call), and
  # they sit at the FRONT of the pending queue -- with the default abort
  # threshold of 5, every rejudge invocation died on them before ever reaching
  # the legitimately scoreable rows behind. Disabling the breaker here is safe:
  # the breaker exists to stop runaway PAID failures, but these particular
  # failures cost nothing.
  "$PY" scripts/draco_rejudge.py \
    "$OUT_DIR/baseline-gpt-5.6-sol.jsonl" \
    --output "$OUT_DIR/score-baseline.jsonl" \
    --judge-model gemini-3.1-pro-preview \
    --base-url "$BASE_URL" \
    --workers 1 \
    --max-consecutive-failures 0 \
    --resume --execute
done < "$IDS_FILE"

echo ""
echo "All $total tasks processed."
