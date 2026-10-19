#!/usr/bin/env bash
# 4-panel fusion with the MAIN-LIKE minimal fuser prompt (branch
# fusion-prompt-mainlike): one-paragraph FINAL_INSTRUCTION, no budget note,
# fuser = kimi-k3. Tests the "capable fuser needs no length coaching"
# hypothesis against the two prior 4-panel variants.
#
# Reuses EVERYTHING reusable:
#   - all 4 panel answers (3 old panelists from out/task_major_v2/, mimo from
#     out/fusion4_mimo/) -- no generation, no search cost;
#   - the fusion-internal judge's analyses via --judge-cache
#     (out/fusion4_mainlike/judge_cache.jsonl, prebuilt from the previous
#     4-panel runs' persisted judge_json) -- zero fresh judge calls for the
#     ~30 cached tasks. Only the kimi-k3 fuser calls and grading are new spend.
#
# New outputs in out/fusion4_mainlike/, config id fusion-4panel-mainlike.
#
# Usage:
#   export TR_API_KEY=...
#   nohup bash scripts/run_4panel_mainlike.sh \
#     > out/fusion4_mainlike_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

cd "$(dirname "$0")/.." || exit 1

MANIFEST=data/draco-full-100.manifest.json
V2=out/task_major_v2
MIMO_DIR=out/fusion4_mimo
OUT=out/fusion4_mainlike
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

"$PY" scripts/draco_client_fusion.py \
  --manifest "$MANIFEST" \
  --panel minimax-m3="$V2/panel-minimax-m3.jsonl" \
  --panel glm-5.3-flash="$V2/panel-glm-5.3-flash.jsonl" \
  --panel deepseek-v4-flash="$V2/panel-deepseek-v4-flash.jsonl" \
  --panel mimo-v2.5-pro="$MIMO_DIR/panel-mimo-v2.5-pro.jsonl" \
  --output "$OUT/fusion-4panel-mainlike.jsonl" \
  --config-id fusion-4panel-mainlike \
  --judge-model minimax-m3 \
  --fuser-model kimi-k3 \
  --judge-cache "$OUT/judge_cache.jsonl" \
  --base-url "$BASE_URL" \
  --api-key-name TR_API_KEY \
  --workers 1 \
  --require-full-panel \
  --resume --execute

"$PY" scripts/draco_rejudge.py \
  "$OUT/fusion-4panel-mainlike.jsonl" \
  --output "$OUT/score-fusion-4panel-mainlike.jsonl" \
  --judge-model gemini-3.1-pro-preview \
  --base-url "$BASE_URL" \
  --workers 1 \
  --resume --execute

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
mainlike = scores('out/fusion4_mainlike/score-fusion-4panel-mainlike.jsonl')
mm   = scores('out/fusion4_mimo/score-fusion-4panel-mimo.jsonl')
three= scores('out/task_major_v2/score-fusion-latest3panel.jsonl')
base = scores('out/task_major_v2/score-baseline.jsonl')
common = set(mainlike) & set(mm) & set(three) & set(base)
if common:
    n = len(common)
    def avg(d): return sum(d[t] for t in common)/n
    print(f"\n=== COMPARISON over {n} common tasks ===")
    print(f"4p kimi-fuser, main-like prompt: {avg(mainlike):.2f}")
    print(f"4p minimax-fuser, tuned prompt:  {avg(mm):.2f}")
    print(f"3-panel:                         {avg(three):.2f}")
    print(f"baseline:                        {avg(base):.2f}")
    w1 = sum(1 for t in common if mainlike[t] > mm[t])
    w2 = sum(1 for t in common if mainlike[t] > three[t])
    print(f"mainlike beats 4p-minimax: {w1}/{n}; beats 3-panel: {w2}/{n}")
else:
    print("no commonly scored tasks yet")
PYEOF

echo ""
echo "Done."
