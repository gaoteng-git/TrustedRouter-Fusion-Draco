# DRACO 评测汇总：Fusion（3面板 / 5面板）vs Baseline（gpt-5.6-sol）

更新日期：2026-10-14
网关：0G router（https://router-api.0g.ai/v1），分支 `fusion-prompt-detail-fix`
打分判官：gemini-3.1-pro-preview（chunk=3 条标准/次，reasoning_effort=high）
Baseline 与每个面板成员均通过完全相同的 tooled agentic harness（`draco_agentic_solo.py`）运行。

---

## 一、3面板 fusion vs baseline（100 题全量，完整数据）

配置：panel = minimax-m3 + glm-5.3-flash + deepseek-v4-flash，judge = fuser = minimax-m3，
`--require-full-panel`（3/3 面板齐全才融合）。数据目录 `out/task_major_v2/`。

### 平均分与胜率

| 范围 | 3-panel | baseline | 3-panel 胜率 |
|---|---|---|---|
| 非财务 80 题 | **70.39** | 67.84 | 49/80（61%） |
| 金融 20 题 | 65.47 | 64.99 | 8/20（40%） |
| **全部 100 题** | **69.41** | **67.27** | **57/100** |

- 3-panel 总体领先约 2.1 分，优势全部来自非财务题。
- 金融题基本打平：胜率不足一半，平均分靠少数大赢的题撑住。根因分析（抽样核查）表明金融题的
  得分关键在"能否挖到财报脚注里的冷门精确数字"，这更依赖单模型研究深度，面板融合的多视角
  优势在此类题上无法体现；约半数失分源于全部面板成员共同的研究空白（而非融合阶段丢失），
  prompt 微调无法修复。

### 成本（100 题全量）

| 项目 | 3-panel fusion | baseline | 比值 |
|---|---|---|---|
| LLM 成本 | **$15.81**（面板 $13.96 + 判官 $0.58 + 融合 $1.28） | $59.93 | 0.26x |
| 工具成本（Exa 搜索） | $23.32（3331 次） | $10.72（1532 次） | 2.17x |
| **总成本** | **$39.13** | **$70.66** | **0.55x** |

核心结论：**3-panel 用 baseline 55% 的成本拿到高 2.1 分的成绩**。两边成本结构相反——
3-panel 的大头是搜索（3 个模型独立搜索），baseline 的大头是 LLM（gpt-5.6-sol 输出单价
$30/M，是面板模型的 20-60 倍）。判官+融合的额外开销合计 $1.86，占比不到 5%。

---

## 二、5面板 fusion（65 题，部分完成）

配置：panel = minimax-m3 + kimi-k3 + glm-5.3-flash + deepseek-v4-flash + qwen3.8-max，
judge = fuser = minimax-m3，`--require-full-panel`。数据目录 `out/task_major_5panel/`。
注意：5面板只跑完了非财务 80 题中的 65 题（评测中途停止，未跑金融题），以下所有数字
均基于这 65 题。

### 平均分与胜率（65 题，三方同题对比）

| 配置 | 平均分 | 胜率 |
|---|---|---|
| 5-panel fusion | **73.92** | 赢 3-panel 44/65，赢 baseline 50/65（77%） |
| 3-panel fusion | 70.47 | 赢 baseline 39/65 |
| baseline | 67.90 | — |

- 5-panel 比 3-panel 高约 3.5 分，比 baseline 高约 6 分，排序稳定：5-panel > 3-panel > baseline。
- 注意方法论告警：5-panel 与 3-panel 的对比中混杂了 fuser 写作指令（FINAL_INSTRUCTION）
  的多轮迭代（保留细节/引用内联/冲突裁决/预算兜底），且该调优只施加在融合一侧，baseline 的
  synthesis 指令未同步增强，因此"面板规模本身的贡献"无法从这组数据中干净分离。

### 成本（65 题）

| 项目 | 5-panel fusion | baseline（同 65 题） |
|---|---|---|
| 面板 LLM | $89.04（minimax-m3 $3.17 / kimi-k3 **$42.05** / glm-5.3-flash $1.89 / deepseek-v4-flash $3.46 / qwen3.8-max **$38.47**） | — |
| 判官 + 融合 | $0.58 + $1.04 | — |
| LLM 小计 | $90.66 | $36.05 |
| 工具（Exa） | $27.21（3887 次） | $7.52（1074 次） |
| **总成本** | **$117.87** | **$43.56** |

- 5-panel 总成本是 baseline 的 **2.7 倍**（而 3-panel 只有 baseline 的 0.55 倍）。
- 成本主因：kimi-k3 与 qwen3.8-max 两个新成员的单价比其余三个面板模型贵一个数量级,
  两者合计 $80.52，占 5-panel 总成本的 68%。

---

## 三、三种配置横向对比（quick view）

| | baseline | 3-panel | 5-panel |
|---|---|---|---|
| 平均分（65题同题） | 67.90 | 70.47 | **73.92** |
| 平均分（100题，仅前两者） | 67.27 | **69.41** | 未跑完 |
| 对 baseline 胜率 | — | 61%（非财务）/ 40%（金融） | 77%（非财务 65 题） |
| 成本相对 baseline | 1x | **0.55x** | ~2.7x |

**取舍结论**：
- 追求性价比：3-panel —— 更便宜（55% 成本）且更高分（+2.1 分），但金融题无优势。
- 追求分数上限：5-panel —— 最高分（+6 分 vs baseline），但成本接近 baseline 的 3 倍,
  且其中约 2/3 的成本来自两个贵价面板成员。
- 金融类深研任务：融合架构（无论 3/5 面板）相对单强模型没有表现出优势，瓶颈在面板成员
  的财报脚注挖掘能力，而非融合/判官环节。

---

## 三点五、Prometheus 2.0 真实配置的成本估算（100 题，未实际运行）

真实 Prometheus 2.0 配置（来自 quill-cloud-proxy enclave fusion.go，本仓库
`FUSION_PROMETHEUS20` 常量）：panel = minimax-m3 + kimi-k3 + glm-5.2 +
deepseek-v4-pro + mimo-v2.5-pro，judge = minimax-m3，**fuser = kimi-k3**。
我们未实际跑该配置（kimi-k3 曾因稳定性被禁用；现 ConnectError 重试已修复，可跑），
以下为"实测工作量 × 真实单价"的估算：

| 项目 | 费用 | 占比 | 依据 |
|---|---|---|---|
| kimi-k3（面板） | **$64.69** | 36% | 实测（同模型同角色真实 token 量） |
| glm-5.2（面板） | $16.40 | 9% | glm-5.3-flash 工作量代理 |
| mimo-v2.5-pro（面板） | $16.04 | 9% | qwen3.8-max 工作量代理（跨厂商，误差最大） |
| deepseek-v4-pro（面板） | $15.95 | 9% | deepseek-v4-flash 工作量代理 |
| minimax-m3（面板） | $4.88 | 3% | 实测 |
| 工具（Exa，~5980 次） | **$41.86** | 23% | 实测调用次数 × $0.007 |
| fuser（kimi-k3） | **$20.22** | 11% | 实测 fuser token 量 × kimi 单价 |
| 判官（minimax-m3） | $0.89 | <1% | 字符估算 |
| **总计** | **$180.94** | | **= baseline($70.66 实测) 的 2.56x** |

关键观察：
1. **kimi-k3 一家占总成本 47%**（面板 $64.69 + fuser $20.22）。其输入单价（$3/M）是
   其他面板模型的 4-20 倍，而 agentic 多轮研究是输入密集型（每轮重发全部对话历史,
   65 题实测仅面板输入即 751 万 token），输入成本因此被结构性放大。
2. **工具成本 $41.86（23%）是 5 面板架构的固有成本**：5 个成员独立搜索，Exa 调用量
   约为 baseline 的 4 倍，与模型选型无关，换便宜模型也省不掉。
3. 若把 kimi-k3 从面板和 fuser 中移除（换 mimo/minimax 级价位），总倍数可从 2.56x
   立即降至约 1.4x——"最强配置"的成本溢价近半数系于一个模型的定价。
4. 估算口径：65 题实测量 ×(100/65) 线性外推；金融 20 题的消耗模式与非财务题略有
   差异，实际数字预计 ±15% 浮动。glm-5.2/deepseek-v4-pro/mimo-v2.5-pro 三项为
   同系列/同角色工作量代理，假设"干同样的活耗相近 token"。

---

## 四、口径与数据来源说明

1. 分数均为 `draco_rejudge.py`（judge=gemini-3.1-pro-preview）对最终答案按每题 rubric
   逐条（3条/请求）评出的加权分；每个 task_id 取最后一条非 failed 记录。
2. LLM 成本 = 每次 API 响应中真实记录的 input/output tokens × 0G router `/v1/models`
   公开单价（2026-10-12 抓取）。唯一例外：融合判官的 token 为估算值（字符数÷4，该角色
   未持久化真实 usage），占比 <1%，不影响结论。
3. 工具成本 = web_search 调用次数 × $0.007（Exa `auto` 模式公开价，$7/千次请求,
   highlights 随 /search 返回不额外计费）。web_fetch/bash/sec_facts 均为免费。
4. 失败重试产生的废弃调用未计入（两边都有少量，不改变方向性结论）。
5. 3-panel 的 100 题结果全部来自 `out/task_major_v2/`（单一来源）。5-panel 的 65 题
   基线分数以 `out/task_major_5panel/` 为主、`out/task_major_v2/` 兜底合并。
6. 已知方法论限制：(a) fuser 指令调优只作用于融合一侧（见第二节告警）；(b) 3-panel 早期
   曾存在 20 题"面板不足 3/3 即抢跑融合"的数据，已全部清除并以 `--require-full-panel`
   重跑，本文数字均为修复后的干净结果；(c) 5-panel 的 65 题与 3-panel 的 80/100 题
   任务集不同，直接对比时请使用第二节"三方同题 65 题"的列。
