#!/usr/bin/env python3
"""Fail a benchmark job when the C extension loses its conservative lead."""

from __future__ import annotations

import argparse
import json
import pathlib


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=pathlib.Path)
    parser.add_argument("--minimum-point-ratio", type=float, default=2.0)
    parser.add_argument("--minimum-batch-ratio", type=float, default=5.0)
    parser.add_argument("--minimum-cursor-ratio", type=float, default=5.0)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    engines = summary["engines"]
    native = engines["clibmdbx"]
    reference = engines["libmdbx_ctypes_0_3_2"]
    ratios = {
        "point": native["point_get_ops_per_second"]["median"] / reference["point_get_ops_per_second"]["median"],
        "batch": native["batch_get_ops_per_second_median"] / reference["batch_get_ops_per_second_median"],
        "cursor": native["cursor_scan_ops_per_second_median"] / reference["cursor_scan_ops_per_second_median"],
    }
    minimums = {
        "point": args.minimum_point_ratio,
        "batch": args.minimum_batch_ratio,
        "cursor": args.minimum_cursor_ratio,
    }
    failures = [
        f"{name} ratio {ratios[name]:.2f}x is below {minimums[name]:.2f}x"
        for name in ratios
        if ratios[name] < minimums[name]
    ]
    print(" ".join(f"{name}={ratio:.2f}x" for name, ratio in ratios.items()))
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
