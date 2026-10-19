#!/usr/bin/env bash
# 4-panel fusion for full 100 tasks with IMPORTANCE-PRIORITIZED prompt
#
# 核心特点：
# 1. 逐题执行：检查mimo → fusion → 打分 → 立即显示对比 → 下一题
# 2. 复用3面板panel结果，只在需要时生成mimo panel
# 3. 实时日志flush（python -u + flush=True）
# 4. 每题完成后立即显示该题分数和running average
#
# 输出目录: out/fusion4_balanced_full100/
#
# Usage:
#   export TR_API_KEY=... EXA_API_KEY=...
#   bash scripts/run_4panel_balanced_full100.sh \
#     > out/fusion4_balanced_full100_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &
#   tail -f out/fusion4_balanced_full100_run_*.log

cd "$(dirname "$0")/.." || exit 1

MANIFEST=data/draco-full-100.manifest.json
V2=out/task_major_v2
MAINLIKE=out/fusion4_mainlike
OUT=out/fusion4_balanced_full100
MIMO_PANEL_DIR=out/mimo_panels_full100
PY="python3"
BASE_URL=https://router-api.0g.ai/v1

# 设置PYTHONPATH
export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

mkdir -p "$OUT"
mkdir -p "$MIMO_PANEL_DIR"

# --- Preflight ---
if [ -z "$TR_API_KEY" ]; then
  echo "FATAL: TR_API_KEY is not set." >&2; exit 2
fi
if [ -z "$EXA_API_KEY" ]; then
  echo "FATAL: EXA_API_KEY is not set." >&2; exit 2
fi

echo "=========================================="
echo "Preflight check..."
echo "=========================================="
preflight_status=$(curl -sS --max-time 30 -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $TR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":1,"stream":false}')
if [ "$preflight_status" != "200" ]; then
  echo "FATAL: preflight auth check returned HTTP $preflight_status (expected 200). Aborting." >&2
  exit 2
fi
echo "✓ Gateway auth verified (HTTP 200)"
echo ""

# --- 检查必需的3-panel输入文件 ---
if [ ! -f "$V2/panel-minimax-m3.jsonl" ]; then
  echo "FATAL: $V2/panel-minimax-m3.jsonl not found. Run 3-panel first." >&2
  exit 2
fi
if [ ! -f "$V2/panel-glm-5.3-flash.jsonl" ]; then
  echo "FATAL: $V2/panel-glm-5.3-flash.jsonl not found." >&2
  exit 2
fi
if [ ! -f "$V2/panel-deepseek-v4-flash.jsonl" ]; then
  echo "FATAL: $V2/panel-deepseek-v4-flash.jsonl not found." >&2
  exit 2
fi
if [ ! -f "$MAINLIKE/judge_cache.jsonl" ]; then
  echo "FATAL: $MAINLIKE/judge_cache.jsonl not found. Need judge cache." >&2
  exit 2
fi

echo "Using existing 3-panel results:"
echo "  - minimax-m3:       $V2/panel-minimax-m3.jsonl"
echo "  - glm-5.3-flash:    $V2/panel-glm-5.3-flash.jsonl"
echo "  - deepseek-v4-flash: $V2/panel-deepseek-v4-flash.jsonl"
echo "  - judge cache:      $MAINLIKE/judge_cache.jsonl"
echo "  - mimo panels:      $MIMO_PANEL_DIR/ (按需生成)"
echo ""

# --- 读取全部100个task IDs ---
all_task_ids=$($PY -u -c "
import json, sys
d = json.load(open('$MANIFEST'))
tasks = d if isinstance(d, list) else d.get('tasks', d)
for t in tasks:
    print(t['id'], flush=True)
")

total=$(echo "$all_task_ids" | wc -l)
i=0

echo "=========================================="
echo "Starting task-by-task execution"
echo "Total tasks: $total"
echo "=========================================="
echo ""

for tid in $all_task_ids; do
  i=$((i+1))
  echo ""
  echo "===== [$i/$total] $tid ====="
  echo ""

  # --- 检查是否已完成评分 ---
  already_scored=$($PY -u -c "
import json, sys
tid = sys.argv[1]
path = '$OUT/score-fusion-4panel-balanced.jsonl'
try:
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == tid and r.get('status') != 'failed':
                print('yes', flush=True)
                sys.exit(0)
except FileNotFoundError:
    pass
print('no', flush=True)
" "$tid")

  if [ "$already_scored" = "yes" ]; then
    echo "[skip] Task $tid already scored"

    # 显示已有结果
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

balanced = get_score('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl', tid)
three_panel = get_score('out/task_major_v2/score-fusion-latest3panel.jsonl', tid)
baseline = get_score('out/task_major_v2/score-baseline.jsonl', tid)

# 安全的格式化函数
def safe_format(val):
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return "N/A"

print(f"  Balanced:   {safe_format(balanced)}", flush=True)
print(f"  3-Panel:    {safe_format(three_panel)}", flush=True)
print(f"  Baseline:   {safe_format(baseline)}", flush=True)
PYEOF
    continue
  fi

  # ===== STEP 1: 检查并生成mimo panel =====
  mimo_exists=$($PY -u -c "
import json, sys, os
tid = sys.argv[1]
path = '$MIMO_PANEL_DIR/panel-mimo-v2.5-pro.jsonl'
if os.path.exists(path):
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == tid:
                print('yes', flush=True)
                sys.exit(0)
print('no', flush=True)
" "$tid")

  if [ "$mimo_exists" != "yes" ]; then
    echo "[mimo] Generating mimo panel for task $tid..."

    $PY -u scripts/draco_agentic_solo.py \
      --manifest "$MANIFEST" \
      --model mimo-v2.5-pro \
      --output "$MIMO_PANEL_DIR/panel-mimo-v2.5-pro.jsonl" \
      --config-id "panel-mimo-v2.5-pro" \
      --base-url "$BASE_URL" \
      --api-key-name TR_API_KEY \
      --task-id "$tid" \
      --resume --execute 2>&1 | grep -E "(task|completed|ERROR|FATAL)" || true

    # 检查mimo panel是否成功生成
    mimo_ok=$($PY -u -c "
import json, sys, os
tid = sys.argv[1]
path = '$MIMO_PANEL_DIR/panel-mimo-v2.5-pro.jsonl'
if not os.path.exists(path):
    print('no', flush=True)
    sys.exit(0)
with open(path) as f:
    for line in f:
        r = json.loads(line)
        if r.get('task_id') == tid:
            print('yes', flush=True)
            sys.exit(0)
print('no', flush=True)
" "$tid")

    if [ "$mimo_ok" != "yes" ]; then
      echo "[mimo] FAILED - skipping this task" >&2
      continue
    fi
    echo "[mimo] ✓ Completed"
  else
    echo "[mimo] ✓ Already exists"
  fi

  # ===== STEP 2: Fusion =====
  echo "[fusion] Starting fusion..."

  $PY -u scripts/draco_client_fusion.py \
    --manifest "$MANIFEST" \
    --panel minimax-m3="$V2/panel-minimax-m3.jsonl" \
    --panel glm-5.3-flash="$V2/panel-glm-5.3-flash.jsonl" \
    --panel deepseek-v4-flash="$V2/panel-deepseek-v4-flash.jsonl" \
    --panel mimo-v2.5-pro="$MIMO_PANEL_DIR/panel-mimo-v2.5-pro.jsonl" \
    --output "$OUT/fusion-4panel-balanced.jsonl" \
    --config-id fusion-4panel-balanced \
    --judge-model minimax-m3 \
    --judge-cache "$MAINLIKE/judge_cache.jsonl" \
    --fuser-model kimi-k3 \
    --base-url "$BASE_URL" \
    --api-key-name TR_API_KEY \
    --workers 1 \
    --require-full-panel \
    --resume --execute 2>&1 | grep -E "(fusion [0-9]+/|fused task|ERROR|FATAL)" | head -5 || true

  fusion_ok=$($PY -u -c "
import json, sys
tid = sys.argv[1]
path = '$OUT/fusion-4panel-balanced.jsonl'
try:
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == tid:
                print('yes', flush=True)
                sys.exit(0)
except FileNotFoundError:
    pass
print('no', flush=True)
" "$tid")

  if [ "$fusion_ok" != "yes" ]; then
    echo "[fusion] ✗ FAILED - task not found in output" >&2
    continue
  fi
  echo "[fusion] ✓ Completed"

  # ===== STEP 3: Scoring =====
  echo "[score] Starting scoring..."

  $PY -u scripts/draco_rejudge.py \
    "$OUT/fusion-4panel-balanced.jsonl" \
    --output "$OUT/score-fusion-4panel-balanced.jsonl" \
    --judge-model gemini-3.1-pro-preview \
    --base-url "$BASE_URL" \
    --workers 1 \
    --resume --execute 2>&1 | grep -E "(rejudged [0-9]+/|ERROR|FATAL)" | head -5 || true

  score_ok=$($PY -u -c "
import json, sys
tid = sys.argv[1]
path = '$OUT/score-fusion-4panel-balanced.jsonl'
try:
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('task_id') == tid and r.get('status') != 'failed':
                print('yes', flush=True)
                sys.exit(0)
except FileNotFoundError:
    pass
print('no', flush=True)
" "$tid")

  if [ "$score_ok" != "yes" ]; then
    echo "[score] ✗ FAILED - task not found or failed in score output" >&2
    continue
  fi
  echo "[score] ✓ Completed"
  echo ""

  # ===== STEP 4: 立即显示该题对比和running average =====
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

def load_all_scores(path):
    scores = {}
    if not os.path.exists(path):
        return scores
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('status') != 'failed' and 'score' in r:
                scores[r['task_id']] = r['score']
    return scores

# 当前题目分数
balanced = get_score('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl', tid)
three_panel = get_score('out/task_major_v2/score-fusion-latest3panel.jsonl', tid)
baseline = get_score('out/task_major_v2/score-baseline.jsonl', tid)

print("=" * 70, flush=True)
print(f"Task {tid[:8]} scores:", flush=True)

# 安全的格式化函数
def safe_format(val):
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return "N/A"

print(f"  Balanced:   {safe_format(balanced)}", flush=True)
print(f"  3-Panel:    {safe_format(three_panel)}", flush=True)
print(f"  Baseline:   {safe_format(baseline)}", flush=True)

if balanced is not None and three_panel is not None:
    try:
        diff = float(balanced) - float(three_panel)
        print(f"  → vs 3-Panel:  {diff:+.2f}", flush=True)
    except (ValueError, TypeError):
        pass
if balanced is not None and baseline is not None:
    try:
        diff = float(balanced) - float(baseline)
        print(f"  → vs Baseline: {diff:+.2f}", flush=True)
    except (ValueError, TypeError):
        pass

# Running average
balanced_all = load_all_scores('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl')
three_panel_all = load_all_scores('out/task_major_v2/score-fusion-latest3panel.jsonl')
baseline_all = load_all_scores('out/task_major_v2/score-baseline.jsonl')

common = set(balanced_all) & set(three_panel_all) & set(baseline_all)
if len(common) > 0:
    n = len(common)
    avg_bal = sum(balanced_all[t] for t in common) / n
    avg_3p = sum(three_panel_all[t] for t in common) / n
    avg_base = sum(baseline_all[t] for t in common) / n

    wins_3p = sum(1 for t in common if balanced_all[t] > three_panel_all[t])
    wins_base = sum(1 for t in common if balanced_all[t] > baseline_all[t])

    print("-" * 70, flush=True)
    print(f"Running average ({n} tasks completed):", flush=True)
    print(f"  Balanced:   {avg_bal:.2f}  (wins: {wins_3p}/{n} vs 3P, {wins_base}/{n} vs Base)", flush=True)
    print(f"  3-Panel:    {avg_3p:.2f}  (Δ {avg_bal - avg_3p:+.2f})", flush=True)
    print(f"  Baseline:   {avg_base:.2f}  (Δ {avg_bal - avg_base:+.2f})", flush=True)
print("=" * 70, flush=True)
print("", flush=True)
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
            if r.get('status') != 'failed' and 'score' in r:
                scores[r['task_id']] = r['score']
    return scores

balanced = load_all_scores('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl')
three_panel = load_all_scores('out/task_major_v2/score-fusion-latest3panel.jsonl')
baseline = load_all_scores('out/task_major_v2/score-baseline.jsonl')

common = set(balanced) & set(three_panel) & set(baseline)
if len(common) > 0:
    n = len(common)
    avg_bal = sum(balanced[t] for t in common) / n
    avg_3p = sum(three_panel[t] for t in common) / n
    avg_base = sum(baseline[t] for t in common) / n

    wins_3p = sum(1 for t in common if balanced[t] > three_panel[t])
    wins_base = sum(1 for t in common if balanced[t] > baseline[t])

    print("=" * 70, flush=True)
    print("FINAL RESULTS", flush=True)
    print("=" * 70, flush=True)
    print(f"Tasks scored: {n}/100", flush=True)
    print("", flush=True)
    print(f"Average scores:", flush=True)
    print(f"  Balanced:   {avg_bal:.2f}", flush=True)
    print(f"  3-Panel:    {avg_3p:.2f}  (Δ {avg_bal - avg_3p:+.2f})", flush=True)
    print(f"  Baseline:   {avg_base:.2f}  (Δ {avg_bal - avg_base:+.2f})", flush=True)
    print("", flush=True)
    print(f"Win rates:", flush=True)
    print(f"  vs 3-Panel:   {wins_3p}/{n} ({100*wins_3p/n:.1f}%)", flush=True)
    print(f"  vs Baseline:  {wins_base}/{n} ({100*wins_base/n:.1f}%)", flush=True)
    print("", flush=True)

    # Top improvements
    diffs_3p = [(t, balanced[t] - three_panel[t]) for t in common]
    diffs_3p.sort(key=lambda x: x[1], reverse=True)

    print("Top 5 improvements vs 3-Panel:", flush=True)
    for i, (t, diff) in enumerate(diffs_3p[:5], 1):
        print(f"  {i}. {t[:8]}: {diff:+.2f} (Balanced {balanced[t]:.1f} vs 3-Panel {three_panel[t]:.1f})", flush=True)

    print("", flush=True)
    print("Top 5 regressions vs 3-Panel:", flush=True)
    for i, (t, diff) in enumerate(diffs_3p[-5:], 1):
        print(f"  {i}. {t[:8]}: {diff:+.2f} (Balanced {balanced[t]:.1f} vs 3-Panel {three_panel[t]:.1f})", flush=True)
else:
    print("No results to compare yet", flush=True)

print("", flush=True)
print("Done.", flush=True)
PYEOF
