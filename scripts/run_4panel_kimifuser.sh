#!/usr/bin/env bash
# 4-panel fusion rerun with TWO changes vs fusion-4panel-mimo:
#   1) fuser = kimi-k3 (Prometheus 2.0's real synthesis model) instead of
#      minimax-m3 -- tests whether a stronger fuser stops the defensive
#      over-compression diagnosed on the minimax fuser;
#   2) softened budget wording in FINAL_INSTRUCTION/budget_note (source-size
#      number removed; budget framed as room to use) -- see the dated comments
#      in scripts/draco_client_fusion.py.
# NOTE: both changes land together, so score deltas vs fusion-4panel-mimo are
# the COMBINED effect (deliberate: this is a "does the fix work at all" pilot,
# not an ablation).
#
# Reuses ALL existing panel answers (3 old panelists from out/task_major_v2/,
# mimo from out/fusion4_mimo/) -- no panel generation, no new search cost.
# With --require-full-panel, only tasks where all 4 answered get fused: that is
# exactly the ~30 tasks the stopped 4-panel run completed, which is the pilot
# comparison set. New outputs in out/fusion4_kimifuser/ under config id
# fusion-4panel-kimifuser; nothing existing is touched.
#
# Usage:
#   export TR_API_KEY=...
#   nohup bash scripts/run_4panel_kimifuser.sh \
#     > out/fusion4_kimifuser_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

cd "$(dirname "$0")/.." || exit 1

MANIFEST=data/draco-full-100.manifest.json
V2=out/task_major_v2
MIMO_DIR=out/fusion4_mimo
OUT=out/fusion4_kimifuser
PY=.venv/bin/python3
BASE_URL=https://router-api.0g.ai/v1

mkdir -p "$OUT"

if [ -z "$TR_API_KEY" ]; then
  echo "FATAL: TR_API_KEY is not set." >&2; exit 2
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

# One fusion pass (scans the whole backlog of full-panel-ready tasks at once)
"$PY" scripts/draco_client_fusion.py \
  --manifest "$MANIFEST" \
  --panel minimax-m3="$V2/panel-minimax-m3.jsonl" \
  --panel glm-5.3-flash="$V2/panel-glm-5.3-flash.jsonl" \
  --panel deepseek-v4-flash="$V2/panel-deepseek-v4-flash.jsonl" \
  --panel mimo-v2.5-pro="$MIMO_DIR/panel-mimo-v2.5-pro.jsonl" \
  --output "$OUT/fusion-4panel-kimifuser.jsonl" \
  --config-id fusion-4panel-kimifuser \
  --judge-model minimax-m3 \
  --fuser-model kimi-k3 \
  --base-url "$BASE_URL" \
  --api-key-name TR_API_KEY \
  --workers 1 \
  --require-full-panel \
  --resume --execute

# Score with the official grading judge
"$PY" scripts/draco_rejudge.py \
  "$OUT/fusion-4panel-kimifuser.jsonl" \
  --output "$OUT/score-fusion-4panel-kimifuser.jsonl" \
  --judge-model gemini-3.1-pro-preview \
  --base-url "$BASE_URL" \
  --workers 1 \
  --resume --execute

# Comparison over commonly scored tasks
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
kimi = scores('out/fusion4_kimifuser/score-fusion-4panel-kimifuser.jsonl')
mm   = scores('out/fusion4_mimo/score-fusion-4panel-mimo.jsonl')
three= scores('out/task_major_v2/score-fusion-latest3panel.jsonl')
base = scores('out/task_major_v2/score-baseline.jsonl')
common = set(kimi) & set(mm) & set(three) & set(base)
if common:
    n = len(common)
    def avg(d): return sum(d[t] for t in common)/n
    print(f"\n=== COMPARISON over {n} common tasks ===")
    print(f"4p kimi-fuser (new prompt): {avg(kimi):.2f}")
    print(f"4p minimax-fuser (old):     {avg(mm):.2f}")
    print(f"3-panel:                    {avg(three):.2f}")
    print(f"baseline:                   {avg(base):.2f}")
    wk = sum(1 for t in common if kimi[t] > mm[t])
    w3 = sum(1 for t in common if kimi[t] > three[t])
    print(f"kimi-fuser beats minimax-fuser: {wk}/{n}; beats 3-panel: {w3}/{n}")
else:
    print("no commonly scored tasks yet")
PYEOF

echo ""
echo "Done."
