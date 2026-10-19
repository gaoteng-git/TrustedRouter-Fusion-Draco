#!/usr/bin/env bash
# 4-panel fusion with IMPORTANCE-PRIORITIZED prompt, task-by-task execution
#
# 关键特点：
# 1. 逐题执行：fusion → 立即打分 → 立即输出对比
# 2. 复用4个panel结果 + judge cache（无panel/judge调用，零额外成本）
# 3. 实时日志flush（python -u，实时看到进度）
# 4. 每题完成后立即显示 running average
#
# 输出目录: out/fusion4_balanced/
#
# Usage:
#   export TR_API_KEY=... EXA_API_KEY=...
#   bash scripts/run_4panel_balanced_taskwise.sh \
#     > out/fusion4_balanced_run_$(date +%Y%m%d_%H%M%S).log 2>&1

cd "$(dirname "$0")/.." || exit 1

MANIFEST=data/draco-full-100.manifest.json
V2=out/task_major_v2
MIMO_PANEL=out/fusion4_mimo/panel-mimo-v2.5-pro.jsonl
MAINLIKE=out/fusion4_mainlike
OUT=out/fusion4_balanced
PY="python3"
BASE_URL=https://router-api.0g.ai/v1

# 设置PYTHONPATH以便系统Python可以找到项目模块
export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

mkdir -p "$OUT"

# --- Preflight ---
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
echo ""

# --- 检查必需的输入文件 ---
if [ ! -f "$MIMO_PANEL" ]; then
  echo "FATAL: $MIMO_PANEL not found. Run run_4panel_mimo.sh first." >&2
  exit 2
fi
if [ ! -f "$MAINLIKE/judge_cache.jsonl" ]; then
  echo "FATAL: $MAINLIKE/judge_cache.jsonl not found. Need judge cache." >&2
  exit 2
fi
echo "Using existing panel results:"
echo "  - 3-panel from $V2/"
echo "  - mimo panel from $MIMO_PANEL"
echo "  - judge cache from $MAINLIKE/judge_cache.jsonl"
echo ""

# --- 读取全部task IDs ---
all_task_ids=$($PY -u -c "
import json
d = json.load(open('$MANIFEST'))
tasks = d if isinstance(d, list) else d.get('tasks', d)
for t in tasks:
    print(t['id'])
")

total=$(echo "$all_task_ids" | wc -l)
i=0

echo "=========================================="
echo "Starting task-by-task fusion + scoring"
echo "Total tasks: $total"
echo "=========================================="
echo ""

for tid in $all_task_ids; do
  i=$((i+1))
  echo ""
  echo "===== [$i/$total] $tid ====="

  # --- 检查是否已完成 ---
  already_scored=$($PY -u -c "
import json, sys
tid = sys.argv[1]
path = 'out/fusion4_balanced/score-fusion-4panel-balanced.jsonl'
try:
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == tid and r.get('status') != 'failed':
                print('yes')
                sys.exit(0)
except FileNotFoundError:
    pass
print('no')
" "$tid")

  if [ "$already_scored" = "yes" ]; then
    echo "[skip] Task already scored, skipping..."

    # 仍然显示对比
    echo "[compare] Task $tid comparison:"
    tid="$tid" $PY -u - <<'PYEOF'
import json, os, sys

tid = os.environ['tid']

def get_score(path, task_id):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == task_id and r.get('status') != 'failed':
                return r.get('score')
    return None

balanced = get_score('out/fusion4_balanced/score-fusion-4panel-balanced.jsonl', tid)
mainlike = get_score('out/fusion4_mainlike/score-fusion-4panel-mainlike.jsonl', tid)
three_panel = get_score('out/task_major_v2/score-fusion-latest3panel.jsonl', tid)
baseline = get_score('out/task_major_v2/score-baseline.jsonl', tid)

print(f"  balanced:   {balanced:.1f if balanced else 'N/A'}", flush=True)
print(f"  mainlike:   {mainlike:.1f if mainlike else 'N/A'}", flush=True)
print(f"  3-panel:    {three_panel:.1f if three_panel else 'N/A'}", flush=True)
print(f"  baseline:   {baseline:.1f if baseline else 'N/A'}", flush=True)

if balanced and mainlike:
    diff = balanced - mainlike
    print(f"  → balanced vs mainlike: {diff:+.1f}", flush=True)
if balanced and three_panel:
    diff = balanced - three_panel
    print(f"  → balanced vs 3-panel:  {diff:+.1f}", flush=True)
PYEOF
    continue
  fi

  # (1) Fusion - 用--resume模式，只处理这个task
  echo "[fusion] Starting fusion for task $tid..."
  $PY -u scripts/draco_client_fusion.py \
    --manifest "$MANIFEST" \
    --panel minimax-m3="$V2/panel-minimax-m3.jsonl" \
    --panel glm-5.3-flash="$V2/panel-glm-5.3-flash.jsonl" \
    --panel deepseek-v4-flash="$V2/panel-deepseek-v4-flash.jsonl" \
    --panel mimo-v2.5-pro="$MIMO_PANEL" \
    --output "$OUT/fusion-4panel-balanced.jsonl" \
    --config-id fusion-4panel-balanced \
    --judge-model minimax-m3 \
    --judge-cache "$MAINLIKE/judge_cache.jsonl" \
    --fuser-model kimi-k3 \
    --base-url "$BASE_URL" \
    --api-key-name TR_API_KEY \
    --workers 1 \
    --require-full-panel \
    --resume --execute 2>&1

  # 检查fusion是否成功（检查输出文件是否包含这个task）
  fusion_ok=$($PY -u -c "
import json, sys
tid = sys.argv[1]
path = 'out/fusion4_balanced/fusion-4panel-balanced.jsonl'
try:
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == tid:
                print('yes')
                sys.exit(0)
except FileNotFoundError:
    pass
print('no')
" "$tid")

  if [ "$fusion_ok" != "yes" ]; then
    echo "[fusion] FAILED - task not found in output" >&2
    echo "[score] Skipping scoring for this task" >&2
    continue
  fi
  echo "[fusion] Completed"

  # (2) Score this task immediately
  echo "[score] Starting scoring for task $tid..."
  $PY -u scripts/draco_rejudge.py \
    "$OUT/fusion-4panel-balanced.jsonl" \
    --output "$OUT/score-fusion-4panel-balanced.jsonl" \
    --judge-model gemini-3.1-pro-preview \
    --base-url "$BASE_URL" \
    --workers 1 \
    --resume --execute 2>&1

  # 检查score是否成功
  score_ok=$($PY -u -c "
import json, sys
tid = sys.argv[1]
path = 'out/fusion4_balanced/score-fusion-4panel-balanced.jsonl'
try:
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == tid and r.get('status') != 'failed':
                print('yes')
                sys.exit(0)
except FileNotFoundError:
    pass
print('no')
" "$tid")

  if [ "$score_ok" != "yes" ]; then
    echo "[score] FAILED - task not found or failed in score output" >&2
    continue
  fi
  echo "[score] Completed"

  # (3) 立即显示这道题的对比和running average
  echo "[compare] Task $tid comparison:"
  tid="$tid" $PY -u - <<'PYEOF'
import json, os, sys

tid = os.environ['tid']

# 读取各个版本的score
def get_score(path, task_id):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == task_id and r.get('status') != 'failed':
                return r.get('score')
    return None

balanced = get_score('out/fusion4_balanced/score-fusion-4panel-balanced.jsonl', tid)
mainlike = get_score('out/fusion4_mainlike/score-fusion-4panel-mainlike.jsonl', tid)
three_panel = get_score('out/task_major_v2/score-fusion-latest3panel.jsonl', tid)
baseline = get_score('out/task_major_v2/score-baseline.jsonl', tid)

print(f"  balanced:   {balanced:.1f if balanced else 'N/A'}", flush=True)
print(f"  mainlike:   {mainlike:.1f if mainlike else 'N/A'}", flush=True)
print(f"  3-panel:    {three_panel:.1f if three_panel else 'N/A'}", flush=True)
print(f"  baseline:   {baseline:.1f if baseline else 'N/A'}", flush=True)

if balanced and mainlike:
    diff = balanced - mainlike
    print(f"  → balanced vs mainlike: {diff:+.1f}", flush=True)
if balanced and three_panel:
    diff = balanced - three_panel
    print(f"  → balanced vs 3-panel:  {diff:+.1f}", flush=True)

# Running average
def load_all_scores(path):
    scores = {}
    if not os.path.exists(path):
        return scores
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('status') != 'failed':
                scores[r['task_id']] = r['score']
    return scores

balanced_all = load_all_scores('out/fusion4_balanced/score-fusion-4panel-balanced.jsonl')
mainlike_all = load_all_scores('out/fusion4_mainlike/score-fusion-4panel-mainlike.jsonl')
three_panel_all = load_all_scores('out/task_major_v2/score-fusion-latest3panel.jsonl')
baseline_all = load_all_scores('out/task_major_v2/score-baseline.jsonl')

common = set(balanced_all) & set(mainlike_all) & set(three_panel_all) & set(baseline_all)
if len(common) > 0:
    n = len(common)
    avg_bal = sum(balanced_all[t] for t in common) / n
    avg_main = sum(mainlike_all[t] for t in common) / n
    avg_3p = sum(three_panel_all[t] for t in common) / n
    avg_base = sum(baseline_all[t] for t in common) / n

    wins_main = sum(1 for t in common if balanced_all[t] > mainlike_all[t])
    wins_3p = sum(1 for t in common if balanced_all[t] > three_panel_all[t])
    wins_base = sum(1 for t in common if balanced_all[t] > baseline_all[t])

    print(f"\n[running avg] over {n} scored tasks:", flush=True)
    print(f"  balanced:   {avg_bal:.2f}  (beats mainlike {wins_main}/{n}, 3p {wins_3p}/{n}, base {wins_base}/{n})", flush=True)
    print(f"  mainlike:   {avg_main:.2f}", flush=True)
    print(f"  3-panel:    {avg_3p:.2f}", flush=True)
    print(f"  baseline:   {avg_base:.2f}", flush=True)
PYEOF

done

echo ""
echo "=========================================="
echo "All $total tasks processed."
echo "=========================================="
echo ""

# --- Final summary ---
$PY -u - <<'PYEOF'
import json, os

def load_all_scores(path):
    scores = {}
    if not os.path.exists(path):
        return scores
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('status') != 'failed':
                scores[r['task_id']] = r['score']
    return scores

balanced = load_all_scores('out/fusion4_balanced/score-fusion-4panel-balanced.jsonl')
mainlike = load_all_scores('out/fusion4_mainlike/score-fusion-4panel-mainlike.jsonl')
three_panel = load_all_scores('out/task_major_v2/score-fusion-latest3panel.jsonl')
baseline = load_all_scores('out/task_major_v2/score-baseline.jsonl')

common = set(balanced) & set(mainlike) & set(three_panel) & set(baseline)
if len(common) > 0:
    n = len(common)
    avg_bal = sum(balanced[t] for t in common) / n
    avg_main = sum(mainlike[t] for t in common) / n
    avg_3p = sum(three_panel[t] for t in common) / n
    avg_base = sum(baseline[t] for t in common) / n

    wins_main = sum(1 for t in common if balanced[t] > mainlike[t])
    wins_3p = sum(1 for t in common if balanced[t] > three_panel[t])
    wins_base = sum(1 for t in common if balanced[t] > baseline[t])

    print("=== FINAL RESULTS ===", flush=True)
    print(f"Tasks scored: {n}", flush=True)
    print(f"", flush=True)
    print(f"Average scores:", flush=True)
    print(f"  balanced:   {avg_bal:.2f}", flush=True)
    print(f"  mainlike:   {avg_main:.2f}  (Δ {avg_bal - avg_main:+.2f})", flush=True)
    print(f"  3-panel:    {avg_3p:.2f}  (Δ {avg_bal - avg_3p:+.2f})", flush=True)
    print(f"  baseline:   {avg_base:.2f}  (Δ {avg_bal - avg_base:+.2f})", flush=True)
    print(f"", flush=True)
    print(f"Win rates:", flush=True)
    print(f"  vs mainlike:  {wins_main}/{n} ({100*wins_main/n:.1f}%)", flush=True)
    print(f"  vs 3-panel:   {wins_3p}/{n} ({100*wins_3p/n:.1f}%)", flush=True)
    print(f"  vs baseline:  {wins_base}/{n} ({100*wins_base/n:.1f}%)", flush=True)
    print(f"", flush=True)

    # Top improvements and regressions
    diffs_main = [(t, balanced[t] - mainlike[t]) for t in common]
    diffs_main.sort(key=lambda x: x[1], reverse=True)

    print("Top 5 improvements vs mainlike:", flush=True)
    for i, (t, diff) in enumerate(diffs_main[:5], 1):
        print(f"  {i}. {t}: {diff:+.1f} (balanced {balanced[t]:.1f} vs mainlike {mainlike[t]:.1f})", flush=True)

    print(f"", flush=True)
    print("Top 5 regressions vs mainlike:", flush=True)
    for i, (t, diff) in enumerate(diffs_main[-5:], 1):
        print(f"  {i}. {t}: {diff:+.1f} (balanced {balanced[t]:.1f} vs mainlike {mainlike[t]:.1f})", flush=True)

    # Length analysis
    print(f"", flush=True)
    print("Output length distribution (balanced):", flush=True)
    with open('out/fusion4_balanced/fusion-4panel-balanced.jsonl') as f:
        lengths = []
        truncated = 0
        for line in f:
            r = json.loads(line)
            if 'final' in r:
                content = r['final']['content']
                lengths.append(len(content))
                if r['final'].get('finish_reason') == 'length':
                    truncated += 1

        if lengths:
            avg_len = sum(lengths) / len(lengths)
            print(f"  avg length: {avg_len:.0f} chars (~{avg_len/4:.0f} tokens)", flush=True)
            print(f"  min: {min(lengths)}, max: {max(lengths)}", flush=True)
            print(f"  truncated (finish=length): {truncated}/{len(lengths)}", flush=True)
else:
    print("No results to compare yet", flush=True)
PYEOF

echo ""
echo "Done."
