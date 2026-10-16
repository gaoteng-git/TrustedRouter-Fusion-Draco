#!/usr/bin/env python3
"""Client-orchestrated TrustedRouter Fusion over pre-computed tooled panel solos.

The native gateway /fusion endpoint cannot give panels live tools (its panel runs
on frozen context -> ~40 on DRACO). OpenRouter's 64.7 comes from giving the panel
LIVE tools. So we reproduce Fusion client-side: the panel == our validated tooled
solos (gemini-flash + kimi + deepseek, full-100), then replicate the gateway's
exact judge->fuser pipeline (prompts copied verbatim from enclave fusion.go):

  panel reports -> Fusion judge (gemini-3.1-pro, compact JSON analysis)
               -> Opus-4.8 fuser (panel evidence primary + judge analysis guidance)

Writes ``trustedrouter.fusion_draco.replay.v1`` rows so scripts/draco_rejudge.py
scores them unchanged. No rubric is ever shown to judge or fuser (no leakage).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from trusted_router.evals import tr_sdk
from trusted_router.evals.agentic_tools import MODELS_WITHOUT_TEMPERATURE, strip_tool_markup
from trusted_router.evals.draco_replay import load_manifest
from trusted_router.evals.fusion_live import load_eval_key

REPLAY_SCHEMA = "trustedrouter.fusion_draco.replay.v1"

# Confirmed live against https://router-api.0g.ai/v1/chat/completions
# (2026-10-08): minimax-m3 hard-rejects `response_format: json_object` --
#   {"error":{"message":"failed to select provider: model minimax-m3 does
#   not support JSON mode (response_format: json_object)","type":
#   "invalid_request_error","code":"model_not_capable"}}
# -- even though this is exactly the model Prometheus 2.0's own production
# judge preset tries first (fusionPresetJudgeModelsForModel in
# quill-cloud-proxy's fusion.go: minimax-m3 primary, kimi-k3 fallback).
# TrustedRouter's own gateway presumably reaches minimax-m3 through a path
# that tolerates response_format (or strips it) -- 0G router's does not.
# Rather than swap the judge model to dodge this (which would no longer be
# testing Prometheus 2.0's real configuration), skip sending
# response_format for models known not to support it here, relying on
# JUDGE_SYSTEM's own "Return only JSON" instruction instead -- the same
# fix already applied for this identical model/identical issue in this
# project's other (0g-fusion-model-eval) codebase. Compared
# case-insensitively since 0G router resolves model ids case-insensitively.
JUDGE_MODELS_WITHOUT_JSON_MODE = {"minimax-m3", "minimax/minimax-m3"}

JUDGE_SYSTEM = (
    "You are the TrustedRouter Fusion judge. Compare panel responses and return "
    "compact JSON with keys consensus, contradictions, partial_coverage, "
    "unique_insights, blind_spots, and final_guidance. Do not write the final "
    "answer. Return only JSON; do not include chain-of-thought, hidden reasoning, "
    "or <think> blocks."
)
FINAL_INSTRUCTION = (
    "TrustedRouter Fusion panel answers and judge analysis follow. Use the panel "
    "answers as the primary evidence and the judge analysis as guidance to write "
    "the final answer for the original request. Return only the final visible "
    "answer. Do not include chain-of-thought, hidden reasoning, analysis, "
    "scratchpad text, <think> blocks, or internal model names -- unless the "
    "original request explicitly asks you to describe your own research "
    "process or approach (not merely because the subject matter itself "
    "concerns research methodology).\n\n"
    "Preserve detail -- do not compress for brevity. A longer, fully sourced "
    "answer is strongly preferred over a shorter summary that drops verifiable "
    "specifics. Do not omit a figure, date, name, example, or citation from the "
    "panel answers merely to shorten the final answer or make the prose read "
    "more cleanly.\n\n"
    "Keep citations inline, in the same form the panel answers used (URL or "
    "source name next to the claim it supports). Every specific factual claim, "
    "figure, or quote carried over from a panel answer must keep its original "
    "citation attached.\n\n"
    "Where panel sources conflict, use your own judgment to decide which "
    "position is more likely correct -- based on which is better corroborated, "
    "more specific, or better sourced -- and state your conclusion directly. "
    "You do not need to present every disagreement as unresolved. A claim made "
    "by only one panel source is yours to judge too: include it with "
    "attribution if it's plausible and adds real value, or leave it out if it "
    "seems unreliable or marginal -- don't include it as unattributed settled "
    "fact.\n\n"
    "Your response has a limited token budget (stated below, along with the "
    "total size of the source material). The instructions above on preserving "
    "detail and citations remain the default -- do not shorten the answer out "
    "of caution just because a budget is mentioned. Only if the fully detailed "
    "answer would clearly not fit in your stated budget should you reorder "
    "content by importance: write the direct answer to the original request "
    "and the most load-bearing facts, figures, and conclusions first; place "
    "secondary elaboration, caveats, and less critical supporting detail "
    "later. This way, if you are cut off before finishing, the core answer is "
    "already complete and usable, and only the least important material is "
    "lost."
)
# Deliberate deviation from the upstream (main branch / enclave fusion.go)
# wording "unless the user asked for methodology": confirmed live, a DiD
# econometrics task whose OWN subject matter is "compare these methodologies"
# made minimax-m3 read that clause as license to return the judge's raw
# analysis JSON (consensus/contradictions/... keys and all) verbatim as the
# "final answer" -- a real, reproduced failure, not a parsing/plumbing bug.
# The clause's likely original intent was narrower: only skip the
# no-analysis rule when the user asks the FUSER to explain ITS OWN process,
# not whenever the task's topic happens to be about methodology in general.
# This rewording targets exactly that gap.
#
# Three more paragraphs added (branch fusion-prompt-detail-fix, off the
# methodology fix above): a root-cause dive into 14 real fusion-losing tasks
# (see git history / conversation record) found the fuser (minimax-m3)
# systematically shrinks its own best panel answer by 40-90%, stripping
# nearly all inline citations and dropping whole named-source sections /
# roughly half of the panel's specific figures -- even when well under its
# token budget (finish_reason=stop). The "preserve detail" and "keep
# citations inline" paragraphs target that directly. The "where panel
# sources conflict" paragraph deliberately does NOT mandate mechanically
# presenting every contradiction as unresolved, or forcing a binary
# attribute-or-omit rule on single-source claims -- early drafts did, but
# that was judged too rigid: real synthesis value comes from the fuser
# making a credible call, not refusing to. The wording keeps that judgment
# call explicitly in the fuser's hands, only anchoring it to concrete
# criteria (corroboration, specificity, sourcing) instead of leaving it
# totally open-ended, given this same model has already shown a tendency to
# default to the laziest option (silently dropping content) when given
# unconstrained synthesis instructions.
#
# A fifth paragraph ("Your response has a limited token budget...") added
# later, on the same branch: of the tasks scored so far, a handful hit
# finish_reason=length (ran out of --fuser-max-tokens, currently 12000,
# mid-answer) and lose to baseline specifically because of what got cut off
# at the END, not because the content was wrong -- the model had no way to
# know it should front-load the most important material. This paragraph is
# explicitly scoped as a fallback ("only if...clearly not fit"), not a new
# default, so it doesn't re-introduce the premature-compression behavior the
# "preserve detail" paragraph was written to stop. --fuser-max-tokens is
# deliberately NOT raised further alongside this: baseline's own dominant
# code path (draco_agentic_solo.py's forced-synthesis call, taken in the
# large majority of baseline rows) caps at the same 12000, so matching that
# ceiling is what keeps this a fair comparison rather than just giving the
# fuser more room to write than baseline ever gets.


def _panel_evidence(panel: list[tuple[str, str]]) -> str:
    # Matches enclave fusion.go fusionPanelEvidence: "Panel answers:\n" then "\n[i] model=..\ntext\n".
    b = ["Panel answers:\n"]
    for i, (model, text) in enumerate(panel, start=1):
        b.append(f"\n[{i}] model={model}\n{text.strip()}\n")
    return "".join(b)


def _judge_user(problem: str, panel: list[tuple[str, str]]) -> str:
    evidence = _panel_evidence(panel)
    body = evidence[len("Panel answers:\n"):] if evidence.startswith("Panel answers:\n") else evidence
    return f"Original request summary:\n{problem}\n\nPanel responses:\n{body}"


def _post(client: tr_sdk.TrustedRouter, base_url: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    # Routes through the TrustedRouter SDK (auth, regional failover, 429/5xx
    # retries handled by the client). base_url/api_key are baked into the client.
    return tr_sdk.chat(client, body)


def _content(resp: dict[str, Any]) -> str:
    return (resp.get("choices", [{}])[0].get("message", {}) or {}).get("content") or ""


def _clean_judge_json(raw: str) -> str:
    """Clean a judge model's raw output before splicing it into the fuser's
    prompt or persisting it. Confirmed live (8-sample check, 2026-10-10):
    the judge (minimax-m3) leaks a <think> block exactly like it does as a
    panel member / fuser (see strip_tool_markup's own docstring) AND wraps
    its JSON in a markdown code fence (```json ... ```), despite
    JUDGE_SYSTEM's "Return only JSON" instruction covering neither. Of 8 raw
    samples: 1 was already clean, 4 parsed as valid JSON once <think> and
    the fence were stripped, and 3 were genuinely truncated mid-answer
    (hit --judge-max-tokens before finishing) -- those still won't parse
    after this cleanup, because there's nothing left to clean; that's a
    real generation-truncation problem (raise --judge-max-tokens), not
    something text post-processing can fix."""
    text = strip_tool_markup(raw)
    t = text.strip()
    if t.startswith("```"):
        newline_idx = t.find("\n")
        t = t[newline_idx + 1:] if newline_idx != -1 else t[3:]
    if t.rstrip().endswith("```"):
        t = t.rstrip()[:-3]
    return t.strip()


_NO_CONTENT_PLACEHOLDER = (
    "[no usable answer -- this panel member's generation was truncated "
    "before producing a final response]"
)


def _transcript_path(output: Path) -> Path:
    return output.with_name(output.stem + ".transcript.txt")


def _format_fusion_transcript(
    *, task_id: str, domain: str, config_id: str, panel_labels: list[str],
    judge_model: str, judge_system: str, judge_user: str,
    judge_raw: str | None, judge_json: str, judge_from_cache: bool,
    judge_elapsed_ms: int | None,
    fuser_problem: str, fuser_final_user: str,
    fuser_attempts_detail: list[dict[str, Any]],
    final_content: str,
) -> str:
    """Human-readable, call-ordered companion to one jsonl fusion row -- NEVER
    read back by any script (--resume/_load_panel/scoring all still parse the
    single-line jsonl exactly as before). Shows the judge's and fuser's FULL
    input (the real system/user prompt text sent) and FULL output (the real
    raw response text), which the jsonl row never persisted (only judge_json
    -- the cleaned judge output -- and final.content -- the winning fuser
    output -- survive there; the raw/uncleaned judge response, the fuser's
    exact input text, and any losing retry attempts' content did not)."""
    lines: list[str] = [
        "=" * 100,
        f"task_id: {task_id}",
        f"domain:  {domain}",
        f"config:  {config_id}",
        f"panel used: {panel_labels}",
        "-" * 100,
        "[JUDGE]",
        f"model: {judge_model}" + ("  (reused from --judge-cache; no fresh call made this run)"
                                    if judge_from_cache else f"  elapsed_ms={judge_elapsed_ms}"),
        "  >> full input (system):",
    ]
    for ln in judge_system.splitlines():
        lines.append(f"      {ln}")
    lines.append("  >> full input (user):")
    for ln in judge_user.splitlines():
        lines.append(f"      {ln}")
    lines.append("  >> full raw output (before <think>/code-fence cleanup):")
    for ln in (judge_raw or "(not called this run -- judge_cache hit)").splitlines() or [""]:
        lines.append(f"      {ln}")
    lines.append("  >> cleaned judge_json (what the fuser actually received):")
    for ln in judge_json.splitlines() or [""]:
        lines.append(f"      {ln}")
    lines.append("-" * 100)
    lines.append("[FUSER]")
    lines.append("  >> full input (message 1 -- original task):")
    for ln in fuser_problem.splitlines():
        lines.append(f"      {ln}")
    lines.append("  >> full input (message 2 -- instruction + panel evidence + judge analysis + budget note):")
    for ln in fuser_final_user.splitlines():
        lines.append(f"      {ln}")
    for a in fuser_attempts_detail:
        lines.append(f"  >> attempt {a['attempt']} model={a['model']} elapsed_ms={a.get('elapsed_ms')}")
        if a.get("error"):
            lines.append(f"      ERROR: {a['error']}")
            continue
        lines.append(f"      finish_reason={a.get('finish_reason')}")
        lines.append("      full output:")
        for ln in (a.get("content") or "").splitlines() or ["      (empty)"]:
            lines.append(f"          {ln}")
    lines.append("-" * 100)
    lines.append("[final content]")
    lines.append(final_content or "(empty)")
    lines.append("")
    return "\n".join(lines)


def _load_panel(path: Path) -> dict[str, str]:
    """task_id -> final report text (last non-failed row wins).

    A row counts as "this panel member answered" once the call actually
    completed (has a `final` block) and wasn't a hard API failure (no
    `status: "failed"`, which only `_failure_row`-style rows carry and
    which never have a `final` block at all) -- even when the model's own
    verbosity ate its whole token budget and left `final.content` empty
    (finish_reason="length", reasoning/think populated but no answer
    text). That is the model being long-winded, not a transient error, so
    it must not block --require-full-panel forever waiting for a retry
    that will never come (status-less "ok" rows are never re-attempted by
    --resume). Confirmed this already happened, unremarked, in the
    original 3-panel run (out/task_major_v2/panel-deepseek-v4-flash.jsonl
    and panel-glm-5.3-flash.jsonl both have multiple such rows) -- it just
    didn't matter there because that run's coverage check was any(), not
    all(). A placeholder is used as evidence in this case so the
    judge/fuser see an explicit "no answer" rather than a blank panel
    block that could read as a formatting bug.
    """
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("status") == "failed":
            continue
        final = r.get("final")
        if final is None:
            continue
        t = r.get("task_id")
        if not t:
            continue
        c = final.get("content")
        out[t] = c if isinstance(c, str) and c.strip() else _NO_CONTENT_PLACEHOLDER
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, default=Path("artifacts/fusion-draco/draco-full-100.manifest.json"))
    p.add_argument("--panel", action="append", required=True,
                   help="model_label=replay_path. Repeat for each panel member.")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--config-id", default="fusion_client_budget_opus")
    p.add_argument("--judge-model", default="google/gemini-3.1-pro-preview")
    p.add_argument("--fuser-model", default="anthropic/claude-opus-4.8")
    p.add_argument("--fallback-fuser-model", default=None,
                   help="If the primary fuser returns (near-)empty output — e.g. a silent "
                        "refusal — re-synthesize the same panel with this model instead.")
    p.add_argument("--judge-max-tokens", type=int, default=3000)
    # Raised from 8000 -> 12000 (matches draco_agentic_solo.py's own
    # synthesis_max_tokens default) alongside FINAL_INSTRUCTION's new
    # "preserve detail / keep citations inline" paragraphs: those paragraphs
    # ask the fuser to write a longer, fully-cited answer, which needs more
    # output budget -- otherwise the fix would just trade "lossy summary"
    # for "truncated mid-answer" (already observed hitting the old 8000
    # ceiling, finish_reason=length, on at least one task under the OLD,
    # shorter-by-default instruction).
    p.add_argument("--fuser-max-tokens", type=int, default=12000)
    p.add_argument("--judge-provider", default=None,
                   help="Force a provider for the judge model (OpenRouter-shape provider.only), "
                        "e.g. tinfoil for GLM-5.2 so the Z.ai host censorship doesn't blank the judge.")
    p.add_argument("--judge-providers", default=None,
                   help="Comma-separated provider list; spread the judge's calls across them "
                        "deterministically by task id (parallelizes the judge while keeping each task's "
                        "judge provider stable across runs). Overrides --judge-provider.")
    p.add_argument("--fuser-provider", default=None,
                   help="Force a provider for the fuser model, e.g. tinfoil for GLM-5.2.")
    p.add_argument("--fuser-provider-frac", type=float, default=1.0,
                   help="Route only this fraction of tasks (stable hash split by task id) through "
                        "--fuser-provider; the rest use default routing. e.g. 0.5 to load-test a provider on half.")
    p.add_argument("--fuser-providers", default=None,
                   help="Comma-separated provider list; spread the primary fuser's calls across them "
                        "deterministically by task id (parallelizes load while keeping each task pinned to "
                        "the same provider across runs, so comparisons stay clean). Overrides --fuser-provider.")
    p.add_argument("--judge-cache", default=None,
                   help="Shared JSONL cache of judge analyses keyed by (judge_model, judge_provider, "
                        "task). Reused across synthesizer runs on the SAME panel — point every cell of a "
                        "judge×synthesizer grid at one cache so each judge analysis is computed once.")
    p.add_argument("--base-url", default="https://api-us-central1.quillrouter.com/v1")
    p.add_argument("--api-key-name", default="TR_FUSION_EVAL_API_KEY")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--timeout-seconds", type=float, default=600.0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--require-full-panel", action="store_true",
                   help="Only fuse a task once EVERY configured --panel member has answered it "
                        "(default: fuse as soon as ANY ONE has, see the any()-vs-all() comment "
                        "below). Use this for an experiment where partial-coverage fusion would "
                        "not be a faithful measurement of the full panel (e.g. a 5-panel config "
                        "where 2 slow/flaky new members haven't caught up yet) -- tasks missing "
                        "even one member are simply left pending, picked up automatically by a "
                        "later --resume re-run once that member's result exists.")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args(argv)

    panels: dict[str, dict[str, str]] = {}
    labels: list[str] = []
    for spec in args.panel:
        label, _, path = spec.partition("=")
        panels[label] = _load_panel(Path(path))
        labels.append(label)

    manifest = load_manifest(args.manifest)
    tasks = list(manifest.tasks)
    if args.limit:
        tasks = tasks[: args.limit]
    # Fuse a task as soon as ANY configured panel member answered it --
    # not only when every single one did. A task-major pipeline calling
    # this per-task (one panel member failing, or coming back with
    # genuinely empty content, is a real and not-uncommon outcome -- see
    # scripts/draco_task_major_pipeline.py's own "prerequisite missing"
    # skip logic) would otherwise lose that task's fusion entirely even
    # when 2 of 3 panel members produced a perfectly usable answer.
    # run_one() below only includes the labels that actually have this
    # task_id in `panel`, so the judge/fuser only ever see the panel
    # members that really answered, never a KeyError on a missing one.
    #
    # --require-full-panel flips this to all(): for an experiment
    # comparing a SPECIFIC N-panel config (e.g. 5-panel) against baseline,
    # fusing a task on 2/5 members isn't a faithful instance of that
    # config -- it's silently a different, smaller config. Leaving such a
    # task pending until every member has a real answer (--resume retries
    # it later) keeps every fused row an actual N/N-panel result.
    coverage_check = all if args.require_full_panel else any
    tasks = [t for t in tasks if coverage_check(t.id in panels[l] for l in labels)]

    completed: set[str] = set()
    if args.resume and args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("status") != "failed" and row.get("task_id"):
                    completed.add(row["task_id"])
    pending = [t for t in tasks if t.id not in completed]

    print(f"panel members: {labels}")
    coverage_desc = "ALL panel answers" if args.require_full_panel else "at least 1 panel answer"
    print(f"tasks with {coverage_desc}: {len(tasks)}  pending: {len(pending)}")
    print(f"fuser: {args.fuser_model}  judge: {args.judge_model}  config: {args.config_id}")
    print(f"output: {args.output}")
    if not args.execute:
        print("dry run; add --execute")
        return 0

    api_key = load_eval_key(args.api_key_name)
    if not api_key:
        print(f"missing key {args.api_key_name}")
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Judge analysis depends only on (panel, judge_model, judge_provider) — not the synthesizer.
    # A shared cache lets a judge×synthesizer grid compute each judge pass once and reuse it.
    judge_cache: dict[str, str] = {}
    cache_lock = threading.Lock()
    if args.judge_cache and Path(args.judge_cache).exists():
        for line in Path(args.judge_cache).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                jk = f"{rec['judge_model']}\t{rec.get('judge_provider') or ''}\t{rec['task_id']}"
                judge_cache[jk] = rec["judge_json"]
        print(f"judge cache: loaded {len(judge_cache)} analyses from {args.judge_cache}")

    def run_one(task) -> tuple[dict[str, Any], str]:
        client = tr_sdk.make_client(base_url=args.base_url, api_key=api_key, timeout=args.timeout_seconds)
        run_started = time.perf_counter()
        try:
            # Only the labels that actually answered THIS task -- guaranteed
            # non-empty by the `any(...)` filter above, but may be a strict
            # subset of `labels` (see that filter's comment).
            present_labels = [l for l in labels if task.id in panels[l]]
            panel = [(l, panels[l][task.id]) for l in present_labels]
            # 1) Fusion judge analysis (cached per judge_model/provider/task — reused across synthesizers)
            jkey = f"{args.judge_model}\t{args.judge_provider or ''}\t{task.id}"
            judge_json = judge_cache.get(jkey)
            judge_from_cache = judge_json is not None
            judge_elapsed_ms = None
            judge_user_text = _judge_user(task.problem, panel)
            judge_raw: str | None = None
            if judge_json is None:
                judge_started = time.perf_counter()
                judge_body = {
                    "model": args.judge_model, "max_tokens": args.judge_max_tokens,
                    "messages": [
                        {"role": "system", "content": JUDGE_SYSTEM},
                        {"role": "user", "content": judge_user_text},
                    ],
                }
                if args.judge_model.lower() not in JUDGE_MODELS_WITHOUT_JSON_MODE:
                    judge_body["response_format"] = {"type": "json_object"}
                jprov = args.judge_provider
                if args.judge_providers:
                    jprovs = [x for x in args.judge_providers.split(",") if x]
                    jprov = jprovs[int(hashlib.md5(task.id.encode()).hexdigest(), 16) % len(jprovs)]
                if jprov:
                    judge_body["provider"] = {"only": [jprov], "allow_fallbacks": False}
                judge_raw = _content(_post(client, args.base_url, api_key, judge_body))
                judge_json = _clean_judge_json(judge_raw)
                judge_elapsed_ms = int((time.perf_counter() - judge_started) * 1000)
                if args.judge_cache:
                    with cache_lock:
                        judge_cache[jkey] = judge_json
                        with open(args.judge_cache, "a", encoding="utf-8") as cf:
                            cf.write(json.dumps({"judge_model": args.judge_model,
                                                 "judge_provider": args.judge_provider,
                                                 "task_id": task.id, "judge_json": judge_json}) + "\n")
            # 2) fuser (primary, with optional fallback when it returns near-empty —
            #    e.g. GLM-5.2 silently refusing politically restricted panel content)
            evidence_block = _panel_evidence(panel)
            # Rough chars/4 token estimate -- 0G router exposes no tokenizer
            # endpoint, and this is only for the model's own budgeting sense,
            # not an exact accounting.
            evidence_tokens_est = (len(evidence_block) + len(judge_json)) // 4
            budget_note = (
                f"\n\nFor reference: the panel answers and judge analysis above total "
                f"approximately {evidence_tokens_est} tokens of source material. Your own "
                f"response budget for this answer is approximately {args.fuser_max_tokens} tokens."
            )
            final_user = (
                FINAL_INSTRUCTION + "\n\n" + evidence_block + "\n\nJudge analysis JSON:\n" + judge_json
                + budget_note
            )

            def _fuse(model: str, pin: bool = True) -> tuple[str, dict[str, Any], int]:
                # Mirror enclave fusion.go fusionFinalRequest: reuse the original request (the bare
                # task as a user message) and append ONLY the instruction+panel+judge user message.
                # No persona system prompt — that was ours, not TrustedRouter's / OpenRouter's.
                body = {
                    "model": model, "max_tokens": args.fuser_max_tokens,
                    "messages": [
                        {"role": "user", "content": task.problem},
                        {"role": "user", "content": final_user},
                    ],
                }
                # See MODELS_WITHOUT_TEMPERATURE's own docstring (agentic_tools.py)
                # -- kimi-k3 hard-rejects this param on 0G router. Not currently
                # reachable (fuser_model is always minimax-m3 in the active
                # configs), but FUSION_PROMETHEUS20 sets fuser_model=kimi-k3 and
                # is kept defined for re-enabling later -- fix it here too so
                # that doesn't silently regress when it comes back.
                if model.lower() not in MODELS_WITHOUT_TEMPERATURE:
                    body["temperature"] = 0.2
                prov = None
                if pin and model == args.fuser_model:  # pin only the primary fuser, never the fallback model
                    if args.fuser_providers:
                        provs = [x for x in args.fuser_providers.split(",") if x]
                        prov = provs[int(hashlib.md5(task.id.encode()).hexdigest(), 16) % len(provs)]
                    else:
                        prov = args.fuser_provider
                        if prov and args.fuser_provider_frac < 1.0:
                            bucket = int(hashlib.md5(task.id.encode()).hexdigest(), 16) % 1000
                            if bucket >= args.fuser_provider_frac * 1000:
                                prov = None  # this task uses default routing
                if prov:
                    body["provider"] = {"only": [prov], "allow_fallbacks": False}
                fuse_started = time.perf_counter()
                resp = _post(client, args.base_url, api_key, body)
                return strip_tool_markup(_content(resp)), resp, int((time.perf_counter() - fuse_started) * 1000)

            fuser_used = args.fuser_model
            fell_back = False
            fuser_attempts: list[dict[str, Any]] = []
            fuser_attempts_detail: list[dict[str, Any]] = []
            # STICK WITH GLM. First attempt uses the deterministic pinned provider (spreads load
            # across the GLM fleet); subsequent attempts drop the pin and let the gateway route to
            # ANY GLM provider, retried with backoff. A loaded/failing/balance-out provider must
            # never leave the synthesis empty — we keep retrying GLM, not swap models.
            content, fr, fuser_elapsed_ms = "", {}, None
            for attempt in range(5):
                attempt_started = time.perf_counter()
                try:
                    content, fr, fuser_elapsed_ms = _fuse(args.fuser_model, pin=(attempt == 0))
                    fuser_attempts.append({"attempt": attempt, "model": args.fuser_model,
                                            "elapsed_ms": fuser_elapsed_ms, "content_chars": len(content)})
                    fuser_attempts_detail.append({
                        "attempt": attempt, "model": args.fuser_model, "elapsed_ms": fuser_elapsed_ms,
                        "content": content, "finish_reason": fr.get("choices", [{}])[0].get("finish_reason"),
                    })
                    if len(content.strip()) >= 50:
                        break
                except Exception as exc:  # noqa: BLE001 - provider error; retry GLM via open routing
                    content, fr = "", {}
                    attempt_elapsed_ms = int((time.perf_counter() - attempt_started) * 1000)
                    fuser_attempts.append({"attempt": attempt, "model": args.fuser_model,
                                            "elapsed_ms": attempt_elapsed_ms,
                                            "content_chars": 0, "error": True})
                    fuser_attempts_detail.append({"attempt": attempt, "model": args.fuser_model,
                                                   "elapsed_ms": attempt_elapsed_ms, "error": str(exc)[:400]})
                time.sleep(1.5 * (attempt + 1))
            # Cross-model fallback ONLY if one is explicitly configured (we do not pass one, to
            # keep the synthesizer a pure GLM measurement).
            if len(content.strip()) < 50 and args.fallback_fuser_model:
                fuser_used = args.fallback_fuser_model
                fell_back = True
                content, fr, fuser_elapsed_ms = _fuse(args.fallback_fuser_model, pin=False)
                fuser_attempts.append({"attempt": len(fuser_attempts), "model": fuser_used,
                                        "elapsed_ms": fuser_elapsed_ms, "content_chars": len(content)})
                fuser_attempts_detail.append({
                    "attempt": len(fuser_attempts_detail), "model": fuser_used, "elapsed_ms": fuser_elapsed_ms,
                    "content": content, "finish_reason": fr.get("choices", [{}])[0].get("finish_reason"),
                })
            usage = fr.get("usage") or {}
            transcript = _format_fusion_transcript(
                task_id=task.id, domain=task.domain, config_id=args.config_id,
                panel_labels=present_labels, judge_model=args.judge_model,
                judge_system=JUDGE_SYSTEM, judge_user=judge_user_text,
                judge_raw=judge_raw, judge_json=judge_json, judge_from_cache=judge_from_cache,
                judge_elapsed_ms=judge_elapsed_ms, fuser_problem=task.problem,
                fuser_final_user=final_user, fuser_attempts_detail=fuser_attempts_detail,
                final_content=content,
            )
            return {
                "schema": REPLAY_SCHEMA, "config_id": args.config_id, "task_id": task.id,
                "domain": task.domain, "task": task.cache_dict(),
                "fusion": {"panel": present_labels, "judge_model": args.judge_model,
                           "judge_chars": len(judge_json),
                           # Raw judge analysis text, saved unconditionally (not just when
                           # --judge-cache is set) -- previously only judge_chars (a length)
                           # survived to disk and the actual judge_json was discarded right
                           # after building the fuser prompt, making it impossible to check,
                           # after the fact, whether a terse/weak judge analysis contributed to
                           # a lossy fuser rewrite. Same text the fuser itself was given as
                           # "Judge analysis JSON:\n<this>" in final_user.
                           "judge_json": judge_json,
                           "fuser_model": args.fuser_model,
                           "fallback_fuser_model": args.fallback_fuser_model,
                           "fuser_used": fuser_used, "fell_back": fell_back,
                           # None when this task's judge analysis came from --judge-cache (no
                           # real call made this run); otherwise the one real judge call's
                           # wall-clock time.
                           "judge_elapsed_ms": judge_elapsed_ms,
                           # Wall-clock time of the WINNING fuser attempt only (the one whose
                           # content is in `final.content`) -- excludes retry/backoff overhead.
                           "fuser_elapsed_ms": fuser_elapsed_ms,
                           # Every fuser attempt (including failed/discarded retries), so retry
                           # overhead can be told apart from genuine synthesis latency.
                           "fuser_attempts": fuser_attempts},
                "final": {"content": content, "finish_reason": fr.get("choices", [{}])[0].get("finish_reason"),
                          "model": fuser_used, "input_tokens": usage.get("prompt_tokens"),
                          "output_tokens": usage.get("completion_tokens"),
                          # Total wall-clock time for this task's run_one() call (judge + ALL
                          # fuser attempts, including retries/backoff sleeps) -- same convention
                          # as draco_agentic_solo.py's final.elapsed_ms (total time to produce
                          # this row's answer), so fusion- and baseline-latency are comparable.
                          "elapsed_ms": int((time.perf_counter() - run_started) * 1000),
                          "http_status": 200, "request_id": fr.get("id")},
            }, transcript
        except Exception as exc:  # noqa: BLE001
            row = {"schema": REPLAY_SCHEMA, "status": "failed", "config_id": args.config_id,
                   "task_id": task.id, "domain": task.domain,
                   "error_type": type(exc).__name__, "error": str(exc)[:400]}
            transcript = "\n".join([
                "=" * 100, f"task_id: {task.id}", f"domain:  {task.domain}",
                f"config:  {args.config_id}", f"STATUS: FAILED ({type(exc).__name__})",
                str(exc)[:400], "",
            ])
            return row, transcript
        finally:
            client.close()

    fh = args.output.open("a" if args.resume else "w", encoding="utf-8")
    transcript_fh = _transcript_path(args.output).open("a" if args.resume else "w", encoding="utf-8")
    done = 0
    total = len(pending)
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, total or 1))) as ex:
        futures: dict[Future, Any] = {}
        nxt = 0

        def submit():
            nonlocal nxt
            if nxt < total:
                futures[ex.submit(run_one, pending[nxt])] = pending[nxt]
                nxt += 1

        for _ in range(min(args.workers, total)):
            submit()
        while futures:
            ready, _ = wait(futures, return_when=FIRST_COMPLETED)
            for fut in ready:
                futures.pop(fut)
                row, transcript = fut.result()
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                fh.flush()
                transcript_fh.write(transcript)
                transcript_fh.flush()
                done += 1
                fin = row.get("final") or {}
                print(f"fusion {done}/{total} task={str(row.get('task_id'))[:8]} "
                      f"status={row.get('status','ok')} clen={len(fin.get('content') or '')} "
                      f"finish={fin.get('finish_reason')}")
                submit()
    fh.close()
    transcript_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
