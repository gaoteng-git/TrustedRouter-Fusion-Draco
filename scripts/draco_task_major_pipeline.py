#!/usr/bin/env python3
"""Task-major DRACO orchestration: for each task (one at a time, in manifest
order), run the active panel solos + baseline, the active fusion config(s),
and their scorings -- before moving to the next task. Pure orchestration
over the EXISTING CLI tools (draco_agentic_solo.py / draco_client_fusion.py
/ draco_rejudge.py) via subprocess; no panel/judge/fusion/scoring logic is
reimplemented here.

Current scope (2026-10-08): only "fusion-latest3panel" + baseline are
active -- "fusion-prometheus20" (the real Prometheus 2.0 panel) is disabled
for now because its panel includes kimi-k3, which has been hitting
intermittent real HTTP 400s from 0G router on this agentic workload (not
yet root-caused; --resume will retry it automatically whenever it's
re-enabled). FUSION_PROMETHEUS20 is still fully defined below, just left
out of the active FUSION_CONFIGS tuple -- re-enable by adding it back in
once kimi-k3 is stable, no other code changes needed.

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
draco_client_fusion.py fuses a task as soon as ANY ONE configured panel
member answered it (not only once every single one has) -- see that
script's own `any(...)` task filter and `present_labels` construction in
run_one(). So a single panel member failing, or coming back with
genuinely empty content (both real, observed outcomes -- a raised
exception, or a response that hit its token/tool-call budget before
writing any visible content), does NOT block fusion for that task; the
judge/fuser simply see whichever subset of the configured panel actually
answered (see each fused row's own `fusion.panel` field for exactly which
labels that was). Fusion for task K is only skipped entirely when EVERY
configured panel member failed or came back empty for it. This script
still detects that zero-coverage case (by looking up each combo's output
for a row matching this specific task_id -- see find_row_for_task) and
prints a clear "skipped (prerequisite missing)" line rather than treating
it as a silent success.
Re-running this script later with --resume at every step will pick task K
back up once/if at least one panel member succeeds for it.

Models and configs (see report/draco-eval-on-0g-router-howto.md for the
full background on where these come from)
------------------------------------------------------------------------
Active panel pool (3, solo, independently tooled runs): minimax-m3,
glm-5.3-flash, deepseek-v4-flash -- exactly what fusion-latest3panel needs,
nothing more. Baseline: gpt-5.6-sol, run through the IDENTICAL tooled
agentic harness (draco_agentic_solo.py) as every panel member -- not a
simplified single-shot call.

One active fusion config (built only from the 3 panel results above):
  - "fusion-latest3panel": panel = minimax-m3, glm-5.3-flash,
    deepseek-v4-flash -- this project's own "Latest 3-panel" combo.
    judge=synthesis=minimax-m3.

Disabled for now (see "Current scope" above): "fusion-prometheus20" --
panel = minimax-m3, kimi-k3, glm-5.2, deepseek-v4-pro, mimo-v2.5-pro --
the real Prometheus 2.0 panel (confirmed from Lore-Hex/quill-cloud-proxy's
enclave fusion.go, `fusionPrometheus20Panel`). judge=minimax-m3,
fuser=kimi-k3 (each config's own real first-priority choice from
fusionPresetJudgeModelsForModel / fusionPresetFinalModelsForModel).

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
    panel-<model>.jsonl             one per active panel model (3 files)
    baseline-gpt-5.6-sol.jsonl
    fusion-latest3panel.jsonl
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

# Active panel pool -- just what fusion-latest3panel needs. kimi-k3,
# glm-5.2, deepseek-v4-pro, mimo-v2.5-pro (needed only by the currently-
# disabled fusion-prometheus20) are intentionally NOT here; re-add them
# alongside re-enabling FUSION_PROMETHEUS20 below.
PANEL_MODELS: tuple[str, ...] = (
    "minimax-m3",
    "glm-5.3-flash",
    "deepseek-v4-flash",
)
BASELINE_MODEL = "gpt-5.6-sol"

# Kept defined (not deleted) for when kimi-k3 is stable again -- see
# "Current scope" in the module docstring. Not referenced by FUSION_CONFIGS
# below, so it has no effect on an active run; PANEL_MODELS above would also
# need kimi-k3/glm-5.2/deepseek-v4-pro/mimo-v2.5-pro added back.
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
FUSION_CONFIGS = (FUSION_LATEST3PANEL,)

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


def find_row_for_task(path: Path, task_id: str) -> dict[str, Any] | None:
    """The row in `path` for this exact task_id (last one, if somehow
    duplicated), or None if there isn't one.

    NOT the same as read_last_jsonl_row(path) -- that returns whatever
    row is physically last in the file, which is only the SAME row as
    this task's when this task is the most recently processed one. On a
    restart (this script re-walks every task_id from the start each time,
    relying on --resume to skip already-done ones), a task near the front
    of the manifest whose row was written in an EARLIER invocation can be
    arbitrarily far from the end of an already-partially-filled output
    file -- read_last_jsonl_row would silently check some LATER task's
    status instead of this one's, both masking real failures and raising
    false "prerequisite missing" alarms for tasks that actually succeeded
    (see the task-major run log: this exact false alarm fired for task 1
    once task 2's rows were appended after it)."""
    match: dict[str, Any] | None = None
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("task_id") == task_id:
            match = row
    return match


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
    require_full_panel: bool = False,
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
    if require_full_panel:
        cmd.append("--require-full-panel")
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
    p.add_argument("--panel-models", default=",".join(PANEL_MODELS),
                    help="Comma-separated panel model list. Default reproduces the active "
                         "PANEL_MODELS/FUSION_LATEST3PANEL combo exactly; override to run a "
                         "different panel (e.g. a 5-panel experiment) without touching the "
                         "default 3-panel setup's own output files (use a different --out-dir).")
    p.add_argument("--fusion-config-id", default=FUSION_LATEST3PANEL["config_id"],
                    help="config_id for the single active fusion config built from "
                         "--panel-models/--judge-model/--fuser-model.")
    p.add_argument("--judge-model", default=FUSION_LATEST3PANEL["judge_model"])
    p.add_argument("--fuser-model", default=FUSION_LATEST3PANEL["fuser_model"])
    p.add_argument("--require-full-panel", action="store_true",
                    help="Passed through to draco_client_fusion.py: only fuse/score a task once "
                         "EVERY --panel-models member has answered it, instead of the default "
                         "any-one-is-enough rule. Tasks missing a member are left pending and "
                         "picked up by a later --resume re-run once that member catches up.")
    p.add_argument("--execute", action="store_true",
                    help="Actually call the gateway/Exa/docker. Omit for a dry run that prints "
                         "every command without spending anything.")
    args = p.parse_args(argv)

    python_exe = sys.executable
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    panel_models = tuple(m.strip() for m in args.panel_models.split(",") if m.strip())
    fusion_configs = ({
        "config_id": args.fusion_config_id,
        "panel": panel_models,
        "judge_model": args.judge_model,
        "fuser_model": args.fuser_model,
    },)

    task_ids = load_task_ids(args.manifest, limit=args.limit)
    print(f"manifest: {args.manifest}  tasks: {len(task_ids)}  execute: {args.execute}")
    print(f"out_dir: {out_dir}")
    print(f"panel models ({len(panel_models)}): {', '.join(panel_models)}")
    print(f"baseline: {BASELINE_MODEL}")
    for cfg in fusion_configs:
        print(f"fusion [{cfg['config_id']}]: panel={list(cfg['panel'])} "
              f"judge={cfg['judge_model']} fuser={cfg['fuser_model']}")
    print()

    panel_paths = {m: panel_output_path(out_dir, m) for m in panel_models}
    baseline_path = baseline_output_path(out_dir)
    fusion_paths = {cfg["config_id"]: fusion_output_path(out_dir, cfg["config_id"]) for cfg in fusion_configs}
    score_paths = {cfg["config_id"]: score_output_path(out_dir, cfg["config_id"]) for cfg in fusion_configs}
    score_paths["baseline"] = score_output_path(out_dir, "baseline")

    for i, task_id in enumerate(task_ids, start=1):
        print(f"\n===== task {i}/{len(task_ids)}  id={task_id} =====")

        # (a) active panel models, solo
        for model in panel_models:
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
                row = find_row_for_task(out_path, task_id)
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
            row = find_row_for_task(baseline_path, task_id)
            if not row_status_ok(row):
                print(f"  WARNING: baseline failed for task {task_id} "
                      f"(error_type={row.get('error_type') if row else None})")

        # (c)/(d) both fusion configs -- full manifest + full (cumulative)
        # panel files + --resume; see module docstring for why this alone
        # resolves to "only this task" in a task-major loop.
        for cfg in fusion_configs:
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
            panel_files = {label: panel_paths[label] for label in cfg["panel"]}
            cmd = build_fusion_cmd(
                python_exe=python_exe, manifest=args.manifest, panel_files=panel_files,
                output=fusion_out, config_id=cfg["config_id"],
                judge_model=cfg["judge_model"], fuser_model=cfg["fuser_model"],
                base_url=args.base_url, api_key_name=args.api_key_name, execute=args.execute,
                workers=args.workers, require_full_panel=args.require_full_panel,
            )
            run_step(cmd, label=f"fusion: {cfg['config_id']}")
            if args.execute:
                # Matched by task_id, NOT a before/after line-count diff --
                # on a restart, a task resolved in an EARLIER invocation has
                # "no new row" too (--resume skipped it), which a count diff
                # can't tell apart from genuine zero panel coverage. See
                # find_row_for_task's docstring.
                row = find_row_for_task(fusion_out, task_id)
                if row is None:
                    reason = (
                        f"at least one of {list(cfg['panel'])} hasn't answered yet"
                        if args.require_full_panel else
                        f"ALL of {list(cfg['panel'])} failed or came back empty above "
                        "(fusion only needs one to succeed)"
                    )
                    print(f"  skipped (prerequisite missing): {cfg['config_id']} has no row "
                          f"for task {task_id} -- {reason}.")
                elif not row_status_ok(row):
                    print(f"  WARNING: fusion {cfg['config_id']} failed for task {task_id} "
                          f"(error_type={row.get('error_type')}) -- a failed row was written; "
                          "--resume will retry it on a later full re-run.")

        # (e) score every active fusion config, plus baseline -- independent
        # scoring passes, same --resume-based narrowing. Iterates
        # fusion_configs directly (not a hardcoded config_id list) so this
        # stays correct however many fusion configs are currently active.
        for config_id, replay_path in (
            *((cfg["config_id"], fusion_paths[cfg["config_id"]]) for cfg in fusion_configs),
            ("baseline", baseline_path),
        ):
            score_out = score_paths[config_id]
            if not args.execute:
                # Same reasoning as the fusion step above -- draco_rejudge.py
                # reads `replay_path` before its own --execute check.
                ensure_file_exists(replay_path)
                ensure_file_exists(score_out)
            cmd = build_rejudge_cmd(
                python_exe=python_exe, replay_file=replay_path, output=score_out,
                base_url=args.base_url, execute=args.execute, workers=args.workers,
            )
            run_step(cmd, label=f"score: {config_id}")
            if args.execute:
                # See the fusion step's identical comment above: matched by
                # task_id, not a before/after count diff.
                row = find_row_for_task(score_out, task_id)
                if row is None:
                    print(f"  skipped (prerequisite missing): score-{config_id} has no row "
                          f"for task {task_id}.")
                elif not row_status_ok(row):
                    print(f"  WARNING: score-{config_id} failed for task {task_id} "
                          f"(error_type={row.get('error_type')}) -- a failed row was written; "
                          "--resume will retry it on a later full re-run.")

    print("\nAll tasks processed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FatalPipelineError as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        raise SystemExit(1)
