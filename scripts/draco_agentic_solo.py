#!/usr/bin/env python3
"""Run a DRACO solo model agentically with live tools (web_search/web_fetch/bash).

This replicates OpenRouter's DRACO harness client-side: the model is given the
task problem (NO frozen context) plus the three tools, and iteratively searches /
fetches / computes through the gateway-as-plain-proxy until it writes a final
report. Writes replay rows in ``trustedrouter.fusion_draco.replay.v1`` so the
existing ``scripts/draco_rejudge.py`` can score them unchanged.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from trusted_router.evals import tr_sdk
from trusted_router.evals.agentic_tools import (
    DRACO_AGENTIC_SYSTEM_PROMPT,
    DEFAULT_BASH_IMAGE,
    ExaCircuitBreakerTripped,
    build_tool_executors,
    run_agentic_completion,
)
from trusted_router.evals.draco import DracoTask
from trusted_router.evals.draco_replay import load_manifest
from trusted_router.evals.exa import ExaSearchClient
from trusted_router.evals.fusion_live import load_eval_key

REPLAY_SCHEMA = "trustedrouter.fusion_draco.replay.v1"
KEY_NAMES = (
    "TR_FUSION_EVAL_API_KEY",
    "TR_API_KEY",
    "TRUSTEDROUTER_API_KEY",
    "TR_SMOKE_API_KEY",
    "TR_API_KEY_FOR_SELF_HEAL",
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path,
                   default=Path("artifacts/fusion-draco/draco-non-financial-80.manifest.json"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--model", default="deepseek/deepseek-v4-pro")
    p.add_argument("--config-id", default="solo_deepseek_v4_pro_tooled")
    p.add_argument("--task-id", action="append", default=None,
                   help="8-char prefix or full uuid. Repeatable.")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--max-tool-calls", type=int, default=16)
    p.add_argument("--max-tokens", type=int, default=8000)
    p.add_argument("--synthesis-max-tokens", type=int, default=12000)
    p.add_argument("--force-first-tool", action="store_true",
                   help="Force at least one tool call (for reluctant tool-users like gemini-flash).")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--reasoning-effort", default=None, choices=(None, "low", "high"))
    p.add_argument("--no-bash", action="store_true", help="Disable the bash tool.")
    p.add_argument("--doc-parser", default="llamaparse", choices=("llamaparse", "markitdown", "plain"),
                   help="web_fetch document parser chain (llamaparse=cached LlamaParse first).")
    p.add_argument("--no-sec-facts", action="store_true", help="Disable the EDGAR XBRL sec_facts tool.")
    p.add_argument("--bash-image", default=DEFAULT_BASH_IMAGE)
    p.add_argument("--base-url", default="https://api-us-central1.quillrouter.com/v1")
    p.add_argument("--api-key-name", default=None,
                   help="Use this specific key (e.g. CHATGPT_API_KEY) instead of the TR keys, "
                        "for routing a model directly to its provider.")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--timeout-seconds", type=float, default=600.0)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args(argv)

    if args.workers < 1 or args.workers > 8:
        print("workers must be 1-8")
        return 2

    manifest = load_manifest(args.manifest)
    tasks = _select_tasks(manifest.tasks, task_filter=args.task_id, limit=args.limit)
    if not tasks:
        print("no tasks matched")
        return 2

    completed = _completed_ids(args.output) if args.resume else set()
    pending = [t for t in tasks if t.id not in completed]

    print(f"tasks: {len(tasks)}  pending: {len(pending)}")
    print(f"model: {args.model}  config_id: {args.config_id}")
    print(f"tools: web_search, web_fetch{'' if args.no_bash else ', bash'}  max_tool_calls: {args.max_tool_calls}")
    print(f"base_url: {args.base_url}")
    print(f"output: {args.output}")
    if not args.execute:
        print("dry run only; add --execute to call the gateway/Exa/docker.")
        return 0

    if args.api_key_name:
        api_key = load_eval_key(args.api_key_name)
    else:
        api_key = next((load_eval_key(n) for n in KEY_NAMES if load_eval_key(n)), None)
    if not api_key:
        print("missing API key")
        return 2
    exa_key = load_eval_key("EXA_API_KEY")
    if not exa_key:
        print("missing EXA_API_KEY")
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a" if args.resume else "w", encoding="utf-8") as fh, \
         _transcript_path(args.output).open("a" if args.resume else "w", encoding="utf-8") as transcript_fh:
        _run(pending, args=args, api_key=api_key, exa_key=exa_key, fh=fh, transcript_fh=transcript_fh)
    return 0


def _run(pending: list[DracoTask], *, args: argparse.Namespace, api_key: str, exa_key: str,
         fh: Any, transcript_fh: Any) -> None:
    total = len(pending)
    if args.workers == 1:
        for i, task in enumerate(pending, start=1):
            row, result = _run_one(task, args=args, api_key=api_key, exa_key=exa_key)
            _write(fh, transcript_fh, row, result, index=i, total=total)
        return
    max_workers = min(args.workers, total)
    ex = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures: dict[Future[tuple[dict[str, Any], Any | None]], DracoTask] = {}
        nxt = 0
        done = 0

        def submit() -> None:
            nonlocal nxt
            if nxt >= total:
                return
            task = pending[nxt]
            nxt += 1
            futures[ex.submit(_run_one, task, args=args, api_key=api_key, exa_key=exa_key)] = task

        for _ in range(max_workers):
            submit()
        while futures:
            ready, _ = wait(futures, return_when=FIRST_COMPLETED)
            for fut in ready:
                task = futures.pop(fut)
                try:
                    row, result = fut.result()
                except ExaCircuitBreakerTripped:
                    # Must abort the whole run, not just fail this one task.
                    raise
                except Exception as exc:  # noqa: BLE001
                    row, result = _failure_row(task, args, exc), None
                done += 1
                _write(fh, transcript_fh, row, result, index=done, total=total)
                submit()
    finally:
        ex.shutdown(wait=True)


def _run_one(task: DracoTask, *, args: argparse.Namespace, api_key: str,
             exa_key: str) -> tuple[dict[str, Any], Any | None]:
    client = tr_sdk.make_client(base_url=args.base_url, api_key=api_key, timeout=args.timeout_seconds)
    exa_client = ExaSearchClient(exa_key)
    try:
        schemas, executors = build_tool_executors(
            task, exa_client=exa_client, bash_image=args.bash_image, enable_bash=not args.no_bash,
            enable_sec_facts=not args.no_sec_facts, doc_parser=args.doc_parser,
        )
        result = run_agentic_completion(
            client=client,
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            system_prompt=DRACO_AGENTIC_SYSTEM_PROMPT,
            user_prompt=f"Research task:\n{task.problem}",
            tool_schemas=schemas,
            executors=executors,
            max_tool_calls=args.max_tool_calls,
            max_tokens=args.max_tokens,
            synthesis_max_tokens=args.synthesis_max_tokens,
            temperature=args.temperature,
            reasoning_effort=args.reasoning_effort,
            force_first_tool=args.force_first_tool,
        )
        row = {
            "schema": REPLAY_SCHEMA,
            "config_id": args.config_id,
            "task_id": task.id,
            "domain": task.domain,
            "task": task.cache_dict(),
            "agentic": {
                "tool_calls_made": result.tool_calls_made,
                "steps": result.steps,
                "truncated_loop": result.truncated_loop,
                "tools": [
                    {"name": r.name, "args": r.args, "result_chars": r.result_chars,
                     "error": r.error, "elapsed_ms": r.elapsed_ms}
                    for r in result.tool_records
                ],
                # One entry per gateway /chat/completions call (research-phase
                # rounds plus, if reached, the synthesis call). Lets latency
                # analysis split `final.elapsed_ms` into LLM-wait vs tool-wait
                # and compare per-round latency across panel/baseline models.
                "llm_calls": [
                    {"phase": c.phase, "step": c.step, "elapsed_ms": c.elapsed_ms,
                     "input_tokens": c.input_tokens, "output_tokens": c.output_tokens}
                    for c in result.llm_calls
                ],
            },
            "final": {
                "content": result.content,
                # Saved but NOT used anywhere yet (not a fallback when
                # `content` is empty, not fed to judge/fuser) -- see
                # AgenticResult.reasoning's own docstring for why this
                # exists. Raw reasoning_content from whichever single call
                # produced this result, or None if the model/response
                # didn't carry one.
                "reasoning": result.reasoning,
                "finish_reason": result.finish_reason,
                "model": args.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "elapsed_ms": result.elapsed_ms,
                "http_status": 200,
                "request_id": None,
            },
        }
        return row, result
    except ExaCircuitBreakerTripped:
        # Must abort the whole run, not just fail this one task -- see
        # agentic_tools.ExaCircuitBreakerTripped's own docstring.
        raise
    except Exception as exc:  # noqa: BLE001
        return _failure_row(task, args, exc), None
    finally:
        exa_client.close()
        client.close()


def _failure_row(task: DracoTask, args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    return {
        "schema": REPLAY_SCHEMA,
        "status": "failed",
        "config_id": args.config_id,
        "task_id": task.id,
        "domain": task.domain,
        "error_type": type(exc).__name__,
        "error": str(exc)[:500],
    }


def _transcript_path(output: Path) -> Path:
    return output.with_name(output.stem + ".transcript.txt")


def _format_transcript(row: dict[str, Any], result: Any | None) -> str:
    """Human-readable, call-ordered companion to one jsonl replay row --
    NEVER read back by any script (--resume/_load_panel/find_row_for_task
    all still parse the single-line jsonl exactly as before). Groups each
    LLM call together with the tool calls it triggered, in true call order,
    showing each tool's full args and full output text (the jsonl row only
    keeps result_chars, not the text itself) -- then the final answer.
    """
    lines: list[str] = [
        "=" * 100,
        f"task_id: {row.get('task_id')}",
        f"domain:  {row.get('domain')}",
        f"config:  {row.get('config_id')}",
    ]
    if row.get("status") == "failed" or result is None:
        lines.append(f"STATUS: FAILED ({row.get('error_type')})")
        lines.append(row.get("error") or "")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        f"finish_reason={result.finish_reason}  steps={result.steps}  "
        f"tool_calls_made={result.tool_calls_made}  truncated_loop={result.truncated_loop}"
    )
    tools_by_step: dict[int, list[Any]] = {}
    for t in result.tool_records:
        tools_by_step.setdefault(t.step, []).append(t)
    for call in result.llm_calls:
        lines.append("-" * 100)
        lines.append(
            f"[round {call.step}] phase={call.phase}  input_tokens={call.input_tokens}  "
            f"output_tokens={call.output_tokens}  elapsed_ms={call.elapsed_ms}"
        )
        lines.append("  >> full input (messages sent to the LLM this call):")
        for i, m in enumerate(call.input_messages):
            tc = m.get("tool_calls")
            header = f"    [{i}] role={m.get('role')}"
            if m.get("tool_call_id"):
                header += f" tool_call_id={m.get('tool_call_id')}"
            if tc:
                header += f" tool_calls={json.dumps(tc, ensure_ascii=False)}"
            lines.append(header)
            for ln in (m.get("content") or "").splitlines() or [""]:
                lines.append(f"        {ln}")
        lines.append("  >> full output (raw LLM response message this call):")
        out = call.output_message or {}
        if out.get("tool_calls"):
            lines.append(f"    tool_calls={json.dumps(out['tool_calls'], ensure_ascii=False)}")
        if out.get("reasoning_content"):
            lines.append("    reasoning_content:")
            for ln in str(out["reasoning_content"]).splitlines():
                lines.append(f"        {ln}")
        lines.append("    content:")
        for ln in (out.get("content") or "").splitlines() or ["    (empty)"]:
            lines.append(f"        {ln}")
        for t in tools_by_step.get(call.step, []):
            lines.append(f"  tool: {t.name}")
            lines.append(f"  args: {json.dumps(t.args, ensure_ascii=False)}")
            status = f"error={t.error}" if t.error else f"result_chars={t.result_chars}"
            lines.append(f"  {status}  elapsed_ms={t.elapsed_ms}")
            lines.append("  result:")
            for ln in (t.result or "").splitlines() or [""]:
                lines.append(f"    {ln}")
    lines.append("-" * 100)
    lines.append("[final content]")
    lines.append(result.content or "(empty)")
    lines.append("")
    return "\n".join(lines)


def _write(fh: Any, transcript_fh: Any, row: dict[str, Any], result: Any | None,
           *, index: int, total: int) -> None:
    fh.write(json.dumps(row, sort_keys=True) + "\n")
    fh.flush()
    transcript_fh.write(_format_transcript(row, result))
    transcript_fh.flush()
    final = row.get("final") or {}
    ag = row.get("agentic") or {}
    print(
        f"agentic {index}/{total} task={str(row.get('task_id'))[:8]} "
        f"status={row.get('status', 'ok')} tools={ag.get('tool_calls_made')} "
        f"finish={final.get('finish_reason')} clen={len(final.get('content') or '')} "
        f"elapsed={(final.get('elapsed_ms') or 0) / 1000:.0f}s"
    )


def _select_tasks(tasks: tuple[DracoTask, ...], *, task_filter: list[str] | None, limit: int | None) -> list[DracoTask]:
    wanted = set(task_filter or ())
    out = [t for t in tasks if not wanted or any(t.id == w or t.id.startswith(w) for w in wanted)]
    if limit is not None:
        out = out[:limit]
    return out


def _completed_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") != "failed" and isinstance(row.get("task_id"), str):
            done.add(row["task_id"])
    return done


if __name__ == "__main__":
    raise SystemExit(main())
