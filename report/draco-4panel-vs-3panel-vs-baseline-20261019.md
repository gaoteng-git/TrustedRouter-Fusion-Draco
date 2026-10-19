# DRACO 评测：4面板 vs 3面板 vs Baseline（分数 + 成本）

更新日期：2026-10-19
网关：0G router（https://router-api.0g.ai/v1），分支 `fusion-prompt-mainlike`
打分判官：gemini-3.1-pro-preview（chunk=3 条标准/次，3 passes，reasoning_effort=high）

## 配置

| 配置 | 面板 | judge | fuser | 数据 |
|---|---|---|---|---|
| 4面板 | minimax-m3 + glm-5.3-flash + deepseek-v4-flash + mimo-v2.5-pro | minimax-m3 | kimi-k3（importance-ordered prompt） | `out/fusion4_balanced_full100/`、`out/mimo_panels_full100/` |
| 3面板 | minimax-m3 + glm-5.3-flash + deepseek-v4-flash | minimax-m3 | minimax-m3 | `out/task_major_v2/` |
| baseline | gpt-5.6-sol 单模型 | — | — | `out/task_major_v2/` |

面板成员与 baseline 均使用同一套 tooled agentic harness（`draco_agentic_solo.py`）；4面板复用
3面板的三份面板答案，`--require-full-panel`。

## 分数（三方均有分数的 98 题）

4面板缺 2 题，均为 kimi-k3 fuser 无输出：
- `ce335c0c`（转型正义题）：上游 provider 稳定拒绝（HTTP 400 "The upstream provider rejected
  the request"，两次运行共 10 次请求）。
- `e6fbba71`（美德税负计算题）：4 个面板答案全为空（finish=length），fuser 每次将 11.5k token
  全部耗在推理上，无可见正文。

`ce522bc2` 原因 mimo 面板 502 未融合，已用 `scripts/rerun_4panel_missing3.sh` 补跑（4p 73.9 /
3p 53.1 / baseline 49.9）。

| 配置 | 平均分 | vs baseline |
|---|---|---|
| **4面板** | **70.84** | **+3.76** |
| 3面板 | 69.33 | +2.25 |
| baseline | 67.08 | — |

| 对比 | 胜 / 负 / 平 | 胜率 |
|---|---|---|
| 4面板 vs baseline | 65 / 32 / 1 | **66.3%** |
| 3面板 vs baseline | 56 / 42 / 0 | 57.1% |
| 4面板 vs 3面板 | 62 / 31 / 5 | 63.3% |

（3面板 100 题全量：69.41 vs 67.27，胜 57/100，与 98 题口径一致。）

## 成本（同 98 题，假设面板每次都重新跑）

| 项目 | 4面板 | 3面板 | baseline |
|---|---|---|---|
| 面板 LLM | $26.49 | $13.71 | — |
| judge（minimax-m3，估算） | $0.68 | $0.58 | — |
| fuser | **$20.44**（kimi-k3） | $1.25（minimax-m3） | — |
| baseline LLM | — | — | $58.64 |
| **LLM 小计** | **$47.61** | **$15.54** | **$58.64** |
| Exa 搜索 | $30.48（4355 次） | $22.88（3269 次） | $10.46（1494 次） |
| **总成本** | **$78.10** | **$38.42** | **$69.10** |
| 每题 | $0.80 | $0.39 | $0.71 |
| 相对 baseline | 1.13x | 0.56x | 1x |

面板成员明细：

| 面板 | 单价 in/out（$/M） | 输入 token | 输出 token | LLM | 搜索 | Exa |
|---|---|---|---|---|---|---|
| minimax-m3 | 0.27 / 1.08 | 13.9M | 0.96M | $4.80 | 1178 | $8.25 |
| glm-5.3-flash | 0.143 / 0.501 | 15.0M | 1.77M | $3.03 | 949 | $6.64 |
| deepseek-v4-flash | 0.264 / 0.792 | 16.7M | 1.84M | $5.87 | 1142 | $7.99 |
| mimo-v2.5-pro | 0.652 / 1.305 | 17.9M | 0.84M | $12.79 | 1086 | $7.60 |

其他单价：kimi-k3 $3 / $15，gpt-5.6-sol $5 / $30（$/M，in/out）。

## 结论

- **3面板**：约一半成本（0.56x），高 2.25 分——性价比最优。
- **4面板**：分数最高（+3.76，胜率 66%），成本 1.13x baseline、约为 3面板的 2 倍。多出的约 $40
  中一半是 kimi-k3 fuser（单价 $15/M 输出，是 minimax-m3 fuser 的 16 倍），其余主要是 mimo 面板
  LLM 与多出的约 1100 次搜索。
- kimi-k3 fuser 可靠性问题：约一半融合 finish=length（推理吃掉 token 上限导致正文截断，几道题
  正文仅 600-3500 字符、得分 15-30），另有 2 题完全无输出。换 fuser 或调整 token/推理预算可能
  进一步拉开 4面板优势。

## 口径

1. 分数：每个 task_id 取最后一条有分数的记录；只比较三方都有分数的 98 题。
2. LLM 成本 = 每次调用记录的真实 input/output tokens × 0G `/v1/models` 公开单价（2026-10-19
   抓取）；gpt-5.6-sol 按无缓存全价。
3. judge token 为估算（未持久化真实 usage）：输出 = judge_chars/4，输入 ≈ fuser 输入 − judge 输出。
4. fuser 只计最终成功那次调用；同题内失败/空内容重试未计入（4面板 fuser 略偏低）。
5. Exa = web_search 次数 × $0.007（`auto` 模式 $7/千次）；web_fetch / bash 不计费。
6. 不含 gemini-3.1-pro 打分成本（三方相同，属评测开销）。
