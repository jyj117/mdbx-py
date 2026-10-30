#!/usr/bin/env python3
"""Reproducible synthetic clibmdbx versus ctypes-libmdbx benchmark.

The point-read comparison keeps one read transaction and one DBI alive for the
whole timed region on both bindings. Test data is generated locally; no FSJ or
company data is read or packaged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import resource
import statistics
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

import clibmdbx


def pin_current_thread(cpu: int | None) -> set[int] | None:
    if cpu is None:
        return None
    original = os.sched_getaffinity(0)
    if cpu not in original:
        raise ValueError(f"CPU {cpu} is not in this process's allowed affinity set {sorted(original)}")
    os.sched_setaffinity(0, {cpu})
    return original


def restore_current_thread_affinity(original: set[int] | None) -> None:
    if original is not None:
        os.sched_setaffinity(0, original)


def percentile(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index] / 1_000


def timed_reads(
    get: Callable[[bytes], bytes | None], keys: list[bytes], iterations: int, warmup: int
) -> dict[str, Any]:
    digest = hashlib.sha256()
    for i in range(warmup):
        value = get(keys[i % len(keys)])
        if value is None:
            raise AssertionError("warmup lookup missed")
    latencies: list[int] = []
    cpu_start = time.process_time_ns()
    wall_start = time.perf_counter_ns()
    for i in range(iterations):
        started = time.perf_counter_ns()
        value = get(keys[i % len(keys)])
        latencies.append(time.perf_counter_ns() - started)
        if value is None:
            raise AssertionError("timed lookup missed")
        if i % 1024 == 0:
            digest.update(value)
    wall_ns = time.perf_counter_ns() - wall_start
    cpu_ns = time.process_time_ns() - cpu_start
    return {
        "operations": iterations,
        "seconds": wall_ns / 1e9,
        "ops_per_second": iterations * 1e9 / wall_ns,
        "cpu_seconds": cpu_ns / 1e9,
        "average_cores": cpu_ns / wall_ns,
        "latency_us": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "mean": statistics.fmean(latencies) / 1_000,
        },
        "sample_digest": digest.hexdigest(),
    }


def populate(path: pathlib.Path, records: int, value_size: int) -> list[bytes]:
    path.mkdir(parents=True)
    keys = [i.to_bytes(8, "big") for i in range(records)]
    env = clibmdbx.Environment(path, geometry=(0, 1 << 20, 1 << 31, 1 << 24, 1 << 25, -1))
    try:
        with env.write() as txn:
            txn.put_many([(key, hashlib.blake2b(key, digest_size=value_size).digest()) for key in keys])
        env.sync()
    finally:
        env.close()
    return keys


def validate_fixture(path: pathlib.Path, records: int, value_size: int) -> list[bytes]:
    """Validate every deterministic record before reusing a read-only fixture."""
    keys = [i.to_bytes(8, "big") for i in range(records)]
    with clibmdbx.Environment(path, readonly=True) as env:
        with env.read() as txn:
            assert txn.stat()["entries"] == records, "fixture record count differs"
            for key in keys:
                assert txn.get(key) == hashlib.blake2b(key, digest_size=value_size).digest(), "fixture data differs"
    return keys


def timed_cursor(scan: Callable, records: int, repeats: int) -> dict[str, Any]:
    # Equal work for both bindings; one short scan is too sensitive to Python
    # allocator/GC scheduling to establish a cross-revision performance change.
    first_start = time.perf_counter_ns()
    warm = scan()
    first_scan_ns = time.perf_counter_ns() - first_start
    assert len(warm) == records
    del warm
    elapsed_samples = []
    digest = hashlib.sha256()
    for _ in range(repeats):
        start = time.perf_counter_ns()
        items = scan()
        elapsed_samples.append(time.perf_counter_ns() - start)
        assert len(items) == records
        for key, value in items:
            digest.update(key)
            digest.update(value)
        del items
    elapsed = sum(elapsed_samples)
    operations = records * repeats
    return {
        "operations": operations,
        "seconds": elapsed / 1e9,
        "ops_per_second": operations * 1e9 / elapsed,
        "samples_ns": elapsed_samples,
        "sample_digest": digest.hexdigest(),
        "first_scan_ns": first_scan_ns,
        "first_scan_ops_per_second": records * 1e9 / first_scan_ns,
    }


def bench_clib(path: pathlib.Path, keys: list[bytes], args: argparse.Namespace) -> dict[str, Any]:
    env = clibmdbx.Environment(path, readonly=True)
    try:
        original_affinity = pin_current_thread(args.cpu)
        try:
            one_shot = timed_reads(env.get, keys, args.iterations, args.warmup)

            def short_transaction_get(key: bytes) -> bytes | None:
                short_txn = env.read()
                try:
                    return short_txn.get(key)
                finally:
                    short_txn.abort()

            short_transaction = timed_reads(short_transaction_get, keys, args.iterations, args.warmup)
            txn = env.read()
            try:
                point = timed_reads(txn.get, keys, args.iterations, args.warmup)
                batches = [keys[i : i + args.batch_size] for i in range(0, len(keys), args.batch_size)]
                start = time.perf_counter_ns()
                operations = 0
                digest = hashlib.sha256()
                for i in range(args.batch_iterations):
                    values = txn.get_many(batches[i % len(batches)])
                    operations += len(values)
                    digest.update(values[0])
                elapsed = time.perf_counter_ns() - start
                batch = {
                    "operations": operations,
                    "seconds": elapsed / 1e9,
                    "ops_per_second": operations * 1e9 / elapsed,
                    "sample_digest": digest.hexdigest(),
                    "boundary_calls": args.batch_iterations,
                }
                cur = txn.cursor()
                try:
                    cursor = timed_cursor(lambda: cur.items(limit=len(keys)), len(keys), args.cursor_repeats)
                finally:
                    cur.close()
            finally:
                txn.abort()
        finally:
            restore_current_thread_affinity(original_affinity)

        start = 0

        def start_readers() -> None:
            nonlocal start
            start = time.perf_counter_ns()

        # Timestamp before the barrier releases any worker. Starting after the
        # coordinator's wait() returns omits reads completed before it gets the
        # GIL back, and can report near-zero elapsed time for the entire batch.
        barrier = threading.Barrier(args.threads + 1, action=start_readers)
        thread_digests: list[str] = [""] * args.threads

        def reader(index: int) -> None:
            local = env.read()
            digest = hashlib.sha256()
            barrier.wait()
            for i in range(args.concurrent_iterations):
                value = local.get(keys[(i + index * 997) % len(keys)])
                if value is None:
                    raise AssertionError("concurrent lookup missed")
                if i % 1024 == 0:
                    digest.update(value)
            local.abort()
            thread_digests[index] = digest.hexdigest()

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(args.threads)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        elapsed = time.perf_counter_ns() - start
        if not all(thread_digests):
            raise AssertionError("a concurrent reader did not complete its workload")
        concurrent_ops = args.threads * args.concurrent_iterations
        concurrent = {
            "threads": args.threads,
            "operations": concurrent_ops,
            "seconds": elapsed / 1e9,
            "ops_per_second": concurrent_ops * 1e9 / elapsed,
            "thread_digests": thread_digests,
        }
        return {
            "one_shot_get": one_shot,
            "short_transaction_get": short_transaction,
            "point_get": point,
            "batch_get": batch,
            "cursor_scan": cursor,
            "concurrent_read": concurrent,
        }
    finally:
        env.close()


def bench_ctypes(path: pathlib.Path, keys: list[bytes], args: argparse.Namespace) -> dict[str, Any]:
    import mdbx  # type: ignore[import-not-found]

    original_affinity = pin_current_thread(args.cpu)
    try:
        env = mdbx.Env(str(path), flags=mdbx.MDBXEnvFlags.MDBX_RDONLY, maxreaders=64, maxdbs=16)
        try:
            txn = env.ro_transaction()
            db = txn.open_map(None)
            try:
                point = timed_reads(lambda key: db.get(txn, key), keys, args.iterations, args.warmup)
                batches = [keys[i : i + args.batch_size] for i in range(0, len(keys), args.batch_size)]
                start = time.perf_counter_ns()
                operations = 0
                digest = hashlib.sha256()
                for i in range(args.batch_iterations):
                    values = [db.get(txn, key) for key in batches[i % len(batches)]]
                    operations += len(values)
                    digest.update(values[0])
                elapsed = time.perf_counter_ns() - start
                batch = {
                    "operations": operations,
                    "seconds": elapsed / 1e9,
                    "ops_per_second": operations * 1e9 / elapsed,
                    "sample_digest": digest.hexdigest(),
                    "boundary_calls": operations,
                }
                cur = txn.cursor(db)
                try:
                    cursor = timed_cursor(lambda: list(cur.iter()), len(keys), args.cursor_repeats)
                finally:
                    cur.close()
            finally:
                txn.abort()
            return {"point_get": point, "batch_get": batch, "cursor_scan": cursor}
        finally:
            env.close()
    finally:
        restore_current_thread_affinity(original_affinity)


def bench_mixed_clib(path: pathlib.Path, args: argparse.Namespace) -> dict[str, Any]:
    keys = populate(path, args.records, args.value_size)
    env = clibmdbx.Environment(path)
    stop = threading.Event()
    cpu_start = started = 0

    def start_mixed() -> None:
        nonlocal cpu_start, started
        cpu_start = time.process_time_ns()
        started = time.perf_counter_ns()

    barrier = threading.Barrier(args.threads, action=start_mixed)
    reader_counts = [0] * (args.threads - 1)

    def reader(index: int) -> None:
        txn = env.read()
        barrier.wait()
        count = 0
        while not stop.is_set():
            value = txn.get(keys[count % len(keys)])
            if value is None:
                raise AssertionError("mixed reader lookup missed")
            count += 1
        txn.abort()
        reader_counts[index] = count

    readers = [threading.Thread(target=reader, args=(i,)) for i in range(args.threads - 1)]
    for thread in readers:
        thread.start()
    barrier.wait()
    write_operations = 0
    try:
        for batch in range(100):
            with env.write() as txn:
                for offset in range(100):
                    index = (batch * 100 + offset) % len(keys)
                    txn.put(keys[index], (batch * 100 + offset).to_bytes(args.value_size, "little"))
                    write_operations += 1
    finally:
        stop.set()
        for thread in readers:
            thread.join()
        elapsed = time.perf_counter_ns() - started
        cpu = time.process_time_ns() - cpu_start
        env.close()
    reads = sum(reader_counts)
    if not all(reader_counts):
        raise AssertionError("a mixed reader did not complete its workload")
    return {
        "reader_threads": len(readers),
        "read_operations": reads,
        "write_operations": write_operations,
        "seconds": elapsed / 1e9,
        "read_ops_per_second": reads * 1e9 / elapsed,
        "write_ops_per_second": write_operations * 1e9 / elapsed,
        "cpu_seconds": cpu / 1e9,
        "average_cores": cpu / elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--value-size", type=int, default=32)
    parser.add_argument("--iterations", type=int, default=500_000)
    parser.add_argument("--warmup", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--batch-iterations", type=int, default=5_000)
    parser.add_argument("--cursor-repeats", type=int, default=9)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--concurrent-iterations", type=int, default=100_000)
    parser.add_argument("--cpu", type=int, help="pin single-thread workloads to one allowed Linux CPU")
    parser.add_argument("--skip-ctypes", action="store_true")
    parser.add_argument("--reuse-existing", action="store_true", help="validate and read an existing synthetic fixture")
    args = parser.parse_args()
    if min(args.records, args.value_size, args.iterations, args.batch_size, args.threads, args.cursor_repeats) <= 0:
        parser.error("record, value, iteration, batch, and thread counts must be positive")
    if args.value_size > 64:
        parser.error("value-size must be <= 64 for deterministic BLAKE2 test values")
    if args.threads < 2:
        parser.error("threads must be >= 2 for concurrent and mixed tests")
    if args.cpu is not None and not hasattr(os, "sched_getaffinity"):
        parser.error("--cpu requires os.sched_getaffinity/os.sched_setaffinity")

    temporary = None
    if args.path is None:
        temporary = tempfile.TemporaryDirectory(prefix="clibmdbx-bench-", dir=os.environ.get("CLIBMDBX_BENCH_TMP"))
        args.path = pathlib.Path(temporary.name) / "db"
    keys = (
        validate_fixture(args.path, args.records, args.value_size)
        if args.reuse_existing
        else populate(args.path, args.records, args.value_size)
    )
    report: dict[str, Any] = {
        "schema": 1,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "config": vars(args) | {"path": str(args.path), "output": str(args.output)},
        "diagnostics": clibmdbx.diagnostics(),
        "results": {"clibmdbx": bench_clib(args.path, keys, args)},
    }
    if not args.skip_ctypes:
        try:
            report["results"]["libmdbx_ctypes_0_3_2"] = bench_ctypes(args.path, keys, args)
            native = report["results"]["clibmdbx"]
            reference = report["results"]["libmdbx_ctypes_0_3_2"]
            for workload in ("point_get", "batch_get", "cursor_scan"):
                if native[workload]["sample_digest"] != reference[workload]["sample_digest"]:
                    raise RuntimeError(f"{workload} compared different data across bindings")
        except (ImportError, OSError) as exc:
            report["ctypes_skipped"] = f"{type(exc).__name__}: {exc}"
    # A paired comparison reuses the same read-only fixture across processes.
    # Mixed writes must never alter it or collide with another run's database.
    with tempfile.TemporaryDirectory(prefix="clibmdbx-mixed-", dir=args.path.parent) as mixed:
        report["results"]["clibmdbx"]["mixed_read_write"] = bench_mixed_clib(pathlib.Path(mixed) / "db", args)
    report["rss_max_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["results"], indent=2, sort_keys=True))
    if temporary is not None:
        temporary.cleanup()


if __name__ == "__main__":
    main()
