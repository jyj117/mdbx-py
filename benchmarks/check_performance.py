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
    parser.add_argument(
        "--minimum-one-shot-to-reused-ratio",
        type=float,
        default=0.50,
        help="minimum Environment.get throughput relative to a reused Transaction.get snapshot",
    )
    parser.add_argument(
        "--minimum-one-shot-to-short-transaction-ratio",
        type=float,
        default=1.10,
        help="minimum Environment.get speedup over the equivalent explicit short-transaction path",
    )
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
    one_shot = native.get("one_shot_get_ops_per_second")
    if one_shot is None:
        failures.append("clibmdbx one-shot point benchmark is missing")
    else:
        one_shot_ratio = one_shot["median"] / native["point_get_ops_per_second"]["median"]
        if one_shot_ratio < args.minimum_one_shot_to_reused_ratio:
            failures.append(
                f"one-shot/reused ratio {one_shot_ratio:.2f}x is below {args.minimum_one_shot_to_reused_ratio:.2f}x"
            )
        print(f"one-shot/reused={one_shot_ratio:.2f}x")
    short_transaction = native.get("short_transaction_get_ops_per_second")
    if short_transaction is None:
        failures.append("clibmdbx explicit short-transaction benchmark is missing")
    elif one_shot is not None:
        short_ratio = one_shot["median"] / short_transaction["median"]
        if short_ratio < args.minimum_one_shot_to_short_transaction_ratio:
            failures.append(
                f"one-shot/short-transaction ratio {short_ratio:.2f}x is below "
                f"{args.minimum_one_shot_to_short_transaction_ratio:.2f}x"
            )
        print(f"one-shot/short-transaction={short_ratio:.2f}x")
    print(" ".join(f"{name}={ratio:.2f}x" for name, ratio in ratios.items()))
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
