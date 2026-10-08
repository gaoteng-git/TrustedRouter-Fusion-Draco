#!/usr/bin/env python3
"""Task-major DRACO orchestration: for each task (one at a time, in manifest
order), run all 7 panel solos + baseline, both fusion configs, and all 3
scorings -- before moving to the next task. Pure orchestration over the
EXISTING CLI tools (draco_agentic_solo.py / draco_client_fusion.py /
draco_rejudge.py) via subprocess; no panel/judge/fusion/scoring logic is
reimplemented here.

Why no per-task temp manifest or temp single-row panel files are needed
------------------------------------------------------------------------
draco_client_fusion.py and draco_rejudge.py have no --task-id flag, but
both already support exactly the filtering this script needs, for free,
by combining two things each already does on its own:

  1. draco_client_fusion.py intersects --manifest's tasks against every
     --panel file's own task coverage (`tasks = [t for t in tasks if
     all(t.id in panels[l] for l in labels)]`) -- so passing the FULL
     80-task manifest and the FULL (growing) per-model panel files
     naturally narrows `tasks` down to exactly the task_ids every
     required panel member has already answered.
  2. Both draco_client_fusion.py and draco_rejudge.py support --resume,
     which skips task_ids (draco_client_fusion.py) / (config_id, task_id)
     pairs (draco_rejudge.py, via replay_completed_ids) already present
     in their OWN output file.

So: always pass the full manifest, the full (cumulative) panel/replay
files, and --resume pointed at this combo's own cumulative output file.
In a task-major loop that runs panel solos for task K before ever touching
task K+1, step (1) alone would already resolve to "every task up to and
including the most recent one whose full panel is done" -- step (2) is
what narrows that further to just the ONE new task each time, since every
earlier task was already fused/scored in a prior iteration. No temp files,
no reimplementing draco_client_fusion.py's / draco_rejudge.py's own
filtering -- this script only ever changes WHEN it calls each tool, never
WHAT each tool does internally.

One real consequence of relying on this instead of a hard --task-id pin:
if a panel member's call for task K fails, draco_client_fusion.py's own
intersection naturally will not include task K in `tasks` yet (the failed
model has no successful row for it) -- fusion for task K is silently
*skipped that round*, not retried or errored. This script detects that
(by comparing each combo's output line count before/after the call) and
prints a clear "skipped (prerequisite missing)" line rather than treating
it as a silent success. Re-running this script later with --resume at
every step will pick task K back up once/if that panel member succeeds.

Models and configs (see report/draco-eval-on-0g-router-howto.md for the
full background on where these come from)
------------------------------------------------------------------------
Panel pool (7, solo, independently tooled runs): minimax-m3, kimi-k3,
glm-5.2, deepseek-v4-pro, mimo-v2.5-pro, glm-5.3-flash, deepseek-v4-flash.
Baseline: gpt-5.6-sol, run through the IDENTICAL tooled agentic harness
(draco_agentic_solo.py) as every panel member -- not a simplified
single-shot call.

Two fusion configs, BOTH built only from the 7 panel results above (no
panel member is ever run twice):
  - "fusion-prometheus20": panel = minimax-m3, kimi-k3, glm-5.2,
    deepseek-v4-pro, mimo-v2.5-pro -- the real Prometheus 2.0 panel
    (confirmed from Lore-Hex/quill-cloud-proxy's enclave fusion.go,
    `fusionPrometheus20Panel`). judge=minimax-m3, fuser=kimi-k3 (each
    config's own real first-priority choice from
    fusionPresetJudgeModelsForModel / fusionPresetFinalModelsForModel).
  - "fusion-latest3panel": panel = minimax-m3, glm-5.3-flash,
    deepseek-v4-flash -- this project's own "Latest 3-panel" combo.
    judge=synthesis=minimax-m3.

Environment
-----------
TR_API_KEY   -- 0G router inference key (required with --execute)
EXA_API_KEY  -- Exa search key (required with --execute, read internally
                by draco_agentic_solo.py)

Usage
-----
Dry run (prints every command this would run, calls nothing):
    uv run python scripts/draco_task_major_pipeline.py --limit 2

Real run, 2 tasks, actually spending money:
    export TR_API_KEY=...  EXA_API_KEY=...
    uv run python scripts/draco_task_major_pipeline.py --limit 2 --execute

Full 80-task run:
    uv run python scripts/draco_task_major_pipeline.py --execute

Safe to interrupt and re-run: every underlying call is --resume'd against
its own output file, so re-running this script picks up where it left off
(panel solos resume per-model; fusion/scoring resume via the mechanism
described above).

Output files, under --out-dir (default out/task_major/):
    panel-<model>.jsonl             one per panel model, 7 files
    baseline-gpt-5.6-sol.jsonl
    fusion-prometheus20.jsonl
    fusion-latest3panel.jsonl
    score-fusion-prometheus20.jsonl
    score-fusion-latest3panel.jsonl
    score-baseline.jsonl
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

PANEL_MODELS: tuple[str, ...] = (
    "minimax-m3",
    "kimi-k3",
    "glm-5.2",
    "deepseek-v4-pro",
    "mimo-v2.5-pro",
    "glm-5.3-flash",
    "deepseek-v4-flash",
)
BASELINE_MODEL = "gpt-5.6-sol"

FUSION_PROMETHEUS20 = {
    "config_id": "fusion-prometheus20",
    "panel": ("minimax-m3", "kimi-k3", "glm-5.2", "deepseek-v4-pro", "mimo-v2.5-pro"),
    "judge_model": "minimax-m3",
    "fuser_model": "kimi-k3",
}
FUSION_LATEST3PANEL = {
    "config_id": "fusion-latest3panel",
    "panel": ("minimax-m3", "glm-5.3-flash", "deepseek-v4-flash"),
    "judge_model": "minimax-m3",
    "fuser_model": "minimax-m3",
}
FUSION_CONFIGS = (FUSION_PROMETHEUS20, FUSION_LATEST3PANEL)

DEFAULT_MANIFEST = REPO_ROOT / "data" / "draco-non-financial-80.manifest.json"
DEFAULT_OUT_DIR = REPO_ROOT / "out" / "task_major"
DEFAULT_BASE_URL = "https://router-api.0g.ai/v1"
DEFAULT_API_KEY_NAME = "TR_API_KEY"

# 0G router's real model id has no provider prefix (confirmed live against
# https://router-api.0g.ai/v1/models, 2026-10-08) -- DRACO_JUDGE_MODEL's own
# default ("google/gemini-3.1-pro-preview") is TrustedRouter's own naming,
# not 0G router's.
REJUDGE_JUDGE_MODEL = "gemini-3.1-pro-preview"


# --------------------------------------------------------------------------- #
# Pure helpers (no subprocess, no network) -- unit-tested independently       #
# --------------------------------------------------------------------------- #
def load_task_ids(manifest_path: Path, *, limit: int | None = None) -> list[str]:
    """Ordered task ids straight from a real draco manifest JSON file (same
    `tasks` ordering draco_agentic_solo.py's own _select_tasks/--limit would
    use -- this is read-only inspection of the same file, not a
    reimplementation of any scoring/filtering logic)."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError(f"{manifest_path}: manifest has no 'tasks' list")
    ids = [t["id"] for t in tasks]
    if limit is not None:
        ids = ids[:limit]
    return ids


def ensure_file_exists(path: Path) -> None:
    """Create an empty file (and parent dirs) if it doesn't exist yet.

    Needed ONLY for the no-`--execute` dry-run path: draco_client_fusion.py's
    _load_panel() and draco_rejudge.py's row-reading both open their input
    files unconditionally, BEFORE either script's own `if not args.execute:
    print("dry run..."); return 0` check -- so in a from-scratch dry run
    (no prior --execute call ever created these files), calling fusion/
    rejudge on a panel/replay path that doesn't exist yet raises a bare
    FileNotFoundError instead of reaching that script's own clean dry-run
    message. This is a real, pre-existing gap in those two scripts
    (confirmed empty files read as "0 rows", never crashing once they
    exist), not something to patch inside them -- touching an empty file
    here is the minimal fix that keeps this orchestrator's own advertised
    "dry run prints every command, calls nothing" promise true even on a
    brand new --out-dir. In a real --execute run this is always a no-op:
    by the time step (c)/(d)/(e) need a file, the real panel/fusion call
    that produces it has already run for real and created it with content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def read_jsonl_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def read_last_jsonl_row(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    last: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last = line
    return json.loads(last) if last is not None else None


def row_status_ok(row: dict[str, Any] | None) -> bool:
    """A row from draco_agentic_solo.py / draco_client_fusion.py is a
    success unless it explicitly carries status == "failed" (both scripts'
    own convention -- success rows simply omit the `status` key)."""
    return row is not None and row.get("status") != "failed"


def panel_output_path(out_dir: Path, model: str) -> Path:
    return out_dir / f"panel-{model}.jsonl"


def baseline_output_path(out_dir: Path) -> Path:
    return out_dir / f"baseline-{BASELINE_MODEL}.jsonl"


def fusion_output_path(out_dir: Path, config_id: str) -> Path:
    return out_dir / f"{config_id}.jsonl"


def score_output_path(out_dir: Path, config_id: str) -> Path:
    return out_dir / f"score-{config_id}.jsonl"


def build_agentic_solo_cmd(
    *,
    python_exe: str,
    manifest: Path,
    output: Path,
    model: str,
    config_id: str,
    task_id: str,
    base_url: str,
    api_key_name: str | None,
    execute: bool,
    max_tool_calls: int = 16,
    synthesis_max_tokens: int = 12000,
    workers: int = 1,
) -> list[str]:
    cmd = [
        python_exe, "scripts/draco_agentic_solo.py",
        "--manifest", str(manifest),
        "--output", str(output),
        "--model", model,
        "--config-id", config_id,
        "--task-id", task_id,
        "--base-url", base_url,
        "--doc-parser", "markitdown",
        "--no-sec-facts",
        "--max-tool-calls", str(max_tool_calls),
        "--synthesis-max-tokens", str(synthesis_max_tokens),
        "--workers", str(workers),
        "--resume",
    ]
    if api_key_name:
        cmd += ["--api-key-name", api_key_name]
    if execute:
        cmd.append("--execute")
    return cmd


def build_fusion_cmd(
    *,
    python_exe: str,
    manifest: Path,
    panel_files: dict[str, Path],
    output: Path,
    config_id: str,
    judge_model: str,
    fuser_model: str,
    base_url: str,
    api_key_name: str,
    execute: bool,
    workers: int = 1,
) -> list[str]:
    cmd = [
        python_exe, "scripts/draco_client_fusion.py",
        "--manifest", str(manifest),
    ]
    for label, path in panel_files.items():
        cmd += ["--panel", f"{label}={path}"]
    cmd += [
        "--output", str(output),
        "--config-id", config_id,
        "--judge-model", judge_model,
        "--fuser-model", fuser_model,
        "--base-url", base_url,
        "--api-key-name", api_key_name,
        "--workers", str(workers),
        "--resume",
    ]
    if execute:
        cmd.append("--execute")
    return cmd


def build_rejudge_cmd(
    *,
    python_exe: str,
    replay_file: Path,
    output: Path,
    base_url: str,
    execute: bool,
    workers: int = 1,
) -> list[str]:
    cmd = [
        python_exe, "scripts/draco_rejudge.py",
        str(replay_file),
        "--output", str(output),
        "--judge-model", REJUDGE_JUDGE_MODEL,
        "--base-url", base_url,
        "--workers", str(workers),
        "--resume",
    ]
    if execute:
        cmd.append("--execute")
    return cmd


# --------------------------------------------------------------------------- #
# Subprocess execution                                                        #
# --------------------------------------------------------------------------- #
class FatalPipelineError(RuntimeError):
    """A subprocess exited non-zero. Aborts the whole run -- the same
    "stop, don't keep burning calls" philosophy as ExaCircuitBreakerTripped
    (a nonzero exit from draco_agentic_solo.py is in fact exactly how that
    exception surfaces at the process level)."""


def run_step(cmd: list[str], *, label: str) -> subprocess.CompletedProcess:
    print(f"--- {label} ---")
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.stderr:
        print(proc.stderr, end="" if proc.stderr.endswith("\n") else "\n", file=sys.stderr)
    if proc.returncode != 0:
        raise FatalPipelineError(
            f"{label} exited {proc.returncode} -- aborting the whole run "
            "(this is almost always either ExaCircuitBreakerTripped or a "
            "missing/invalid API key; see the stderr above)."
        )
    return proc


# --------------------------------------------------------------------------- #
# Main task-major loop                                                        #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--api-key-name", default=DEFAULT_API_KEY_NAME,
                    help="Env var holding the 0G router key (used for draco_client_fusion.py's "
                         "--api-key-name; draco_agentic_solo.py finds TR_API_KEY on its own).")
    p.add_argument("--limit", type=int, default=None, help="Only process the first N tasks.")
    p.add_argument("--max-tool-calls", type=int, default=16)
    p.add_argument("--synthesis-max-tokens", type=int, default=12000)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--execute", action="store_true",
                    help="Actually call the gateway/Exa/docker. Omit for a dry run that prints "
                         "every command without spending anything.")
    args = p.parse_args(argv)

    python_exe = sys.executable
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    task_ids = load_task_ids(args.manifest, limit=args.limit)
    print(f"manifest: {args.manifest}  tasks: {len(task_ids)}  execute: {args.execute}")
    print(f"out_dir: {out_dir}")
    print(f"panel models ({len(PANEL_MODELS)}): {', '.join(PANEL_MODELS)}")
    print(f"baseline: {BASELINE_MODEL}")
    for cfg in FUSION_CONFIGS:
        print(f"fusion [{cfg['config_id']}]: panel={list(cfg['panel'])} "
              f"judge={cfg['judge_model']} fuser={cfg['fuser_model']}")
    print()

    panel_paths = {m: panel_output_path(out_dir, m) for m in PANEL_MODELS}
    baseline_path = baseline_output_path(out_dir)
    fusion_paths = {cfg["config_id"]: fusion_output_path(out_dir, cfg["config_id"]) for cfg in FUSION_CONFIGS}
    score_paths = {cfg["config_id"]: score_output_path(out_dir, cfg["config_id"]) for cfg in FUSION_CONFIGS}
    score_paths["baseline"] = score_output_path(out_dir, "baseline")

    for i, task_id in enumerate(task_ids, start=1):
        print(f"\n===== task {i}/{len(task_ids)}  id={task_id} =====")

        # (a) 7 panel models, solo
        for model in PANEL_MODELS:
            out_path = panel_paths[model]
            cmd = build_agentic_solo_cmd(
                python_exe=python_exe, manifest=args.manifest, output=out_path,
                model=model, config_id=f"panel-{model}", task_id=task_id,
                base_url=args.base_url, api_key_name=None, execute=args.execute,
                max_tool_calls=args.max_tool_calls, synthesis_max_tokens=args.synthesis_max_tokens,
                workers=args.workers,
            )
            run_step(cmd, label=f"panel solo: {model}")
            if args.execute:
                row = read_last_jsonl_row(out_path)
                if not row_status_ok(row):
                    print(f"  WARNING: panel {model} failed for task {task_id} "
                          f"(error_type={row.get('error_type') if row else None}) -- "
                          "fusion configs needing this model will skip this task until a "
                          "later --resume re-run succeeds.")

        # (b) baseline, SAME tooled harness as every panel member
        cmd = build_agentic_solo_cmd(
            python_exe=python_exe, manifest=args.manifest, output=baseline_path,
            model=BASELINE_MODEL, config_id="baseline", task_id=task_id,
            base_url=args.base_url, api_key_name=None, execute=args.execute,
            max_tool_calls=args.max_tool_calls, synthesis_max_tokens=args.synthesis_max_tokens,
            workers=args.workers,
        )
        run_step(cmd, label=f"baseline: {BASELINE_MODEL}")
        if args.execute:
            row = read_last_jsonl_row(baseline_path)
            if not row_status_ok(row):
                print(f"  WARNING: baseline failed for task {task_id} "
                      f"(error_type={row.get('error_type') if row else None})")

        # (c)/(d) both fusion configs -- full manifest + full (cumulative)
        # panel files + --resume; see module docstring for why this alone
        # resolves to "only this task" in a task-major loop.
        for cfg in FUSION_CONFIGS:
            fusion_out = fusion_paths[cfg["config_id"]]
            if not args.execute:
                # See ensure_file_exists's docstring: a from-scratch dry run
                # never created these panel files for real, but
                # draco_client_fusion.py reads them before its own
                # --execute check -- touch them empty so it reaches that
                # check instead of crashing.
                for label in cfg["panel"]:
                    ensure_file_exists(panel_paths[label])
                ensure_file_exists(fusion_out)
            before = read_jsonl_line_count(fusion_out)
            panel_files = {label: panel_paths[label] for label in cfg["panel"]}
            cmd = build_fusion_cmd(
                python_exe=python_exe, manifest=args.manifest, panel_files=panel_files,
                output=fusion_out, config_id=cfg["config_id"],
                judge_model=cfg["judge_model"], fuser_model=cfg["fuser_model"],
                base_url=args.base_url, api_key_name=args.api_key_name, execute=args.execute,
                workers=args.workers,
            )
            run_step(cmd, label=f"fusion: {cfg['config_id']}")
            if args.execute:
                after = read_jsonl_line_count(fusion_out)
                if after == before:
                    print(f"  skipped (prerequisite missing): {cfg['config_id']} has no new row "
                          f"for task {task_id} -- a required panel member likely failed above.")

        # (e) score fusion-A, fusion-B, and baseline -- three independent
        # scoring passes, same --resume-based narrowing.
        for config_id, replay_path in (
            (FUSION_PROMETHEUS20["config_id"], fusion_paths[FUSION_PROMETHEUS20["config_id"]]),
            (FUSION_LATEST3PANEL["config_id"], fusion_paths[FUSION_LATEST3PANEL["config_id"]]),
            ("baseline", baseline_path),
        ):
            score_out = score_paths[config_id]
            if not args.execute:
                # Same reasoning as the fusion step above -- draco_rejudge.py
                # reads `replay_path` before its own --execute check.
                ensure_file_exists(replay_path)
                ensure_file_exists(score_out)
            before = read_jsonl_line_count(score_out)
            cmd = build_rejudge_cmd(
                python_exe=python_exe, replay_file=replay_path, output=score_out,
                base_url=args.base_url, execute=args.execute, workers=args.workers,
            )
            run_step(cmd, label=f"score: {config_id}")
            if args.execute:
                after = read_jsonl_line_count(score_out)
                if after == before:
                    print(f"  skipped (prerequisite missing): score-{config_id} has no new row "
                          f"for task {task_id}.")

    print("\nAll tasks processed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FatalPipelineError as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        raise SystemExit(1)
