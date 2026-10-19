#!/bin/bash
# 修复第26题问题并重启评测

echo "=========================================="
echo "修复问题并重启剩余33题评测"
echo "=========================================="

# 1. 备份当前结果
echo ""
echo "[1/4] 备份当前结果..."
BACKUP_DIR="out/fusion4_balanced_full100_backup_before_fix_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp out/fusion4_balanced_full100/fusion-4panel-balanced.jsonl "$BACKUP_DIR/" 2>/dev/null && echo "  ✓ Fusion备份完成"
cp out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl "$BACKUP_DIR/" 2>/dev/null && echo "  ✓ Score备份完成"

# 2. 修复第26题 - 删除损坏的fusion记录
echo ""
echo "[2/4] 修复第26题损坏的fusion记录..."
python3 -c "
import json

bad_tid = 'b3c576e7-dfc6-403f-90e7-53c011884d5c'

# 过滤掉第26题
good_lines = []
with open('out/fusion4_balanced_full100/fusion-4panel-balanced.jsonl') as f:
    for line in f:
        r = json.loads(line)
        if r.get('task_id') != bad_tid:
            good_lines.append(line)

# 写回
with open('out/fusion4_balanced_full100/fusion-4panel-balanced.jsonl', 'w') as f:
    for line in good_lines:
        f.write(line)

print(f'  ✓ 已删除第26题的损坏fusion记录')
print(f'  ✓ 保留 {len(good_lines)} 条有效fusion记录')
"

# 3. 清理第26题的重复score记录
echo ""
echo "[3/4] 清理第26题的重复score记录..."
python3 -c "
import json

bad_tid = 'b3c576e7-dfc6-403f-90e7-53c011884d5c'

# 过滤掉第26题的None记录
good_lines = []
with open('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl') as f:
    for line in f:
        r = json.loads(line)
        # 保留非第26题的记录，或者第26题但score不是None的记录
        if r.get('task_id') != bad_tid or r.get('score') is not None:
            good_lines.append(line)

# 写回
with open('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl', 'w') as f:
    for line in good_lines:
        f.write(line)

print(f'  ✓ 已清理第26题的重复None记录')
print(f'  ✓ 保留 {len(good_lines)} 条有效score记录')
"

# 4. 显示修复后状态
echo ""
echo "[4/4] 修复后状态..."
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

print(f'  Fusion: {fusion_count}/100')
print(f'  Score:  {score_valid}/100')
print(f'  剩余:   {100 - score_valid}题')
"

echo ""
echo "=========================================="
echo "✓ 修复完成！"
echo "=========================================="
echo ""
echo "现在可以运行："
echo "  nohup bash scripts/run_4panel_balanced_full100.sh > out/fusion4_balanced_full100_run_\$(date +%Y%m%d_%H%M%S).log 2>&1 &"
echo ""
