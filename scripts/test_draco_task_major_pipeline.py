#!/usr/bin/env python3
"""Standalone tests for draco_task_major_pipeline.py's pure helper functions
(no subprocess, no network, no real API keys needed). Run directly:

    python3 scripts/test_draco_task_major_pipeline.py

Exits non-zero and prints the failing assertion if anything breaks. This
repo has no existing pytest harness, so this follows the same
dependency-free assert-based style already used for ad hoc verification
elsewhere in this branch (see the Exa circuit breaker commits).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draco_task_major_pipeline as pipeline  # noqa: E402


def test_load_task_ids_no_limit():
    with tempfile.TemporaryDirectory() as d:
        manifest = Path(d) / "m.json"
        manifest.write_text(json.dumps({
            "schema": "trustedrouter.draco.manifest.v1", "id": "x", "task_filter": "non-financial",
            "tasks": [{"id": "a", "domain": "X", "problem": "p", "rubric": {"sections": []}},
                      {"id": "b", "domain": "X", "problem": "p", "rubric": {"sections": []}},
                      {"id": "c", "domain": "X", "problem": "p", "rubric": {"sections": []}}],
        }))
        assert pipeline.load_task_ids(manifest) == ["a", "b", "c"]


def test_load_task_ids_with_limit():
    with tempfile.TemporaryDirectory() as d:
        manifest = Path(d) / "m.json"
        manifest.write_text(json.dumps({
            "schema": "trustedrouter.draco.manifest.v1", "id": "x", "task_filter": "non-financial",
            "tasks": [{"id": "a", "domain": "X", "problem": "p", "rubric": {}},
                      {"id": "b", "domain": "X", "problem": "p", "rubric": {}},
                      {"id": "c", "domain": "X", "problem": "p", "rubric": {}}],
        }))
        assert pipeline.load_task_ids(manifest, limit=2) == ["a", "b"]
        assert pipeline.load_task_ids(manifest, limit=0) == []
        assert pipeline.load_task_ids(manifest, limit=100) == ["a", "b", "c"]


def test_read_jsonl_line_count():
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.jsonl"
        assert pipeline.read_jsonl_line_count(missing) == 0

        empty = Path(d) / "empty.jsonl"
        empty.write_text("")
        assert pipeline.read_jsonl_line_count(empty) == 0

        mixed = Path(d) / "mixed.jsonl"
        mixed.write_text('{"a":1}\n\n{"a":2}\n   \n{"a":3}\n')
        assert pipeline.read_jsonl_line_count(mixed) == 3


def test_read_last_jsonl_row():
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.jsonl"
        assert pipeline.read_last_jsonl_row(missing) is None

        f = Path(d) / "rows.jsonl"
        f.write_text('{"task_id":"a"}\n{"task_id":"b"}\n{"task_id":"c"}\n')
        row = pipeline.read_last_jsonl_row(f)
        assert row == {"task_id": "c"}, row

        # trailing blank line must not break "last row" detection
        f.write_text('{"task_id":"a"}\n{"task_id":"b"}\n\n')
        row = pipeline.read_last_jsonl_row(f)
        assert row == {"task_id": "b"}, row


def test_row_status_ok():
    assert pipeline.row_status_ok(None) is False
    assert pipeline.row_status_ok({"status": "failed"}) is False
    assert pipeline.row_status_ok({"final": {"content": "x"}}) is True  # success rows omit `status`
    assert pipeline.row_status_ok({"status": "ok"}) is True  # anything but "failed" counts as ok


def test_build_agentic_solo_cmd_dry_run():
    cmd = pipeline.build_agentic_solo_cmd(
        python_exe="PY", manifest=Path("M.json"), output=Path("O.jsonl"),
        model="minimax-m3", config_id="panel-minimax-m3", task_id="TID",
        base_url="https://router-api.0g.ai/v1", api_key_name=None, execute=False,
    )
    assert cmd[0:2] == ["PY", "scripts/draco_agentic_solo.py"]
    assert "--execute" not in cmd
    assert "--resume" in cmd
    assert "--no-sec-facts" in cmd
    for pair in (("--model", "minimax-m3"), ("--config-id", "panel-minimax-m3"),
                 ("--task-id", "TID"), ("--doc-parser", "markitdown"),
                 ("--max-tool-calls", "16"), ("--synthesis-max-tokens", "12000"),
                 ("--workers", "1"), ("--base-url", "https://router-api.0g.ai/v1")):
        i = cmd.index(pair[0])
        assert cmd[i + 1] == pair[1], (pair, cmd)
    # api_key_name=None must NOT add --api-key-name at all
    assert "--api-key-name" not in cmd


def test_build_agentic_solo_cmd_execute_and_api_key_name():
    cmd = pipeline.build_agentic_solo_cmd(
        python_exe="PY", manifest=Path("M.json"), output=Path("O.jsonl"),
        model="gpt-5.6-sol", config_id="baseline", task_id="TID",
        base_url="https://router-api.0g.ai/v1", api_key_name="TR_API_KEY", execute=True,
    )
    assert "--execute" in cmd
    i = cmd.index("--api-key-name")
    assert cmd[i + 1] == "TR_API_KEY"


def test_build_fusion_cmd():
    panel_files = {"minimax-m3": Path("p1.jsonl"), "kimi-k3": Path("p2.jsonl")}
    cmd = pipeline.build_fusion_cmd(
        python_exe="PY", manifest=Path("M.json"), panel_files=panel_files,
        output=Path("F.jsonl"), config_id="fusion-prometheus20",
        judge_model="minimax-m3", fuser_model="kimi-k3",
        base_url="https://router-api.0g.ai/v1", api_key_name="TR_API_KEY", execute=False,
    )
    assert cmd[0:2] == ["PY", "scripts/draco_client_fusion.py"]
    assert "--execute" not in cmd
    assert "--resume" in cmd
    panel_args = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--panel"]
    assert panel_args == ["minimax-m3=p1.jsonl", "kimi-k3=p2.jsonl"], panel_args
    for pair in (("--config-id", "fusion-prometheus20"), ("--judge-model", "minimax-m3"),
                 ("--fuser-model", "kimi-k3"), ("--api-key-name", "TR_API_KEY"),
                 ("--workers", "1")):
        i = cmd.index(pair[0])
        assert cmd[i + 1] == pair[1], (pair, cmd)


def test_build_rejudge_cmd_uses_0g_router_judge_model_id():
    cmd = pipeline.build_rejudge_cmd(
        python_exe="PY", replay_file=Path("R.jsonl"), output=Path("S.jsonl"),
        base_url="https://router-api.0g.ai/v1", execute=False,
    )
    assert cmd[0:2] == ["PY", "scripts/draco_rejudge.py"]
    assert cmd[2] == "R.jsonl"  # positional replay file, not a flag
    assert "--resume" in cmd
    i = cmd.index("--judge-model")
    # Must be the bare 0G router id, NOT DRACO_JUDGE_MODEL's own
    # "google/gemini-3.1-pro-preview" default (that prefixed form is not a
    # real 0G router model id).
    assert cmd[i + 1] == "gemini-3.1-pro-preview", cmd[i + 1]


def test_fusion_configs_cover_exactly_the_required_panel_models_no_qwen():
    all_fusion_panel_models = set()
    for cfg in pipeline.FUSION_CONFIGS:
        all_fusion_panel_models.update(cfg["panel"])
    assert all_fusion_panel_models <= set(pipeline.PANEL_MODELS), (
        "a fusion config references a panel model not in PANEL_MODELS"
    )
    assert "qwen3.8-max" not in pipeline.PANEL_MODELS
    assert "qwen3.8-max" not in all_fusion_panel_models
    assert set(pipeline.PANEL_MODELS) == {
        "minimax-m3", "kimi-k3", "glm-5.2", "deepseek-v4-pro",
        "mimo-v2.5-pro", "glm-5.3-flash", "deepseek-v4-flash",
    }
    assert set(pipeline.FUSION_PROMETHEUS20["panel"]) == {
        "minimax-m3", "kimi-k3", "glm-5.2", "deepseek-v4-pro", "mimo-v2.5-pro",
    }
    assert pipeline.FUSION_PROMETHEUS20["judge_model"] == "minimax-m3"
    assert pipeline.FUSION_PROMETHEUS20["fuser_model"] == "kimi-k3"
    assert set(pipeline.FUSION_LATEST3PANEL["panel"]) == {
        "minimax-m3", "glm-5.3-flash", "deepseek-v4-flash",
    }
    assert pipeline.FUSION_LATEST3PANEL["judge_model"] == "minimax-m3"
    assert pipeline.FUSION_LATEST3PANEL["fuser_model"] == "minimax-m3"
    assert pipeline.BASELINE_MODEL == "gpt-5.6-sol"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"ok   {t.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
