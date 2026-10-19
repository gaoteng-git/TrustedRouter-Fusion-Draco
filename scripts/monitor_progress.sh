#!/bin/bash
# 监控评测进度和日志活跃状态

LOG_FILE="out/fusion4_balanced_full100_run_20261017_153822.log"
SCORE_FILE="out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl"

while true; do
    clear
    echo "========================================"
    echo "  4-Panel Balanced 评测进度监控"
    echo "========================================"
    echo ""

    # 检查日志文件活跃度
    python3 - <<'EOF'
import os, time, json
from datetime import datetime

log_file = "out/fusion4_balanced_full100_run_20261017_153822.log"
score_file = "out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl"

# 日志活跃度
if os.path.exists(log_file):
    mtime = os.path.getmtime(log_file)
    now = time.time()
    age_minutes = (now - mtime) / 60
    mtime_str = datetime.fromtimestamp(mtime).strftime('%H:%M:%S')

    if age_minutes < 10:
        status = "🟢 正常运行"
    elif age_minutes < 20:
        status = "🟡 可能慢速"
    else:
        status = "🔴 可能卡住"

    print(f"日志状态: {status}")
    print(f"  最后更新: {mtime_str} (距今 {age_minutes:.1f}分钟)")
else:
    print("日志状态: ❌ 文件不存在")

print()

# 完成进度
if os.path.exists(score_file):
    valid_scores = set()
    with open(score_file) as f:
        for line in f:
            r = json.loads(line)
            if r.get('config_id') == 'fusion-4panel-balanced' and r.get('score') is not None:
                valid_scores.add(r['task_id'])

    count = len(valid_scores)
    pct = count
    remaining = 100 - count

    # 简单进度条
    bar_length = 50
    filled = int(bar_length * count / 100)
    bar = '█' * filled + '░' * (bar_length - filled)

    print(f"完成进度: {count}/100题 ({pct}%)")
    print(f"  [{bar}]")
    print(f"  剩余: {remaining}题")

    # 预计完成时间（假设每题12分钟）
    if remaining > 0:
        est_minutes = remaining * 12
        est_hours = est_minutes / 60
        print(f"  预计剩余: ~{est_hours:.1f}小时")
else:
    print("完成进度: ❌ Score文件不存在")

EOF

    echo ""
    echo "========================================"
    echo "  按 Ctrl+C 退出监控"
    echo "  刷新间隔: 60秒"
    echo "========================================"

    # 等待60秒
    sleep 60
done
