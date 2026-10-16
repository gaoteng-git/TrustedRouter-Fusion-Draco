#!/usr/bin/env bash
# 4-panel experiment: add mimo-v2.5-pro as a 4th panelist on top of the EXISTING
# 3-panel results (minimax-m3 / glm-5.3-flash / deepseek-v4-flash, reused as-is
# from out/task_major_v2/ -- never regenerated, never modified).
#
# Task-major rhythm, one task at a time:
#   1) generate mimo-v2.5-pro's panel answer for task K (new file, own dir)
#   2) fuse 4 panels (judge=fuser=minimax-m3, --require-full-panel, NEW config id
#      "fusion-4panel-mimo" -> new output files; the 3-panel fusion results stay
#      untouched for comparison)
#   3) score the fusion with the official judge (gemini-3.1-pro-preview)
#   4) print the running average: 4-panel vs 3-panel on the tasks scored SO FAR
#   then move to task K+1.
#
# All artifacts go to out/fusion4_mimo/ (plus reads of out/task_major_v2/).
# Safe to interrupt and re-run (--resume everywhere, append-only).
#
# Usage:
#   export TR_API_KEY=... EXA_API_KEY=...
#   nohup bash scripts/run_4panel_mimo.sh \
#     > out/fusion4_mimo_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

cd "$(dirname "$0")/.." || exit 1

MANIFEST=data/draco-full-100.manifest.json
V2=out/task_major_v2
OUT=out/fusion4_mimo
IDS_FILE="$OUT/task_ids.txt"
PY=.venv/bin/python3
BASE_URL=https://router-api.0g.ai/v1
MIMO=mimo-v2.5-pro

mkdir -p "$OUT"

# --- Preflight: fail fast on bad/missing API key (lesson from the 2026-10-15
# 401 incident: without this, the loop spins for an hour writing garbage). ---
if [ -z "$TR_API_KEY" ]; then
  echo "FATAL: TR_API_KEY is not set." >&2; exit 2
fi
if [ -z "$EXA_API_KEY" ]; then
  echo "FATAL: EXA_API_KEY is not set." >&2; exit 2
fi
preflight_status=$(curl -sS --max-time 30 -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $TR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":1,"stream":false}')
if [ "$preflight_status" != "200" ]; then
  echo "FATAL: preflight auth check returned HTTP $preflight_status (expected 200). Aborting." >&2
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

  # (1) mimo panel solo for this task (sec_facts enabled; note: the 3 reused
  # panelists ran the 80 non-financial tasks with sec_facts disabled -- a known,
  # accepted inconsistency documented in the experiment notes).
  "$PY" scripts/draco_agentic_solo.py \
    --manifest "$MANIFEST" \
    --output "$OUT/panel-$MIMO.jsonl" \
    --model "$MIMO" \
    --config-id "panel-$MIMO" \
    --task-id "$tid" \
    --base-url "$BASE_URL" \
    --doc-parser markitdown \
    --max-tool-calls 16 \
    --synthesis-max-tokens 12000 \
    --workers 1 \
    --resume --execute

  # (2) 4-panel fusion (whole-backlog scan per invocation, same as orchestrator)
  "$PY" scripts/draco_client_fusion.py \
    --manifest "$MANIFEST" \
    --panel minimax-m3="$V2/panel-minimax-m3.jsonl" \
    --panel glm-5.3-flash="$V2/panel-glm-5.3-flash.jsonl" \
    --panel deepseek-v4-flash="$V2/panel-deepseek-v4-flash.jsonl" \
    --panel "$MIMO"="$OUT/panel-$MIMO.jsonl" \
    --output "$OUT/fusion-4panel-mimo.jsonl" \
    --config-id fusion-4panel-mimo \
    --judge-model minimax-m3 \
    --fuser-model minimax-m3 \
    --base-url "$BASE_URL" \
    --api-key-name TR_API_KEY \
    --workers 1 \
    --require-full-panel \
    --resume --execute

  # (3) score the fusion
  "$PY" scripts/draco_rejudge.py \
    "$OUT/fusion-4panel-mimo.jsonl" \
    --output "$OUT/score-fusion-4panel-mimo.jsonl" \
    --judge-model gemini-3.1-pro-preview \
    --base-url "$BASE_URL" \
    --workers 1 \
    --resume --execute

  # (4) running comparison: 4-panel vs 3-panel on tasks scored so far
  "$PY" - <<'PYEOF'
import json, os
def scores(path):
    best = {}
    if not os.path.exists(path): return best
    for line in open(path):
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        if r.get('status') == 'failed': continue
        tid = r.get('task_id'); s = r.get('score')
        if tid and s is not None: best[tid] = s
    return best
four = scores('out/fusion4_mimo/score-fusion-4panel-mimo.jsonl')
three = scores('out/task_major_v2/score-fusion-latest3panel.jsonl')
base = scores('out/task_major_v2/score-baseline.jsonl')
common = set(four) & set(three) & set(base)
if common:
    n = len(common)
    f = sum(four[t] for t in common)/n
    t3 = sum(three[t] for t in common)/n
    b = sum(base[t] for t in common)/n
    w43 = sum(1 for t in common if four[t] > three[t])
    print(f">>> RUNNING AVG over {n} scored tasks: 4panel={f:.2f}  3panel={t3:.2f}  baseline={b:.2f}  | 4p beats 3p: {w43}/{n}")
else:
    print(">>> RUNNING AVG: no commonly scored tasks yet")
PYEOF

done < "$IDS_FILE"

echo ""
echo "All $total tasks processed."
