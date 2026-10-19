# Kimi-k3 Fuser优化策略：重要内容优先（2026-10-17）

## 问题重述

4-panel (kimi-k3 fuser)落败于3-panel (minimax-m3 fuser)：
- 4-panel: 73.25分，avg ~3435 tokens输出
- 3-panel: 75.12分，avg ~7718 tokens输出
- 最严重失分：262b3145题差43.6分，主要失分在regulatory/policy标准

## 策略转向：不强迫kimi-k3变长，而是引导它选对保留什么

### ❌ 之前的误判

最初以为问题是"kimi-k3输出太短 → 需要prompt强调保留细节 → 让它变长"。

但分析发现：
1. 长度不等于质量（某些题3-panel长3倍但评分相同或更低）
2. Kimi-k3的简洁可能是**特性不是bug**（如果它选对了保留什么）
3. 问题在于它**选错了优先级**（保留technical细节，削减regulatory/policy维度）

### ✅ 新策略：重要内容优先（Importance-Ordered Structure）

借鉴commit 8590e28和cc8458a的"order content by importance"策略：

**核心思想**：
> 即使kimi-k3仍然输出短（4-6k tokens），只要把**load-bearing内容**放前面，
> 核心答案就完整。Regulatory/policy等关键维度不会被跳过或放到最后被truncate。

## 新版Prompt设计

### 第1段：重要内容优先（核心创新）

```
Structure your answer by importance. Lead with the direct answer to the 
original request, then the most load-bearing facts, analysis, and 
conclusions. These must include:
  - Specific quantitative findings (numbers, dates, percentages, technical specs)
  - Key analytical insights (trade-offs, comparisons, causal reasoning)
  - Critical domain-specific details (regulatory requirements, technical standards, 
    policy implications when relevant to the question)
  - Inline citations to official sources (documentation URLs, paper titles, 
    authoritative references)

Place secondary elaboration, background context, and less critical caveats 
later. This ordering ensures that if length constraints bind, the core 
substantive answer remains complete and the material most likely to satisfy 
evaluation criteria is already present.
```

**设计要点**：
1. **明确列出必须包含的4类内容**：
   - Quantitative findings（针对factual失分）
   - Analytical insights（针对analysis失分）
   - **Domain-specific details including regulatory/policy**（针对262b3145等题的regulatory失分）
   - Inline citations（针对tensorrt-int8-documentation等失分）

2. **"These must include"** - 强制性要求，不是建议

3. **"Place secondary elaboration... later"** - 明确什么可以延后

4. **"if length constraints bind"** - 承认kimi-k3可能输出短，但确保核心完整

### 第2段：Budget Note（借鉴cc8458a）

```python
stated_budget = int(args.fuser_max_tokens * 0.95)  # 95% of 11500 = 10925
budget_note = (
    f"\n\nYour response budget is approximately {stated_budget} tokens. "
    f"A thorough answer addressing all aspects of the original request is "
    f"expected to approach this limit. Plan your structure so you finish "
    f"cleanly -- conclude with your final recommendations or summary "
    f"complete, rather than running into the limit mid-sentence."
)
```

**设计要点**：
1. **不提source-material token数** - cc8458a的关键教训（会触发防御性over-compression）

2. **"expected to approach this limit"** - 鼓励充分使用，不是"limited budget"的压力措辞

3. **"finish cleanly"** - 针对kimi-k3的3/31题finish=length问题

4. **95% of cap** - 留500 buffer，避免mid-sentence truncation

## 与之前版本的对比

| 维度 | mainlike | detail-fix | **kimi-prioritize (新)** |
|-----|----------|------------|-------------------------|
| **核心策略** | 极简（1段） | 保留细节（5段） | **重要内容优先（3段）** |
| **长度** | ~70词 | ~400词 | ~250词 |
| **对kimi-k3的假设** | 能自己判断 | 需要强迫变长 | **引导选对优先级** |
| **Budget note** | 无 | 有，提source-size | **有，不提source-size** |
| **Fuser模型** | kimi-k3 | minimax-m3 | **kimi-k3** |
| **预期输出长度** | ~3435 tokens | ~7718 tokens | **~4-6k tokens** |
| **目标** | 测试极简 | 强制详尽 | **核心完整+正确优先级** |

## 关键创新点

### 1. 明确列出"Critical domain-specific details"

```
Critical domain-specific details (regulatory requirements, technical standards, 
policy implications when relevant to the question)
```

这一条**直接针对262b3145等题的失分**：
- 4-panel在regulatory标准上大量失分（EU AI Act, state laws等）
- 不是因为panel没有这些信息，而是kimi-k3融合时优先级判断错误
- 新prompt明确把"regulatory requirements, policy implications"列为必须包含的load-bearing内容

### 2. "These must include"的强制性措辞

不是"should include"或"consider including"，而是**"must include"** - 这是requirement，不是suggestion。

### 3. 承认kimi-k3的简洁特性

```
This ordering ensures that if length constraints bind, the core substantive 
answer remains complete...
```

**"if length constraints bind"** - 不是"you have ample space"的乐观假设，而是现实承认kimi-k3可能输出短。

关键是：**即使短，核心也完整**。

### 4. Budget note的心理学设计

- ❌ **不用**："limited budget", "source material is 25k tokens"
- ✅ **改用**："expected to approach this limit", "thorough answer"

从cc8458a学到：提source-size会触发"summarize模式"，即使明确说"don't compress"。

心理学原理：**给模型一个目标（"approach this limit"），而不是一个限制（"don't exceed"）**。

## 预期效果

### 输出长度

- **不期望**达到minimax-m3的7718 tokens
- **期望**：4-6k tokens（比mainlike的3435略长）
- **关键**：即使只有4-6k tokens，但内容结构是对的

### 评分改进

**主要提升点**（针对262b3145等regulatory-heavy题目）：
- EU AI Act相关标准（10分）
- US Take It Down Act相关标准（10分）
- State laws相关标准（10分）
- Regulatory staging complexity（8分）
- Official government sources引用（8分）

**预期**：在这类题上从38.0分提升到60-70分（不一定达到3-panel的81.5，但显著改进）

**次要提升点**（所有题）：
- analysis-1/2/3/6标准（因为"analytical insights"明确列为必须）
- 引用标准（因为"inline citations"明确列为必须）

### 整体平均分

- **保守目标**：74.0+（vs mainlike的73.25，至少+0.75）
- **乐观目标**：75.0+（接近或超过3-panel的75.12）
- **关键指标**：在19道落败题中赢回10+道

## 验证步骤

### 1. 输出结构检查

随机抽5道题，检查fusion答案的前1000词是否包含：
- [ ] 直接回答原问题
- [ ] Quantitative findings
- [ ] Analytical insights
- [ ] Domain-specific details (regulatory如果relevant)
- [ ] Inline citations

如果前1000词已包含这些 → 结构正确（即使总长度短）

### 2. Regulatory题目专项检查

重点看262b3145等regulatory-heavy题目：
- [ ] EU AI Act是否在前半部分提到
- [ ] US/state laws是否在前半部分提到
- [ ] Regulatory标准通过率是否改进

### 3. 长度分布

```python
import json
lengths = []
with open('out/fusion4_mimo_balanced/fusion-4panel-mimo-balanced.jsonl') as f:
    for line in f:
        r = json.loads(line)
        lengths.append(len(r['final']['content']))

print(f"avg={sum(lengths)/len(lengths):.0f}")
print(f"4-6k tokens (16-24k chars): {sum(1 for x in lengths if 16000 <= x <= 24000)}/{len(lengths)}")
```

预期：avg ~20k字符（5k tokens），4-6k tokens范围内的题目占majority

### 4. Finish_reason分布

```python
finish_reasons = []
with open('out/fusion4_mimo_balanced/fusion-4panel-mimo-balanced.jsonl') as f:
    for line in f:
        r = json.loads(line)
        finish_reasons.append(r['final'].get('finish_reason', 'unknown'))

from collections import Counter
print(Counter(finish_reasons))
```

预期：finish=length的题目 ≤ 3道（不多于mainlike）

## 如果效果不理想

### Scenario A: 仍然输出很短（avg < 4k tokens）且评分没改进

说明budget note不够强。

**Action**: 改budget_note措辞，用更强的鼓励：
```
"A complete answer to this type of question typically requires 8,000-10,000 
tokens. Use the full budget available to you."
```

### Scenario B: 输出变长了（avg ~6k tokens）但评分仍差

说明"重要内容优先"的指令不够清晰。

**Action**: 在FINAL_INSTRUCTION第1段加一个具体例子：
```
For instance, if the task asks about deepfake detection and the panel answers 
cover both technical methods AND regulatory landscape, your answer must address 
BOTH dimensions in the first half of your response, not just the technical side.
```

### Scenario C: Regulatory题目改进了，但其他题目退步了

说明过度强调regulatory，导致其他维度被压缩。

**Action**: 改"Critical domain-specific details"措辞：
```
Critical domain-specific details (which may include regulatory requirements, 
technical standards, policy implications, OR other domain-specific considerations 
depending on what the original request emphasizes)
```

## 运行命令

```bash
cd /workspace/git_exp/TrustedRouter-Fusion-Draco

export TR_API_KEY=your_key_here
export EXA_API_KEY=your_key_here

nohup bash scripts/run_4panel_mimo_balanced.sh \
  > out/fusion4_mimo_balanced_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 查看进度
watch -n 5 "wc -l out/fusion4_mimo_balanced/*.jsonl; tail -3 out/fusion4_mimo_balanced_run_*.log"
```

## 关键教训总结

1. **模型特性是中性的** - kimi-k3的简洁不是缺陷，关键是引导它简洁在对的地方

2. **结构比长度更重要** - 4k tokens的importance-ordered答案 > 8k tokens的random-ordered答案

3. **Commit历史是金矿** - 8590e28和cc8458a的"order by importance"和"budget note教训"直接适用

4. **心理学设计matters** - "expected to approach"vs"limited budget"，措辞差异导致行为差异

5. **针对失分点设计** - 不是泛泛说"preserve detail"，而是列出"regulatory requirements, analytical insights, citations"等具体失分维度
