#!/usr/bin/env bash
# Re-run ONLY the failed parts of the 3 unscored tasks in out/fusion4_balanced_full100,
# reusing every panel / judge result that already exists.
#
#   ce522bc2  mimo-v2.5-pro panel failed (HTTP 502, status=failed) -> the full-100 runner
#             saw a row and skipped generation, then --require-full-panel kept the task
#             out of fusion. Re-run: mimo panel -> judge (not cached) -> fuser -> score.
#   ce335c0c  all 4 panels + judge cache OK; kimi-k3 fuser errored 5/5 attempts and an
#             empty "ok" row was written (never retried by --resume, scored null 33x).
#             Re-run: fuser -> score.
#   e6fbba71  all 4 panels have finish=length / empty content (same rows the 3-panel run
#             used, so they are reused as-is for comparability); judge cached; kimi-k3
#             fuser spent all 11503 tokens without visible output. Re-run: fuser -> score.
#
# Old fusion/score rows for these tasks are moved out (files backed up first).
#
# Usage:
#   export TR_API_KEY=... EXA_API_KEY=...
#   bash scripts/rerun_4panel_missing3.sh > out/fusion4_rerun_missing3_$(date +%Y%m%d_%H%M%S).log 2>&1 &

set -u
cd "$(dirname "$0")/.." || exit 1

MANIFEST=data/draco-full-100.manifest.json
V2=out/task_major_v2
MAINLIKE=out/fusion4_mainlike
OUT=out/fusion4_balanced_full100
MIMO=out/mimo_panels_full100/panel-mimo-v2.5-pro.jsonl
FUSION=$OUT/fusion-4panel-balanced.jsonl
SCORE=$OUT/score-fusion-4panel-balanced.jsonl
BASE_URL=https://router-api.0g.ai/v1
PY=${PY:-python3}
TASKS="ce522bc2-e8a2-436b-971f-dd4972f23d39 ce335c0c-f136-4408-a216-6a891cae861f e6fbba71-d917-4d1e-bd09-009ff9cd8e9b"
export PYTHONPATH="$(pwd)/src:${PYTHONPATH:-}"

[ -n "${TR_API_KEY:-}" ] || { echo "FATAL: TR_API_KEY is not set." >&2; exit 2; }
[ -n "${EXA_API_KEY:-}" ] || { echo "FATAL: EXA_API_KEY is not set." >&2; exit 2; }
command -v "$PY" >/dev/null || { echo "FATAL: $PY not found." >&2; exit 2; }

# --- 1. Back up, then drop the bad fusion/score rows for these tasks ---
ts=$(date +%Y%m%d_%H%M%S)
cp "$FUSION" "$FUSION.bak_rerun3_$ts"
cp "$SCORE" "$SCORE.bak_rerun3_$ts"
TASKS="$TASKS" $PY - "$FUSION" "$SCORE" <<'PYEOF'
import json, os, sys
tasks = set(os.environ["TASKS"].split())
for path in sys.argv[1:]:
    keep, dropped = [], 0
    for line in open(path, encoding="utf-8"):
        if line.strip() and json.loads(line).get("task_id") in tasks:
            dropped += 1
            continue
        keep.append(line)
    open(path, "w", encoding="utf-8").writelines(keep)
    print(f"{path}: dropped {dropped} rows")
PYEOF

# --- 2. mimo panel for ce522bc2 (failed row is not "completed", so --resume retries it) ---
echo "[mimo] ce522bc2 ..."
$PY -u scripts/draco_agentic_solo.py \
  --manifest "$MANIFEST" \
  --model mimo-v2.5-pro \
  --output "$MIMO" \
  --config-id panel-mimo-v2.5-pro \
  --base-url "$BASE_URL" \
  --api-key-name TR_API_KEY \
  --task-id ce522bc2-e8a2-436b-971f-dd4972f23d39 \
  --resume --execute

# --- 3. Fusion: --resume makes exactly the tasks without a row pending ---
echo "[fusion] ..."
$PY -u scripts/draco_client_fusion.py \
  --manifest "$MANIFEST" \
  --panel minimax-m3="$V2/panel-minimax-m3.jsonl" \
  --panel glm-5.3-flash="$V2/panel-glm-5.3-flash.jsonl" \
  --panel deepseek-v4-flash="$V2/panel-deepseek-v4-flash.jsonl" \
  --panel mimo-v2.5-pro="$MIMO" \
  --output "$FUSION" \
  --config-id fusion-4panel-balanced \
  --judge-model minimax-m3 \
  --judge-cache "$MAINLIKE/judge_cache.jsonl" \
  --fuser-model kimi-k3 \
  --base-url "$BASE_URL" \
  --api-key-name TR_API_KEY \
  --workers 3 \
  --require-full-panel \
  --resume --execute

# --- 4. Empty fusion output must not reach the scorer (it would retry forever) ---
TASKS="$TASKS" $PY - "$FUSION" <<'PYEOF'
import json, os, sys
tasks = set(os.environ["TASKS"].split())
path = sys.argv[1]
rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
found = {r["task_id"]: r for r in rows if r.get("task_id") in tasks}
bad = []
for t in sorted(tasks):
    r = found.get(t)
    final = (r or {}).get("final") or {}
    content = final.get("content") or ""
    finish = final.get("finish_reason")
    print(f"{t[:8]}: " + ("MISSING" if r is None else f"chars={len(content)} finish={finish}"))
    if r is not None and not content.strip():
        bad.append(t)
if bad:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            if r.get("task_id") not in bad:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
    print(f"removed empty fusion rows (re-run this script to retry): {[b[:8] for b in bad]}")
PYEOF

# --- 5. Score (only rows not yet scored) ---
echo "[score] ..."
$PY -u scripts/draco_rejudge.py \
  "$FUSION" \
  --output "$SCORE" \
  --judge-model gemini-3.1-pro-preview \
  --base-url "$BASE_URL" \
  --workers 3 \
  --resume --execute

# --- 6. Summary vs baseline / 3-panel ---
$PY - <<'PYEOF'
import json
def load(p):
    s = {}
    for l in open(p, encoding="utf-8"):
        if l.strip():
            r = json.loads(l)
            if r.get("score") is not None:
                s[r["task_id"]] = r["score"]
    return s
four = load("out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl")
three = load("out/task_major_v2/score-fusion-latest3panel.jsonl")
base = load("out/task_major_v2/score-baseline.jsonl")
for name, other in (("baseline", base), ("3-panel", three)):
    c = [t for t in four if t in other]
    a = sum(four[t] for t in c) / len(c); b = sum(other[t] for t in c) / len(c)
    w = sum(four[t] > other[t] for t in c)
    print(f"4p vs {name}: n={len(c)} 4p={a:.2f} {name}={b:.2f} Δ={a-b:+.2f} wins={w}/{len(c)}")
PYEOF
