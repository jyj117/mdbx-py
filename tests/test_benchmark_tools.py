from __future__ import annotations

import copy
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "benchmarks" / "check_performance.py"
CHECK_PAIR = runpy.run_path(str(ROOT / "benchmarks" / "compare_revisions.py"))["check_regression"]


def summary() -> dict:
    return {
        "engines": {
            "clibmdbx": {
                "point_get_ops_per_second": {"median": 400},
                "one_shot_get_ops_per_second": {"median": 250},
                "short_transaction_get_ops_per_second": {"median": 200},
                "batch_get_ops_per_second_median": 1000,
                "cursor_scan_ops_per_second_median": 1000,
            },
            "libmdbx_ctypes_0_3_2": {
                "point_get_ops_per_second": {"median": 100},
                "batch_get_ops_per_second_median": 100,
                "cursor_scan_ops_per_second_median": 100,
            },
        }
    }


@pytest.mark.parametrize(
    "metric,value",
    [
        ("one_shot_get_ops_per_second", 199),
        ("short_transaction_get_ops_per_second", 230),
        ("point_get_ops_per_second", 190),
        ("batch_get_ops_per_second_median", 499),
        ("cursor_scan_ops_per_second_median", 499),
    ],
)
def test_original_performance_thresholds_still_reject_failures(tmp_path: Path, metric: str, value: int) -> None:
    data = summary()
    engine = data["engines"]["clibmdbx"]
    if isinstance(engine[metric], dict):
        engine[metric]["median"] = value
    else:
        engine[metric] = value
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(data))
    result = subprocess.run([sys.executable, str(CHECK), str(path)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "below" in result.stderr


def test_original_lifecycle_threshold_accepts_exact_boundary(tmp_path: Path) -> None:
    data = summary()
    data["engines"]["clibmdbx"]["one_shot_get_ops_per_second"]["median"] = 200
    data["engines"]["clibmdbx"]["short_transaction_get_ops_per_second"]["median"] = 170
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(data))
    result = subprocess.run([sys.executable, str(CHECK), str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "one-shot/reused=0.50x" in result.stdout


def report() -> dict:
    return {
        "platform": dict(python="3.10", machine="x86_64", platform="Linux"),
        "config": dict(
            records=10000, value_size=32, iterations=500000, warmup=100000, cursor_repeats=9, cpu=0, path="/tmp/same"
        ),
        "diagnostics": {
            "build": dict(compiler="gcc", flags="O3", options="checked", target="x86_64"),
            "libmdbx": {"minor": 14, "patch": 3},
        },
        "results": {
            "clibmdbx": {
                name: dict(ops_per_second=1000, sample_digest="same")
                for name in ("point_get", "one_shot_get", "short_transaction_get", "batch_get", "cursor_scan")
            }
        },
        "rss_max_kib": 16000,
    }


def test_paired_comparison_detects_regression_and_rejects_mismatched_inputs() -> None:
    base = [report() for _ in range(4)]
    candidate = copy.deepcopy(base)
    assert CHECK_PAIR(base, candidate)["regressions"] == {}
    for item in candidate:
        item["results"]["clibmdbx"]["one_shot_get"]["ops_per_second"] = 940
    assert "one_shot_get" in CHECK_PAIR(base, candidate)["regressions"]
    candidate[0]["results"]["clibmdbx"]["point_get"]["sample_digest"] = "wrong"
    with pytest.raises(ValueError, match="data mismatch"):
        CHECK_PAIR(base, candidate)
    candidate = copy.deepcopy(base)
    candidate[0]["diagnostics"]["build"]["options"] = "different"
    with pytest.raises(ValueError, match="build mismatch"):
        CHECK_PAIR(base, candidate)
    with pytest.raises(ValueError, match="three complete"):
        CHECK_PAIR(base[:2], base[:2])


@pytest.mark.skipif(sys.platform == "win32", reason="benchmark runner uses Linux resource accounting")
def test_cursor_repeats_keep_all_samples_and_validate_row_count() -> None:
    timed_cursor = runpy.run_path(str(ROOT / "benchmarks" / "benchmark.py"))["timed_cursor"]
    calls = []

    def scan():
        calls.append(1)
        return [(b"key", b"value")]

    result = timed_cursor(scan, 1, 9)
    assert len(calls) == 10  # one warm-up plus every measured repeat
    assert result["operations"] == 9
    assert len(result["samples_ns"]) == 9
    assert result["seconds"] == sum(result["samples_ns"]) / 1e9
    with pytest.raises(AssertionError):
        timed_cursor(lambda: [], 1, 9)
