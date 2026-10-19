# 运行全100道题的4-Panel Balanced评测

## 脚本特性

**`scripts/run_4panel_balanced_full100.sh`** - 支持全100道DRACO题目的逐题执行

### 核心功能

1. ✅ **复用3面板结果**：minimax-m3、glm-5.3-flash、deepseek-v4-flash
2. ✅ **按需生成mimo panel**：只在需要时为当前题目生成mimo-v2.5-pro答案
3. ✅ **逐题执行流程**：
   - 检查mimo panel → 如缺失则生成
   - 运行judge + fusion
   - 立即打分
   - 显示该题分数对比
   - 显示running average
   - 继续下一题
4. ✅ **实时日志**：所有输出立即flush，可实时监控
5. ✅ **Resume能力**：中断后重新运行会自动跳过已完成的题目

---

## 使用方法

### 1. 前置要求

确保已经完成3面板评测：
```bash
# 检查3面板文件是否存在
ls -lh out/task_major_v2/panel-*.jsonl
ls -lh out/fusion4_mainlike/judge_cache.jsonl
```

应该看到：
- `panel-minimax-m3.jsonl`
- `panel-glm-5.3-flash.jsonl`
- `panel-deepseek-v4-flash.jsonl`
- `judge_cache.jsonl`

### 2. 运行脚本

```bash
cd /workspace/git_exp/TrustedRouter-Fusion-Draco

# 设置环境变量
export TR_API_KEY=你的key
export EXA_API_KEY=你的key

# 后台运行
bash scripts/run_4panel_balanced_full100.sh \
  > out/fusion4_balanced_full100_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 实时监控进度
tail -f out/fusion4_balanced_full100_run_*.log
```

### 3. 监控进度

#### 查看当前完成数量
```bash
wc -l out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl
```

#### 查看running average
```bash
grep "Running average" out/fusion4_balanced_full100_run_*.log | tail -1
```

#### 查看最新完成的题目
```bash
tail -30 out/fusion4_balanced_full100_run_*.log
```

---

## 日志格式

### 每道题的输出格式

```
===== [42/100] a1b2c3d4-e5f6-7890-abcd-ef1234567890 =====

[mimo] ✓ Already exists
[fusion] ✓ Completed
[score] ✓ Completed

======================================================================
Task a1b2c3d4 scores:
  Balanced:   78.45
  3-Panel:    75.32
  Baseline:   71.20
  → vs 3-Panel:  +3.13
  → vs Baseline: +7.25
----------------------------------------------------------------------
Running average (42 tasks completed):
  Balanced:   76.84  (wins: 25/42 vs 3P, 31/42 vs Base)
  3-Panel:    75.51  (Δ +1.33)
  Baseline:   71.88  (Δ +4.96)
======================================================================
```

---

## 成本估算

### 每道题成本（已有3面板结果）

| 项目 | 调用次数 | 单价 | 成本 |
|------|---------|------|------|
| **Mimo Panel生成** | 1次 | ~$0.02 | $0.02 |
| **Judge分析** | 0（复用cache） | $0 | $0 |
| **Fusion (kimi-k3)** | 1次 | ~$0.03 | $0.03 |
| **Scoring (gemini-3.1-pro)** | 3 passes | ~$0.02 | $0.02 |
| **单题总成本** | | | **~$0.07** |

### 全100道题总成本

- **已完成31题**：已消耗成本（之前的run）
- **剩余69题**：69 × $0.07 = **~$4.83**
- **全100题总成本**：~$7

---

## 输出文件

### 主要输出

```
out/fusion4_balanced_full100/
├── fusion-4panel-balanced.jsonl        # Fusion结果（100行）
└── score-fusion-4panel-balanced.jsonl  # 评分结果（100行）

out/mimo_panels_full100/
├── panel-mimo-v2.5-pro-<task-id>.jsonl # 每题的mimo panel（100个文件）
└── panel-mimo-v2.5-pro.jsonl           # 合并的mimo panel（如果脚本合并的话）
```

### 日志文件

```
out/fusion4_balanced_full100_run_<timestamp>.log
```

---

## 中断和恢复

### 优雅中断

```bash
# 查找进程
ps aux | grep run_4panel_balanced_full100

# 发送SIGTERM（等当前题目完成）
kill <pid>

# 或强制中断
kill -9 <pid>
```

### 恢复运行

直接重新运行脚本即可，**已完成的题目会自动跳过**：

```bash
bash scripts/run_4panel_balanced_full100.sh \
  >> out/fusion4_balanced_full100_run_existing.log 2>&1 &
```

脚本会：
- ✅ 检查每道题是否已评分
- ✅ 跳过已完成的题目
- ✅ 从第一个未完成的题目继续

---

## 预期运行时间

### 单题时间估算

- Mimo panel生成：~30秒（如需生成）
- Fusion (kimi-k3)：~30秒
- Scoring (3 passes)：~60秒
- **单题总时间**：~2分钟

### 全100题时间

- **已完成**：31题
- **剩余**：69题
- **预计时间**：69 × 2分钟 = **~138分钟（2.3小时）**

实际可能更快（并发、缓存命中等）或更慢（API限流、网络波动等）。

---

## 故障排查

### 问题1：Mimo panel生成失败

```
[mimo] FAILED - skipping this task
```

**原因**：mimo-v2.5-pro API调用失败或超时。

**解决**：
- 检查TR_API_KEY是否有效
- 检查mimo-v2.5-pro是否可用（可能被限流）
- 重新运行脚本会自动重试该题

### 问题2：Fusion失败

```
[fusion] ✗ FAILED - task not found in output
```

**原因**：四个面板答案不全（require-full-panel模式）或kimi-k3调用失败。

**解决**：
- 检查mimo panel是否成功生成
- 检查kimi-k3是否可用
- 重新运行会重试

### 问题3：Scoring失败

```
[score] ✗ FAILED - task not found or failed in score output
```

**原因**：gemini-3.1-pro调用失败。

**解决**：
- 检查gemini-3.1-pro是否可用
- 等待一段时间后重试（可能是限流）

### 问题4：脚本卡住不动

**诊断**：
```bash
# 查看进程状态
ps aux | grep run_4panel_balanced_full100

# 查看日志最后更新时间
stat out/fusion4_balanced_full100_run_*.log | grep Modify

# 查看完整日志
tail -100 out/fusion4_balanced_full100_run_*.log
```

**解决**：
- 如果进程存在但日志长时间无更新 → kill进程重新运行
- 如果是API超时 → 重新运行会从断点继续

---

## 完成后的对比分析

运行完成后，可以对比3个版本的结果：

```bash
# 查看最终summary
tail -50 out/fusion4_balanced_full100_run_*.log

# 或单独分析
python3 - <<'EOF'
import json

def load_scores(path):
    scores = {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('status') != 'failed' and 'score' in r:
                scores[r['task_id']] = r['score']
    return scores

balanced = load_scores('out/fusion4_balanced_full100/score-fusion-4panel-balanced.jsonl')
panel3 = load_scores('out/task_major_v2/score-fusion-latest3panel.jsonl')
baseline = load_scores('out/task_major_v2/score-baseline.jsonl')

common = set(balanced) & set(panel3) & set(baseline)
n = len(common)

avg_bal = sum(balanced[t] for t in common) / n
avg_3p = sum(panel3[t] for t in common) / n
avg_base = sum(baseline[t] for t in common) / n

print(f"Tasks scored: {n}/100")
print(f"Balanced:   {avg_bal:.2f}")
print(f"3-Panel:    {avg_3p:.2f}  (Δ {avg_bal - avg_3p:+.2f})")
print(f"Baseline:   {avg_base:.2f}  (Δ {avg_bal - avg_base:+.2f})")
EOF
```

---

## 与之前31题版本的区别

| 维度 | 31题版本 | 100题版本 |
|------|---------|----------|
| **题目范围** | 31题（有完整4面板答案的） | 全100题 |
| **Mimo panel** | 复用已有 | 按需生成 |
| **输出目录** | `out/fusion4_balanced/` | `out/fusion4_balanced_full100/` |
| **Resume能力** | ✓ | ✓ |
| **实时日志** | ✓ | ✓ |
| **预计成本** | ~$1.55 | ~$7 |
| **预计时间** | ~47分钟 | ~2.3小时 |

---

## 下一步

1. **监控进度**：定期查看日志和running average
2. **等待完成**：预计2-3小时
3. **分析结果**：对比balanced vs 3-panel vs baseline的表现
4. **决定是否作为默认配置**：如果balanced持续优于3-panel

**Good luck! 🚀**
