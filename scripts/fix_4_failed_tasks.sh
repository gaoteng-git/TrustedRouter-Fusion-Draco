#!/bin/bash
# 专门修复4题失败任务的脚本（安全版本）

echo "========================================"
echo "修复4题失败任务（安全模式）"
echo "========================================"

# 失败的4题
FAILED_TASKS=(
  "ce522bc2-e8a2-436b-971f-dd4972f23d39"  # 第12题 - 未跑fusion
  "b3c576e7-dfc6-403f-90e7-53c011884d5c"  # 第26题 - fusion损坏
  "ce335c0c-f136-4408-a216-6a891cae861f"  # score=None
  "e6fbba71-d917-4d1e-bd09-009ff9cd8e9b"  # score=None
)

# 1. 自动备份
echo ""
echo "[1/3] 自动备份..."
BACKUP_DIR="out/fusion4_balanced_full100_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp out/fusion4_balanced_full100/fusion-4panel-balanced.jsonl "$BACKUP_DIR/" 2>/dev/null && echo "  ✓ Fusion已备份"
cp out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl "$BACKUP_DIR/" 2>/dev/null && echo "  ✓ Score已备份"
echo "  备份位置: $BACKUP_DIR"

# 2. 清理这4题的所有记录（为重跑做准备）
echo ""
echo "[2/3] 清理4题的旧记录..."
python3 -c "
import json

failed_tasks = [
    'ce522bc2-e8a2-436b-971f-dd4972f23d39',
    'b3c576e7-dfc6-403f-90e7-53c011884d5c',
    'ce335c0c-f136-4408-a216-6a891cae861f',
    'e6fbba71-d917-4d1e-bd09-009ff9cd8e9b'
]

# 清理fusion
good_lines = []
removed = 0
with open('out/fusion4_balanced_full100/fusion-4panel-balanced.jsonl') as f:
    for line in f:
        r = json.loads(line)
        if r.get('task_id') not in failed_tasks:
            good_lines.append(line)
        else:
            removed += 1

with open('out/fusion4_balanced_full100/fusion-4panel-balanced.jsonl', 'w') as f:
    for line in good_lines:
        f.write(line)

print(f'  ✓ Fusion: 保留{len(good_lines)}条，删除{removed}条')

# 清理score
good_lines = []
removed = 0
with open('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl') as f:
    for line in f:
        r = json.loads(line)
        if r.get('task_id') not in failed_tasks:
            good_lines.append(line)
        else:
            removed += 1

with open('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl', 'w') as f:
    for line in good_lines:
        f.write(line)

print(f'  ✓ Score: 保留{len(good_lines)}条，删除{removed}条')
"

# 3. 显示修复后状态
echo ""
echo "[3/3] 修复后状态..."
python3 -c "
import json

fusion_count = 0
with open('out/fusion4_balanced_full100/fusion-4panel-balanced.jsonl') as f:
    for line in f:
        r = json.loads(line)
        if r.get('config_id') == 'fusion-4panel-balanced':
            fusion_count += 1

score_valid = 0
with open('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl') as f:
    for line in f:
        r = json.loads(line)
        if r.get('config_id') == 'fusion-4panel-balanced' and r.get('score') is not None:
            score_valid += 1

print(f'  ✓ Fusion: {fusion_count}题')
print(f'  ✓ 有效Score: {score_valid}题')
print(f'  → 4题已清理，可以重跑')
"

echo ""
echo "========================================"
echo "✓ 修复完成！96题数据完全保留"
echo "========================================"
echo ""
echo "现在可以运行："
echo "  nohup bash scripts/run_4panel_balanced_full100.sh > out/fusion4_balanced_full100_run_\$(date +%Y%m%d_%H%M%S).log 2>&1 &"
echo ""
echo "或者选择接受96题的结果，不重跑这4题"
echo ""
