#!/usr/bin/env bash
# 4-panel experiment with BALANCED detail-preserving prompt
#
# 基于 run_4panel_mimo.sh，但使用新的 fusion-prompt-detail-balanced 配置：
# - FINAL_INSTRUCTION 优化：保留分析深度和文档引用，针对 analysis-1/2/3/6 失分
# - fuser_max_tokens = 11500（留 500 buffer，避免撞上限）
# - 积极措辞（"comprehensive, ample space"）代替压力词（"budget, limited"）
#
# 与原版 mimo 的对比：
# - run_4panel_mimo.sh 使用 fusion-prompt-mainlike（最简指令，细节丢失严重）
# - 本脚本测试 balanced 版本能否在保留细节的同时不撞上限
#
# 输出目录: out/fusion4_mimo_balanced/
# 复用面板结果: out/task_major_v2/ (3 panel) + out/fusion4_mimo/panel-mimo-v2.5-pro.jsonl
#
# Usage:
#   export TR_API_KEY=... EXA_API_KEY=...
#   nohup bash scripts/run_4panel_mimo_balanced.sh \
#     > out/fusion4_mimo_balanced_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

cd "$(dirname "$0")/.." || exit 1

MANIFEST=data/draco-full-100.manifest.json
V2=out/task_major_v2
MIMO_PANEL=out/fusion4_mimo/panel-mimo-v2.5-pro.jsonl
OUT=out/fusion4_mimo_balanced
IDS_FILE="$OUT/task_ids.txt"
PY=.venv/bin/python3
BASE_URL=https://router-api.0g.ai/v1
MIMO=mimo-v2.5-pro

mkdir -p "$OUT"

# --- Preflight: fail fast on bad/missing API key ---
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

# --- 复用已有的 mimo panel 结果（不重新生成）---
if [ ! -f "$MIMO_PANEL" ]; then
  echo "FATAL: $MIMO_PANEL not found. Run run_4panel_mimo.sh first to generate panel answers." >&2
  exit 2
fi
echo "Using existing mimo panel results from $MIMO_PANEL"

# --- task_ids.txt ---
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

  # (1) 4-panel fusion with IMPORTANCE-PRIORITIZED prompt + kimi-k3 fuser
  #     核心策略：借鉴3-panel时期的"重要内容优先"设计（commit 8590e28, cc8458a）
  #     针对kimi-k3的简洁输出特性（~3435 tokens avg），不强迫它变长，
  #     而是引导它把load-bearing内容（quantitative findings, analysis, citations,
  #     regulatory/policy details）放在前面。Budget note用95% of cap（~10925 tokens），
  #     说"expected to approach this limit"但不提source-size（避免防御性压缩）。
  #     --fuser-max-tokens 使用默认值 11500
  "$PY" scripts/draco_client_fusion.py \
    --manifest "$MANIFEST" \
    --panel minimax-m3="$V2/panel-minimax-m3.jsonl" \
    --panel glm-5.3-flash="$V2/panel-glm-5.3-flash.jsonl" \
    --panel deepseek-v4-flash="$V2/panel-deepseek-v4-flash.jsonl" \
    --panel "$MIMO"="$MIMO_PANEL" \
    --output "$OUT/fusion-4panel-mimo-balanced.jsonl" \
    --config-id fusion-4panel-mimo-balanced \
    --judge-model minimax-m3 \
    --fuser-model kimi-k3 \
    --base-url "$BASE_URL" \
    --api-key-name TR_API_KEY \
    --workers 1 \
    --require-full-panel \
    --resume --execute

  # (2) score the fusion
  "$PY" scripts/draco_rejudge.py \
    "$OUT/fusion-4panel-mimo-balanced.jsonl" \
    --output "$OUT/score-fusion-4panel-mimo-balanced.jsonl" \
    --judge-model gemini-3.1-pro-preview \
    --base-url "$BASE_URL" \
    --workers 1 \
    --resume --execute

  # (3) running comparison: balanced vs mainlike vs baseline
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
balanced = scores('out/fusion4_mimo_balanced/score-fusion-4panel-mimo-balanced.jsonl')
mainlike = scores('out/fusion4_mimo/score-fusion-4panel-mimo.jsonl')
base = scores('out/task_major_v2/score-baseline.jsonl')
common = set(balanced) & set(mainlike) & set(base)
if common:
    n = len(common)
    bal = sum(balanced[t] for t in common)/n
    main = sum(mainlike[t] for t in common)/n
    b = sum(base[t] for t in common)/n
    w_vs_main = sum(1 for t in common if balanced[t] > mainlike[t])
    w_vs_base = sum(1 for t in common if balanced[t] > base[t])
    print(f">>> RUNNING AVG over {n} scored tasks:")
    print(f"    balanced={bal:.2f}  mainlike={main:.2f}  baseline={b:.2f}")
    print(f"    balanced beats mainlike: {w_vs_main}/{n}  |  balanced beats baseline: {w_vs_base}/{n}")
else:
    print(">>> RUNNING AVG: no commonly scored tasks yet")
PYEOF

done < "$IDS_FILE"

echo ""
echo "All $total tasks processed."
echo ""
echo "Final comparison:"
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
balanced = scores('out/fusion4_mimo_balanced/score-fusion-4panel-mimo-balanced.jsonl')
mainlike = scores('out/fusion4_mimo/score-fusion-4panel-mimo.jsonl')
base = scores('out/task_major_v2/score-baseline.jsonl')
common = set(balanced) & set(mainlike) & set(base)
if common:
    n = len(common)
    bal = sum(balanced[t] for t in common)/n
    main = sum(mainlike[t] for t in common)/n
    b = sum(base[t] for t in common)/n
    w_vs_main = sum(1 for t in common if balanced[t] > mainlike[t])
    w_vs_base = sum(1 for t in common if balanced[t] > base[t])
    print(f"=== FINAL RESULTS ({n} tasks) ===")
    print(f"balanced:  {bal:.2f}  (beats mainlike {w_vs_main}/{n}, beats baseline {w_vs_base}/{n})")
    print(f"mainlike:  {main:.2f}")
    print(f"baseline:  {b:.2f}")
    print()
    # 找出balanced改进最大的题目
    improvements = [(t, balanced[t] - mainlike[t]) for t in common]
    improvements.sort(key=lambda x: x[1], reverse=True)
    print("Top 5 improvements (balanced vs mainlike):")
    for i, (t, diff) in enumerate(improvements[:5], 1):
        print(f"  {i}. {t}: +{diff:.1f}")
    print()
    print("Top 5 regressions (balanced vs mainlike):")
    for i, (t, diff) in enumerate(improvements[-5:], 1):
        print(f"  {i}. {t}: {diff:.1f}")
else:
    print("No results to compare yet")
PYEOF
