#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.inputs]
    engines = sorted(set.intersection(*(set(report["results"]) for report in reports)))
    summary: dict[str, Any] = {"runs": [str(path) for path in args.inputs], "engines": {}}
    for engine in engines:
        point = [report["results"][engine]["point_get"] for report in reports]
        summary["engines"][engine] = {
            "point_get_ops_per_second": {
                "min": min(item["ops_per_second"] for item in point),
                "median": statistics.median(item["ops_per_second"] for item in point),
                "max": max(item["ops_per_second"] for item in point),
            },
            "point_get_latency_us_median_run": {
                percentile: statistics.median(item["latency_us"][percentile] for item in point)
                for percentile in ("p50", "p95", "p99")
            },
            "batch_get_ops_per_second_median": statistics.median(
                report["results"][engine]["batch_get"]["ops_per_second"] for report in reports
            ),
            "cursor_scan_ops_per_second_median": statistics.median(
                report["results"][engine]["cursor_scan"]["ops_per_second"] for report in reports
            ),
        }
        if all("one_shot_get" in report["results"][engine] for report in reports):
            one_shot = [report["results"][engine]["one_shot_get"] for report in reports]
            summary["engines"][engine]["one_shot_get_ops_per_second"] = {
                "min": min(item["ops_per_second"] for item in one_shot),
                "median": statistics.median(item["ops_per_second"] for item in one_shot),
                "max": max(item["ops_per_second"] for item in one_shot),
            }
            summary["engines"][engine]["one_shot_get_latency_us_median_run"] = {
                percentile: statistics.median(item["latency_us"][percentile] for item in one_shot)
                for percentile in ("p50", "p95", "p99")
            }
        if all("short_transaction_get" in report["results"][engine] for report in reports):
            short_transaction = [report["results"][engine]["short_transaction_get"] for report in reports]
            summary["engines"][engine]["short_transaction_get_ops_per_second"] = {
                "min": min(item["ops_per_second"] for item in short_transaction),
                "median": statistics.median(item["ops_per_second"] for item in short_transaction),
                "max": max(item["ops_per_second"] for item in short_transaction),
            }
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
