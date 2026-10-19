# Task-by-Task实时执行方案

## 核心特性

1. **逐题执行**：每道题 fusion → 立即打分 → 立即显示结果
2. **实时日志**：python -u 无缓冲输出，实时看到进度
3. **零额外成本**：复用所有panel结果 + judge cache
4. **可中断恢复**：支持Ctrl+C中断，重新运行会跳过已完成的题目

## 运行方式

```bash
cd /workspace/git_exp/TrustedRouter-Fusion-Draco

export TR_API_KEY=your_key_here
export EXA_API_KEY=your_key_here

# 直接运行（前台，实时看到输出）
bash scripts/run_4panel_balanced_taskwise.sh

# 或后台运行（推荐）
bash scripts/run_4panel_balanced_taskwise.sh \
  > out/fusion4_balanced_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 实时查看进度
tail -f out/fusion4_balanced_run_*.log
```

## 输出格式

每道题的输出格式：
```
===== [1/31] 0c2c668a-c3bf-41af-93c9-b5614ff63508 =====
[fusion] Starting fusion for task 0c2c668a...
[fusion] Completed
[score] Starting scoring for task 0c2c668a...
[score] Completed
[compare] Task 0c2c668a comparison:
  balanced:   75.3
  mainlike:   73.2
  3-panel:    75.1
  baseline:   71.4
  → balanced vs mainlike: +2.1
  → balanced vs 3-panel:  +0.2

[running avg] over 1 scored tasks:
  balanced:   75.30  (beats mainlike 1/1, 3p 1/1, base 1/1)
  mainlike:   73.20
  3-panel:    75.10
  baseline:   71.40
```

## 成本分析

| 项目 | 调用次数（31题） | 单价 | 总成本 |
|------|----------------|------|--------|
| Panel生成 | 0（复用） | - | **$0** |
| Judge分析 | 0（复用cache） | - | **$0** |
| Fuser（kimi-k3） | 31次 | ~$0.03/次 | **~$0.93** |
| Scoring（gemini-3.1-pro） | 31次 × 3 passes | ~$0.02/题 | **~$0.62** |
| **总计** | | | **~$1.55** |

对比：
- 完整4-panel从头跑：~$120（包含panel生成、搜索、judge、fuser、scoring）
- Taskwise（复用）：~$1.55（只有fuser + scoring）
- **节省 98.7%**

## 脚本设计细节

### 1. 实时日志flush

```bash
# 所有Python调用都用 -u（unbuffered）
$PY -u scripts/draco_client_fusion.py ...
$PY -u scripts/draco_rejudge.py ...
$PY -u - <<'PYEOF'
print(..., flush=True)  # 每个print都flush
PYEOF
```

### 2. 已完成任务检测

每次处理前检查该任务是否已有评分：
```python
already_scored = check_if_scored(task_id)
if already_scored:
    skip_to_next_task()
```

这样Ctrl+C中断后重新运行，会自动跳过已完成的题目。

### 3. Fusion/Score的Resume模式

```bash
# 两个脚本都用--resume --execute
# --resume: 跳过output文件中已有的task
# --execute: 实际执行（不是dry-run）
scripts/draco_client_fusion.py ... --resume --execute
scripts/draco_rejudge.py ... --resume --execute
```

### 4. Judge Cache复用

```bash
--judge-cache "$MAINLIKE/judge_cache.jsonl"
```

复用mainlike跑过的judge分析，避免重复调用judge（31次 × $0.02 = $0.62节省）。

### 5. 输出过滤

Fusion和Rejudge脚本会输出大量调试信息，用grep过滤只显示关键信息：
```bash
... 2>&1 | grep -E "(Processing task|fused task|ERROR|FATAL|$tid)" || true
```

## 监控进度

### 实时查看正在处理的任务
```bash
tail -f out/fusion4_balanced_run_*.log | grep "====="
```

### 查看running average
```bash
tail -f out/fusion4_balanced_run_*.log | grep "running avg" -A 5
```

### 查看已完成数量
```bash
wc -l out/fusion4_balanced/score-fusion-4panel-balanced.jsonl
```

### 查看最新评分
```bash
tail -1 out/fusion4_balanced/score-fusion-4panel-balanced.jsonl | python -m json.tool
```

## 中断和恢复

### 优雅中断
```bash
# 找到进程
ps aux | grep run_4panel_balanced_taskwise

# 发送SIGTERM（让当前题目完成再退出）
kill <pid>

# 强制中断（Ctrl+C或SIGKILL）
kill -9 <pid>
```

### 恢复运行
直接重新运行脚本即可，已完成的任务会自动跳过：
```bash
bash scripts/run_4panel_balanced_taskwise.sh >> out/fusion4_balanced_run_existing.log 2>&1 &
```

## 最终Summary

运行完成后会显示完整总结：
```
=== FINAL RESULTS ===
Tasks scored: 31

Average scores:
  balanced:   74.52
  mainlike:   73.25  (Δ +1.27)
  3-panel:    75.12  (Δ -0.60)
  baseline:   71.42  (Δ +3.10)

Win rates:
  vs mainlike:  20/31 (64.5%)
  vs 3-panel:   14/31 (45.2%)
  vs baseline:  22/31 (71.0%)

Top 5 improvements vs mainlike:
  1. 262b3145-5b6d-486b-a09f-c8e6a8f98e20: +35.2 (balanced 73.2 vs mainlike 38.0)
  2. 0a652d00-5c22-4621-8ec4-dd92b1f1450b: +18.3 (balanced 86.8 vs mainlike 68.5)
  ...

Top 5 regressions vs mainlike:
  1. ca0edd2d-c9b8-4b85-9b40-754f4865579a: -5.4 (balanced 68.7 vs mainlike 74.1)
  ...

Output length distribution (balanced):
  avg length: 20345 chars (~5086 tokens)
  min: 2134, max: 31456
  truncated (finish=length): 2/31
```

## 预期时间

- 单题fusion（kimi-k3）：~30秒
- 单题scoring（gemini-3.1-pro，3 passes）：~60秒
- **单题总时间：~90秒**
- **31题总时间：~47分钟**

实际可能更快（并发、缓存命中等）。

## 故障排查

### 问题1：Judge cache未找到

```
FATAL: out/fusion4_mainlike/judge_cache.jsonl not found
```

**原因**：mainlike没有运行过，或者judge_cache文件被删除。

**解决**：
```bash
# 选项A：先运行mainlike（会生成judge_cache）
bash scripts/run_4panel_mainlike.sh

# 选项B：不用judge cache（会重新调用judge，增加成本~$0.62）
# 修改脚本，去掉 --judge-cache 参数
```

### 问题2：Panel结果未找到

```
FATAL: out/fusion4_mimo/panel-mimo-v2.5-pro.jsonl not found
```

**原因**：mimo panel没有生成。

**解决**：
```bash
bash scripts/run_4panel_mimo.sh  # 只需要等panel生成完，不需要等fusion/score
```

### 问题3：输出没有实时更新

**原因**：尽管用了python -u，但终端或日志文件仍可能有缓冲。

**解决**：
```bash
# 用unbuffer（如果系统有）
unbuffer bash scripts/run_4panel_balanced_taskwise.sh | tee out/run.log

# 或者用script命令
script -c "bash scripts/run_4panel_balanced_taskwise.sh" out/run.log
```

### 问题4：某题卡住不动

**原因**：网络超时、API限流、模型拒答等。

**诊断**：
```bash
# 查看最新的fusion/score输出
tail -100 out/fusion4_balanced_run_*.log

# 检查API健康
curl -H "Authorization: Bearer $TR_API_KEY" \
  https://router-api.0g.ai/v1/models
```

**解决**：
- Ctrl+C中断，重新运行（会跳过已完成的）
- 检查API key是否有效
- 检查是否触发rate limit

## 与原版run_4panel_mimo_balanced.sh的对比

| 维度 | 原版（批量） | Taskwise（逐题） |
|------|------------|----------------|
| **执行顺序** | 先全部fusion，再全部score | 每题fusion→立即score |
| **日志实时性** | 可能延迟（缓冲） | 实时（python -u） |
| **中断恢复** | 需要重跑全部 | 自动跳过已完成 |
| **查看进度** | 看文件行数 | 实时running avg |
| **调试友好度** | 难定位失败的题 | 每题独立，易定位 |
| **总运行时间** | 相同（~47分钟） | 相同 |
| **适用场景** | 稳定环境，一次跑完 | 不稳定网络，需监控 |

**推荐**：用taskwise版本，实时反馈更好，更容易发现问题。
