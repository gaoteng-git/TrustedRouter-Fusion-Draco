# 4-Panel vs 3-Panel 失分根因分析（2026-10-17）

## 问题重述

用户观察：4-panel fusion落败于3-panel，说"好多面板模型的细节没有被融合模型写到最终结果里"。

## 数据事实

### 评测结果（31道题）

| 配置 | Fuser模型 | 平均分 | vs Baseline | vs 3-panel | 平均输出 |
|------|----------|--------|------------|-----------|---------|
| **3-panel** | minimax-m3 | **75.12** | +3.70 | - | ~7718 tokens |
| **4-panel mainlike** | kimi-k3 | 73.25 | +1.83 | -1.87 | ~3435 tokens |
| baseline | gpt-5.6-sol | 71.42 | - | - | - |

### 失分模式

4-panel落败于3-panel的19道题中，最严重的案例（262b3145）：
- **4p: 38.0分 vs 3p: 81.5分，差43.6分**
- 4p通过39个标准，未通过54个
- 3p通过75个标准，未通过18个
- 4p大量失分于：regulatory相关标准（EU AI Act, US Take It Down Act, state laws等）

### 输出长度对比

```
3-panel (minimax-m3):  平均 30873 字符  ≈ 7718 tokens
4-panel (kimi-k3):     平均 13741 字符  ≈ 3435 tokens

差距: 3-panel比4-panel长 125%（2.25倍）
```

最极端的5道题：
1. 79224558: 4p=13k字符，3p=51k字符（3.9x），评分4p=51.6 vs 3p=56.0
2. 0a652d00: 4p=14k字符，3p=47k字符（3.3x），评分4p=68.5 vs 3p=71.8
3. c6ca6371: 4p=19k字符，3p=51k字符（2.6x），评分4p=87.5 vs 3p=85.7（**4p胜**）
4. 9b35aaf1: 4p=17k字符，3p=44k字符（2.6x），评分4p=96.4 vs 3p=96.4（**平局**）
5. 1516d849: 4p=14k字符，3p=41k字符（2.8x），评分4p=84.0 vs 3p=79.6（**4p胜**）

**关键观察**：长度不等于质量。有的题3-panel输出长3倍但评分相同或反而更低。

## 根本原因

### ❌ 不是prompt的问题

- 4-panel mainlike和3-panel都使用相同的简化版prompt（1段FINAL_INSTRUCTION）
- 区别不在写作指令，而在**fuser模型选择**

### ✅ 是fuser模型的差异

| 配置 | Fuser模型 | 特性 |
|------|----------|------|
| 3-panel | **minimax-m3** | 输出详尽（平均7.7k tokens），很少被截断（0/31题finish_reason=length） |
| 4-panel mainlike | **kimi-k3** | 输出紧凑（平均3.4k tokens），主动压缩，少数被截断（3/31题） |

**kimi-k3的"主动压缩"行为**：
- 即使fuser_max_tokens=12000，实际平均只输出3435 tokens
- 即使finish_reason=stop（正常结束），输出仍比minimax-m3短55%
- 这不是截断问题（只有3题被截断），而是模型倾向性

**为什么长度重要**：
- 复杂题目（如262b3145的deepfake detection）需要覆盖多个维度（technical methods, regulatory landscape, ethical concerns）
- kimi-k3压缩时倾向于优先保留technical内容，削减regulatory/policy/ethical sections
- 这导致在覆盖度要求高的rubric上大量失分

**为什么长度不总是决定性的**：
- 某些题目（如c6ca6371）可能3-panel输出冗长但未击中评分点
- 某些题目（如9b35aaf1）核心答案简洁即可，额外细节无助于评分
- 说明问题不是"越长越好"，而是**正确的细节覆盖**

## 为什么4-panel加mimo反而变差

### 假设1：mimo panel的内容稀释效应

加入mimo后，judge需要处理4个panel而不是3个。可能导致：
- judge analysis变得更复杂，给fuser的指导信号混乱
- kimi-k3在面对更多来源时更激进地压缩

### 假设2：mimo panel与其他panel的内容重叠

如果mimo的贡献与前3个panel高度重复，可能：
- 没有带来新的信息维度
- 反而增加了fuser需要去重的负担
- kimi-k3在去重时丢失了原本保留的细节

### 假设3：4-panel+kimi-k3的组合特别差

- 3-panel+minimax-m3: 75.12分（工作良好）
- 4-panel+kimi-k3: 73.25分（变差）
- 需要验证：4-panel+minimax-m3会怎样？

**关键测试**：如果4-panel+minimax-m3能超过3-panel，说明mimo panel本身是有价值的，只是kimi-k3无法有效融合4个来源。

## 解决方案

### 方案1：4-panel + minimax-m3 + balanced prompt（推荐）

**配置**：
- 4个panel（复用已有，无额外成本）
- Fuser: minimax-m3（与3-panel相同）
- Prompt: 新的detail-balanced版本（5段，强调analytical depth和inline citations，但不提token数字）
- fuser_max_tokens: 11500（留buffer，避免极端情况截断）

**预期**：
- 输出长度接近3-panel（~7-8k tokens）
- mimo panel带来的额外信息能被有效融合
- balanced prompt强调的"analytical depth"和"citations"进一步提升质量
- 目标：**超过3-panel的75.12分**

**成本**：
- Panel: $0（复用）
- Judge: $0（使用--judge-cache）
- Fuser: minimax-m3的成本（与3-panel相同数量级）
- 总成本增量：minimal

**脚本**：`scripts/run_4panel_mimo_balanced.sh`（已创建）

### 方案2：3-panel + balanced prompt（保守方案）

如果方案1仍不理想（4p+minimax-m3仍输3p），说明mimo panel确实没有贡献，退回3-panel并用balanced prompt微调。

**配置**：
- 3个panel（与原版相同）
- Fuser: minimax-m3
- Prompt: balanced版本

**预期**：在75.12基础上小幅提升（+0.5~1分）

## 已完成的修改

### 1. 更新 `scripts/draco_client_fusion.py`

**修改1：FINAL_INSTRUCTION（L60-131）**
```python
# 从1段扩展到5段，约200词
# 1. 基本指令（保持methodology-exception修复）
# 2. 保留分析深度和具体细节（针对analysis失分）
# 3. 保留文档引用（针对引用缺失）
# 4. 处理冲突（简化版）
# 5. 长度指导（积极暗示"ample space"，不提token数字）
```

设计原理：
- ✅ 用"complete, fully detailed, comprehensive"代替"do not compress"
- ✅ 明确要求"analytical depth"和"inline citations"（针对实际失分点）
- ❌ 不提"token"、"budget"、"limited"等压力词（避免触发防御性缩短）

**修改2：fuser_max_tokens默认值（L271）**
```python
default=11500  # 原为12000，留500 buffer
```

### 2. 新增 `scripts/run_4panel_mimo_balanced.sh`

- 复用所有panel结果（无额外生成/搜索成本）
- **Fuser: minimax-m3**（关键！不用kimi-k3）
- 输出到 `out/fusion4_mimo_balanced/`
- 自动对比 balanced vs mainlike vs 3-panel vs baseline

### 3. 文档

- `PROMPT_OPTIMIZATION_SUMMARY.md`：之前的分析（基于错误假设）
- **`ROOT_CAUSE_ANALYSIS.md`**：本文件，纠正后的根因分析

## 如何运行

```bash
cd /workspace/git_exp/TrustedRouter-Fusion-Draco

export TR_API_KEY=your_key_here
export EXA_API_KEY=your_key_here

# 后台运行
nohup bash scripts/run_4panel_mimo_balanced.sh \
  > out/fusion4_mimo_balanced_run_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 查看进度
watch -n 5 "wc -l out/fusion4_mimo_balanced/*.jsonl; tail -5 out/fusion4_mimo_balanced_run_*.log"
```

## 预期效果

### 成功指标

1. **输出长度**：balanced平均应≈7-8k tokens（接近3-panel，而不是mainlike的3.4k）
2. **评分**：
   - balanced > 3-panel（75.12分）→ 说明mimo panel有价值 + prompt有效
   - balanced > mainlike（73.25分）→ 底线，证明minimax-m3 > kimi-k3
3. **失分模式**：
   - 262b3145等regulatory-heavy题目应显著改进
   - analysis-1/2/3/6标准通过率提升
   - tensorrt-int8-documentation等引用标准通过率提升

### 如果不成功

**Scenario A: balanced ≈ mainlike（都是73.25）**
- 说明prompt无效，问题全在fuser模型
- 但很unlikely，因为minimax-m3在3-panel上已经证明能输出7.7k tokens

**Scenario B: 3-panel > balanced > mainlike（例如74.5分）**
- 说明minimax-m3好于kimi-k3，prompt略有帮助
- 但mimo panel没有正贡献（稀释效应 or 内容重复）
- **Action**: 退回方案2（3-panel + balanced prompt）

**Scenario C: balanced < mainlike（例如72分）**
- 说明balanced prompt反向作用（unlikely，因为用的同一个minimax-m3）
- **Action**: 回滚prompt，只改fuser模型

## 关键教训

1. **Fuser模型选择比prompt调优更重要**
   - kimi-k3 vs minimax-m3的差异：1.87分 + 55%长度差
   - 这远大于prompt微调能带来的增益（通常<1分）

2. **长度不等于质量，但覆盖度很重要**
   - 某些题长度相差3倍但评分相同（说明冗余）
   - 某些题长度相差2倍评分差40分（说明遗漏关键维度）
   - Rubric设计决定了什么样的细节有价值

3. **多panel不一定更好**
   - 4-panel比3-panel多了mimo，但变差了
   - 可能原因：内容稀释、重复、fuser模型无法有效处理更多来源
   - 需要验证：问题在fuser还是在第4个panel本身

4. **始终对比相同控制变量**
   - 之前误以为都用相同的fuser，实际上4p用kimi-k3、3p用minimax-m3
   - 这导致最初的诊断完全错误（以为是prompt问题）
   - 教训：先检查配置差异，再归因于算法/prompt
