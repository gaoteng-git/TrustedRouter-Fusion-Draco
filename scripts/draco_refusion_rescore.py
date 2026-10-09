#!/usr/bin/env python3
"""Re-run ONLY judge+fuser+scoring against already-generated panel/baseline
solos, writing to a NEW output directory. Does not touch, re-read-as-write,
or regenerate anything in --source-dir -- panel/baseline solos are the
expensive/slow part (live agentic + Exa calls); this script exists so a
fuser-prompt/--fuser-max-tokens change (e.g. branch fusion-prompt-detail-fix)
can be evaluated against an EXISTING panel dataset without re-paying for
panel/baseline generation.

Pure orchestration over the existing CLI tools (draco_client_fusion.py /
draco_rejudge.py) via subprocess -- no panel/judge/fusion/scoring logic is
reimplemented here, same convention as draco_task_major_pipeline.py.

baseline's score is NOT regenerated: baseline's content doesn't depend on
fusion-latest3panel's judge/fuser prompt at all, so <source-dir>/
score-baseline.jsonl is still valid and directly comparable as-is. Only a
new fusion score is produced.

Usage
-----
Dry run (prints the two commands, calls nothing):
    uv run python scripts/draco_refusion_rescore.py \\
        --source-dir out/task_major --out-dir out/task_major_v2 --limit 25

Real run:
    export TR_API_KEY=...
    uv run python scripts/draco_refusion_rescore.py \\
        --source-dir out/task_major --out-dir out/task_major_v2 \\
        --limit 25 --execute

Safe to interrupt and re-run: both underlying calls are --resume'd against
their own (new) output files in --out-dir.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PANEL_MODELS = ("minimax-m3", "glm-5.3-flash", "deepseek-v4-flash")
FUSION_CONFIG_ID = "fusion-latest3panel"
JUDGE_MODEL = "minimax-m3"
FUSER_MODEL = "minimax-m3"
REJUDGE_JUDGE_MODEL = "gemini-3.1-pro-preview"

DEFAULT_MANIFEST = REPO_ROOT / "data" / "draco-non-financial-80.manifest.json"
DEFAULT_BASE_URL = "https://router-api.0g.ai/v1"
DEFAULT_API_KEY_NAME = "TR_API_KEY"


def run_step(cmd: list[str], *, label: str) -> bool:
    print(f"--- {label} ---")
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    if proc.returncode != 0:
        print(f"{label} exited {proc.returncode}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-dir", type=Path, required=True,
                   help="Directory with EXISTING panel-<model>.jsonl to reuse (read-only).")
    p.add_argument("--out-dir", type=Path, required=True,
                   help="Where to write the new fusion/score files. Must differ from --source-dir.")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--api-key-name", default=DEFAULT_API_KEY_NAME)
    p.add_argument("--limit", type=int, default=None,
                   help="Only the first N manifest tasks -- use this to match however many "
                        "tasks --source-dir's panel files actually cover.")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--execute", action="store_true",
                   help="Actually call the gateway. Omit for a dry run that prints both "
                        "commands without spending anything.")
    args = p.parse_args(argv)

    if args.out_dir.resolve() == args.source_dir.resolve():
        print("--out-dir must differ from --source-dir (refusing to overwrite source panel data)")
        return 2

    missing = [m for m in PANEL_MODELS if not (args.source_dir / f"panel-{m}.jsonl").exists()]
    if missing:
        print(f"missing panel files in {args.source_dir}: {missing}")
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    python_exe = sys.executable

    fusion_out = args.out_dir / f"{FUSION_CONFIG_ID}.jsonl"
    fusion_cmd = [python_exe, "scripts/draco_client_fusion.py", "--manifest", str(args.manifest)]
    for m in PANEL_MODELS:
        fusion_cmd += ["--panel", f"{m}={args.source_dir / f'panel-{m}.jsonl'}"]
    fusion_cmd += [
        "--output", str(fusion_out),
        "--config-id", FUSION_CONFIG_ID,
        "--judge-model", JUDGE_MODEL,
        "--fuser-model", FUSER_MODEL,
        "--base-url", args.base_url,
        "--api-key-name", args.api_key_name,
        "--workers", str(args.workers),
        "--resume",
    ]
    if args.limit is not None:
        fusion_cmd += ["--limit", str(args.limit)]
    if args.execute:
        fusion_cmd.append("--execute")
    if not run_step(fusion_cmd, label="fusion (reusing existing panel solos, new prompt)"):
        return 1

    score_out = args.out_dir / f"score-{FUSION_CONFIG_ID}.jsonl"
    score_cmd = [
        python_exe, "scripts/draco_rejudge.py", str(fusion_out),
        "--output", str(score_out),
        "--judge-model", REJUDGE_JUDGE_MODEL,
        "--base-url", args.base_url,
        "--workers", str(args.workers),
        "--resume",
    ]
    if args.execute:
        score_cmd.append("--execute")
    if not run_step(score_cmd, label="score fusion"):
        return 1

    print()
    print(f"new fusion:       {fusion_out}")
    print(f"new fusion score: {score_out}")
    print(f"baseline score unchanged, reuse as-is: {args.source_dir / 'score-baseline.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
