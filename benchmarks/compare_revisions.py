#!/usr/bin/env python3
"""AB/BA comparison on one runner, CPU, Python ABI and exact read-only fixture.

This is additional protection, not a replacement for check_performance.py.
Its existing 0.50/1.10/2x/5x thresholds are neither overridden nor skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import statistics
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
WORKLOADS = ("one_shot_get", "short_transaction_get", "point_get", "batch_get", "cursor_scan")


def check_regression(baseline: list[dict], candidate: list[dict]) -> dict:
    if len(baseline) != len(candidate) or len(baseline) < 3:
        raise ValueError("at least three complete baseline/candidate pairs are required")
    for old, new in zip(baseline, candidate, strict=True):
        for field in ("python", "machine", "platform"):
            if old["platform"][field] != new["platform"][field]:
                raise ValueError(f"platform mismatch: {field}")
        for field in ("records", "value_size", "iterations", "warmup", "cursor_repeats", "cpu", "path"):
            if old["config"][field] != new["config"][field]:
                raise ValueError(f"benchmark configuration mismatch: {field}")
        for field in ("compiler", "flags", "options", "target"):
            if old["diagnostics"]["build"][field] != new["diagnostics"]["build"][field]:
                raise ValueError(f"native build mismatch: {field}")
        if old["diagnostics"]["libmdbx"] != new["diagnostics"]["libmdbx"]:
            raise ValueError("bundled libmdbx mismatch")
        for field in WORKLOADS:
            if old["results"]["clibmdbx"][field]["sample_digest"] != new["results"]["clibmdbx"][field]["sample_digest"]:
                raise ValueError(f"sample data mismatch: {field}")
    ratios = {}
    for workload in WORKLOADS:
        old = statistics.median(r["results"]["clibmdbx"][workload]["ops_per_second"] for r in baseline)
        new = statistics.median(r["results"]["clibmdbx"][workload]["ops_per_second"] for r in candidate)
        ratios[workload] = new / old
    old_first = statistics.median(
        r["results"]["clibmdbx"]["cursor_scan"]["first_scan_ops_per_second"] for r in baseline
    )
    new_first = statistics.median(
        r["results"]["clibmdbx"]["cursor_scan"]["first_scan_ops_per_second"] for r in candidate
    )
    ratios["cursor_first_scan"] = new_first / old_first
    for old, new in zip(baseline, candidate, strict=True):
        for report in (old, new):
            mixed = report["results"]["clibmdbx"]["mixed_read_write"]
            if mixed["write_operations"] != 10000 or mixed["reader_threads"] != 3:
                raise ValueError("mixed workload must complete all writes with three readers")
    for name, field in (("mixed_read", "read_ops_per_second"), ("mixed_write", "write_ops_per_second")):
        ratios[name] = statistics.median(
            new["results"]["clibmdbx"]["mixed_read_write"][field]
            / old["results"]["clibmdbx"]["mixed_read_write"][field]
            for old, new in zip(baseline, candidate, strict=True)
        )
    ratios["concurrent_read"] = statistics.median(
        new["results"]["clibmdbx"]["concurrent_read"]["ops_per_second"]
        / old["results"]["clibmdbx"]["concurrent_read"]["ops_per_second"]
        for old, new in zip(baseline, candidate, strict=True)
    )
    # New cross-revision guard: 5% is a disclosed measurement tolerance, not
    # permission to lower the original independent lifecycle/ctypes thresholds.
    regressions = {name: ratio for name, ratio in ratios.items() if ratio < 0.95}
    rss_old = statistics.median(r["rss_max_kib"] for r in baseline)
    rss_new = statistics.median(r["rss_max_kib"] for r in candidate)
    if rss_new > rss_old * 1.10 + 1024:
        regressions["rss_max_kib"] = rss_new / rss_old
    return {
        "throughput_ratios": ratios,
        "baseline_rss_kib": rss_old,
        "candidate_rss_kib": rss_new,
        "regressions": regressions,
    }


def run(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"benchmark command failed ({result.returncode}):\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-python", required=True)
    parser.add_argument("--candidate-python", required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--cpu", type=int, default=0)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("at least three rounds are required")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports: dict[str, list[dict]] = {"baseline": [], "candidate": []}
    with tempfile.TemporaryDirectory(prefix="clibmdbx-paired-", dir=os.environ.get("CLIBMDBX_BENCH_TMP")) as tmp:
        fixture = pathlib.Path(tmp) / "shared-read-fixture"
        run(
            [
                args.baseline_python,
                "-c",
                "import runpy,pathlib,sys; runpy.run_path(sys.argv[1])['populate'](pathlib.Path(sys.argv[2]),10000,32)",
                str(ROOT / "benchmark.py"),
                str(fixture),
            ]
        )
        data_path = fixture / "mdbx.dat"
        data_hash = hashlib.sha256(data_path.read_bytes()).hexdigest()
        for round_index in range(args.rounds):
            order = ("baseline", "candidate") if round_index % 2 == 0 else ("candidate", "baseline")
            for name in order:
                output = args.output_dir / f"{name}{round_index + 1}.json"
                executable = args.baseline_python if name == "baseline" else args.candidate_python
                run(
                    [
                        executable,
                        str(ROOT / "benchmark.py"),
                        "--path",
                        str(fixture),
                        "--reuse-existing",
                        "--cpu",
                        str(args.cpu),
                        "--output",
                        str(output),
                    ]
                )
                reports[name].append(json.loads(output.read_text()))
                print(f"completed {name} round {round_index + 1}", flush=True)
                if hashlib.sha256(data_path.read_bytes()).hexdigest() != data_hash:
                    raise RuntimeError("the shared read fixture was modified")
        result = check_regression(reports["baseline"], reports["candidate"])
        result.update(
            {
                "baseline_revision": args.baseline_revision,
                "candidate_revision": args.candidate_revision,
                "fixture_sha256": data_hash,
                "rounds": args.rounds,
                "order": "AB/BA alternating",
                "platform": platform.platform(),
                "allowed_cpus": sorted(os.sched_getaffinity(0)),
                "cpu": args.cpu,
            }
        )
        (args.output_dir / "comparison.json").write_text(json.dumps(result, indent=2, sort_keys=True))
        print(json.dumps(result, indent=2, sort_keys=True))
        if result["regressions"]:
            raise SystemExit("paired regression guard failed")


if __name__ == "__main__":
    main()
