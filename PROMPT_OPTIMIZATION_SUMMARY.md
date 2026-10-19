# Fusion Prompt 优化总结（2026-10-17）

## 问题诊断

基于 4panel-mimo 的评测结果（out/fusion4_mimo/），发现以下问题：

### 1. 整体表现落后
- **4panel-mimo (mainlike prompt)**: 平均分 70.40
- **baseline (gpt-5.6-sol)**: 平均分 71.42
- **差距**: -1.02分，胜率仅 58.1% (18/31)

这与之前3-panel领先baseline约2分的结果形成鲜明对比，说明mainlike的简化prompt导致性能退化。

### 2. 具体失分模式

以最落败的题目 e1f2c310 为例（mimo 48.7 vs baseline 82.9，差34.2分）：

| 标准类别 | Fusion失分 | Baseline通过 | 失分原因 |
|---------|-----------|-------------|---------|
| analysis-1/2/3/6 | ✗ (共32分) | ✓ | **分析深度不足** |
| tensorrt-int8-documentation | ✗ (8分) | ✓ | **文档引用缺失** |
| factual-2/5/7/8 | 部分✗ | 部分✓ | 具体数字遗漏 |

**核心问题**：fusion-prompt-mainlike的极简指令（仅1段）导致fuser主动压缩panel答案中的：
- 定量分析和对比
- 技术推理细节
- 官方文档引用
- 具体数字和规格

### 3. 历史背景

| 版本 | 特点 | 问题 |
|-----|------|-----|
| **fusion-prompt-mainlike** | 1段，极简指令，无长度指导 | **细节丢失严重**（当前） |
| **fusion-prompt-detail-fix** | 5段，明确"保留细节"，提token预算 | 强fuser（kimi-k3）100%撞12k上限 |
| **upstream main / enclave fusion.go** | 1段，无调优 | 未在0G router上验证 |

---

## 优化方案：fusion-prompt-detail-balanced

### 设计原则

1. **针对失分点的具体要求**
   - 明确要求"analytical depth"（针对analysis失分）
   - 明确要求"inline citations"和"official technical document"（针对引用缺失）
   - 明确要求"specific numbers, dates, specifications"（针对factual失分）

2. **避免压力词触发防御性缩短**
   - ❌ 不用："token", "budget", "limited", "do not compress"
   - ✅ 改用："comprehensive", "ample space", "write thoroughly"
   - **原理**：LLM对prompt中的"限制性"词汇敏感，会触发防御性压缩，即使实际空间充足

3. **积极暗示代替禁止性措辞**
   - ❌ detail-fix: "Do not compress... Do not omit..."（纠正坏习惯的语气）
   - ✅ balanced: "Write complete... Preserve depth... Maintain citations"（积极描述）

4. **结构化指导保证核心内容**
   - "critical analysis first, followed by details"
   - 即使接近11500上限被截断，核心答案也已完整

### 新版FINAL_INSTRUCTION（5段，约200词）

```python
FINAL_INSTRUCTION = (
    # 第1段：基本指令（保持不变，含methodology-exception修复）
    "TrustedRouter Fusion panel answers and judge analysis follow. Use the panel "
    "answers as the primary evidence and the judge analysis as guidance to write "
    "the final answer for the original request. Return only the final visible "
    "answer. Do not include chain-of-thought, hidden reasoning, analysis, "
    "scratchpad text, <think> blocks, or internal model names -- unless the "
    "original request explicitly asks you to describe your own research "
    "process or approach (not merely because the subject matter itself "
    "concerns research methodology).\n\n"

    # 第2段：保留分析深度和具体细节
    "Write a complete, fully detailed answer. When panel answers provide "
    "specific analysis, quantitative comparisons, trade-off discussions, or "
    "technical reasoning, preserve that analytical depth in your synthesis. "
    "Include all specific numbers, dates, technical specifications, and named "
    "examples from the panel evidence. Do not compress quantitative details "
    "or omit load-bearing facts for brevity.\n\n"

    # 第3段：保留文档引用
    "Maintain inline citations in the same form the panel answers used — "
    "documentation URLs, paper titles, or source names should stay attached "
    "to the specific claims they support. If a panel answer cites an official "
    "technical document or benchmark result, carry that citation through to "
    "your final answer.\n\n"

    # 第4段：处理冲突（简化版）
    "Where panel sources conflict, state your conclusion directly based on "
    "which position is better corroborated or more specifically sourced. You "
    "do not need to present every disagreement as unresolved.\n\n"

    # 第5段：长度指导（积极暗示，不提数字）
    "Your response should be comprehensive. Structure complex answers with "
    "clear sections, putting the most critical analysis and direct answers "
    "first, followed by supporting details and caveats. Write as thoroughly "
    "as the task requires — there is ample space for a fully substantiated "
    "response."
)
```

### 与detail-fix的对比

| 维度 | detail-fix | balanced |
|-----|-----------|----------|
| 长度 | 5段，约400词 | 5段，约200词 |
| 压力词 | 包含"budget", "token", "limited" | **完全移除** |
| 措辞风格 | 禁止性（"do not compress"） | 积极性（"write complete"） |
| 长度控制 | 显性提token数字 | 隐性暗示"ample space" |
| 预期行为 | 强fuser撞上限（过度响应） | 平衡：保留细节但不失控 |

---

## 已完成的修改

### 1. 更新 `scripts/draco_client_fusion.py`

**修改点1：FINAL_INSTRUCTION（第60-131行）**
- 替换为新的5段balanced版本
- 添加详细的设计原理注释

**修改点2：fuser_max_tokens默认值（第271行）**
```python
# 从
p.add_argument("--fuser-max-tokens", type=int, default=12000)
# 改为
p.add_argument("--fuser-max-tokens", type=int, default=11500)
```
- 留500 token buffer，避免撞上限mid-sentence
- 11500 = 用户要求的硬性上限

### 2. 新增 `scripts/run_4panel_mimo_balanced.sh`

**特点**：
- 复用已有的panel结果（不重新生成mimo panel，节省成本）
- 输出目录：`out/fusion4_mimo_balanced/`
- 自动对比三个版本：balanced vs mainlike vs baseline
- 实时显示改进/退化最大的题目

**使用方法**：
```bash
cd /workspace/git_exp/TrustedRouter-Fusion-Draco
export TR_API_KEY=your_key_here
export EXA_API_KEY=your_key_here

# 后台运行
nohup bash scripts/run_4panel_mimo_balanced.sh \
  > out/fusion4_mimo_balanced_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 查看进度（推荐用数据文件而非日志，因为Python输出缓冲）
watch -n 5 "wc -l out/fusion4_mimo_balanced/*.jsonl; tail -5 out/fusion4_mimo_balanced_run_*.log"
```

---

## 预期效果

### 1. 分数改进
- **主要提升点**：analysis-1/2/3/6 标准（共32分）
- **次要提升点**：tensorrt-int8-documentation等引用标准（8分）
- **预期**：balanced vs mainlike 在落败最严重的题目上提升10-30分

### 2. 长度分布
- **mainlike**：平均约6-8k token，细节不足
- **detail-fix + kimi-k3**：100%撞12k上限
- **balanced + minimax-m3**：预期8-11k，偶尔接近11500但不频繁截断

### 3. 失分模式
- **analysis失分**：应显著减少（这是优化重点）
- **引用缺失**：应减少
- **长度截断**：11500上限 + "critical first"策略 → 即使截断，核心内容也完整

---

## 验证步骤

评测完成后，建议检查：

### 1. 整体分数
```bash
cd /workspace/git_exp/TrustedRouter-Fusion-Draco
python3 << 'EOF'
import json

def load_scores(path):
    scores = {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('status') != 'failed':
                scores[r['task_id']] = r['score']
    return scores

balanced = load_scores('out/fusion4_mimo_balanced/score-fusion-4panel-mimo-balanced.jsonl')
mainlike = load_scores('out/fusion4_mimo/score-fusion-4panel-mimo.jsonl')
baseline = load_scores('out/task_major_v2/score-baseline.jsonl')

common = set(balanced) & set(mainlike) & set(baseline)
print(f"Common tasks: {len(common)}")
print(f"Balanced:  {sum(balanced[t] for t in common)/len(common):.2f}")
print(f"Mainlike:  {sum(mainlike[t] for t in common)/len(common):.2f}")
print(f"Baseline:  {sum(baseline[t] for t in common)/len(common):.2f}")

wins_vs_main = sum(1 for t in common if balanced[t] > mainlike[t])
wins_vs_base = sum(1 for t in common if balanced[t] > baseline[t])
print(f"\nBalanced wins: vs mainlike {wins_vs_main}/{len(common)}, vs baseline {wins_vs_base}/{len(common)}")
EOF
```

### 2. 典型失败案例的改进
重点检查之前落败最严重的题目（e1f2c310等）的评分标准：
```bash
# 查看某个任务的详细评分
python3 << 'EOF'
import json

target = "e1f2c310-d311-49da-b0e4-ee855603469d"

for name, path in [
    ("balanced", "out/fusion4_mimo_balanced/score-fusion-4panel-mimo-balanced.jsonl"),
    ("mainlike", "out/fusion4_mimo/score-fusion-4panel-mimo.jsonl")
]:
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r['task_id'] == target:
                print(f"\n=== {name}: {r['score']:.1f} ===")
                # 统计通过的analysis标准
                analysis_met = sum(1 for j in r['judges'] for c in j['criteria'] 
                                  if 'analysis' in c['id'] and c['met'])
                analysis_total = sum(1 for j in r['judges'] for c in j['criteria'] 
                                    if 'analysis' in c['id'])
                print(f"Analysis standards: {analysis_met}/{analysis_total} met")
                break
EOF
```

### 3. 长度分布
```bash
# 检查是否频繁撞上限
python3 << 'EOF'
import json
import tiktoken

enc = tiktoken.encoding_for_model("gpt-4")

with open('out/fusion4_mimo_balanced/fusion-4panel-mimo-balanced.jsonl') as f:
    lengths = []
    for line in f:
        r = json.loads(line)
        content = r.get('final', {}).get('content', '')
        lengths.append(len(enc.encode(content)))
    
print(f"Token lengths: min={min(lengths)}, max={max(lengths)}, "
      f"avg={sum(lengths)/len(lengths):.0f}, median={sorted(lengths)[len(lengths)//2]}")
print(f"Over 11000: {sum(1 for x in lengths if x > 11000)}/{len(lengths)}")
print(f"Over 11500: {sum(1 for x in lengths if x > 11500)}/{len(lengths)}")
EOF
```

---

## 如果效果不理想

### Plan B：调整策略

1. **如果还是丢细节**（balanced ≈ mainlike）：
   - 措辞还不够强烈，加强"analytical depth"的要求
   - 或者在第2段加"preserve every quantitative comparison"

2. **如果频繁撞上限**（>50%的题超11500）：
   - 不是prompt问题，是fuser模型问题（minimax-m3倾向冗长）
   - 考虑换fuser（比如gemini-flash-1.5，输出更简洁）
   - 或者放宽到11800（留200 buffer）

3. **如果分数改进<1分**：
   - 可能是面板召回率问题（见draco-eval-insights-20261015.md第3点）
   - fusion只能放大panel已有的信息，无法无中生有
   - 需要检查panel answers是否已包含丢失的内容

---

## 相关文件

- **核心脚本**：`scripts/draco_client_fusion.py`（已修改）
- **运行脚本**：`scripts/run_4panel_mimo_balanced.sh`（新增）
- **输出目录**：`out/fusion4_mimo_balanced/`
- **参考对比**：
  - Mainlike结果：`out/fusion4_mimo/`
  - Baseline结果：`out/task_major_v2/score-baseline.jsonl`
- **历史分析**：
  - `report/draco-fusion-vs-baseline-summary-20261014.md`
  - `report/draco-eval-insights-20261015.md`
