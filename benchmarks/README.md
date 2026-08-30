# Benchmarks

Run on a native local filesystem (not an SMB/NFS mount and not WSL `/mnt/c`):

```bash
python benchmarks/benchmark.py --cpu 0 --output benchmarks/results/run.json
```

On Linux, `--cpu` pins only the single-thread point, batch, and cursor
workloads to one allowed CPU to reduce scheduler migration noise. The benchmark
restores the original affinity before concurrent-read and mixed-read/write
tests. Omit it when affinity control is unavailable, and record that fact.

The default point tests perform 100,000 warmup reads followed by 500,000 timed
reads. `one_shot_get` measures `Environment.get()`, including its fresh short
read transaction. `short_transaction_get` measures the equivalent pre-1.0.2
Python sequence of creating, reading, and aborting one transaction per key;
the release gate requires `one_shot_get` to be at least 1.1x faster.
`point_get` reuses one read transaction and is the matched
ctypes comparison: the ctypes adapter uses `libmdbx==0.3.2`, one read
transaction, one DBI, and `DBI.get(txn, key)`. Batch results intentionally
compare one native boundary crossing against a Python/ctypes loop.

Run at least three times after the first page-cache-warming run. Report all raw
JSON files, CPU time, percentiles, and filesystem. MDBX maps the data file, so
RSS includes mapped virtual pages and must not be interpreted as private heap.
`check_performance.py` makes the hosted three-run job fail unless median point
reads remain at least 2×, batch reads 5×, and cursor scans 5× the pinned ctypes
reference. It also requires one-shot throughput to remain at least 50% of the
semantically different reused-transaction path. These are conservative
regression floors, not expected performance.

## 1.0.3 final review

Three CPU-0-pinned runs on 2026-08-30 used CPython 3.10.12, GCC 11.4,
Linux x86_64 under WSL2 and databases on the native `/tmp` filesystem. The
host varied substantially during the sequence: reused-transaction point reads
ranged from 330,418 to 787,893 ops/s and four-reader throughput from 484,701 to
1,039,117 ops/s, while CPU/wall ratios stayed near one. No runs were discarded.

The conservative three-run medians were 349,100 point reads/s (P50/P95/P99
1.653/2.604/5.380 us), 1,306,329 batch keys/s and 1,305,641 cursor rows/s.
These remained 5.90x, 21.00x and 26.84x faster than the pinned ctypes 0.3.2
reference. `Environment.get()` was 1.74x faster than its equivalent explicit
short-transaction sequence, and all digest comparisons matched. Mixed writers
completed 10,000 operations in every run; maximum RSS was 48,708--49,912 KiB.

An immediate same-build spot check outside the throttled sequence reached
861,068 reused-transaction point reads/s with P50/P95/P99 of
0.796/0.982/1.595 us. A same-period 1.0.2 wheel A/B reached P50 0.832 us, so the
unique thread-state owner check showed no measurable hot-path regression in
the comparable non-throttled samples. Raw runs, the aggregate and the spot
check are in `benchmarks/results/1.0.3-final/`. These WSL measurements are a
regression gate, not a substitute for a native production-host benchmark.
The same directory also retains the three same-period 1.0.2 comparison runs
and their summary rather than relying on a previously recorded baseline.
`production-edges-linux.json` and `disk-full-linux.json` retain the quantitative
failure-mode audit and the real 16 MiB filesystem-exhaustion result used by the
1.0.3 release gate. `orphaned-writer-wait-linux.json` records the additional
combined fault in which a writer was already queued before the current writer
was finalized off-owner and its owner exited; the binding-level gate woke it
with `BusyError` in 0.252 seconds before it entered libmdbx.

The final post-gate regression run is `writer-gate-final.json`. It reached
446,369 warm point reads/s (P50/P95/P99 1.341/2.135/4.156 us), 1,562,470 batch
keys/s and 1,510,873 cursor rows/s: 8.74x, 22.60x and 29.42x the pinned ctypes
0.3.2 reference. The mixed workload completed all 10,000 writes at 2,387
writes/s while readers sustained 577,634 reads/s; maximum RSS was 49,364 KiB.
`Environment.get()` reached 359,885 reads/s, 1.87x its equivalent explicit
short-transaction path. This confirms the final writer-operation gate's
uncontended `NOWAIT` path did not regress read hot paths or mixed-write
progress on the noisy review host; both bindings slowed in this retained run.

## 1.0.2 final review

Three unpinned release-build runs on 2026-08-30 used CPython 3.10.12, GCC
11.4, Linux x86_64 under WSL2, and a database on WSL's native `/tmp`
filesystem. `Environment.get()` reached a 424,823 ops/s median with P50/P95/P99
of 1.805/2.465/4.105 us. The equivalent explicit short-transaction path reached
362,067 ops/s, so the new path was 1.17x faster while preserving a fresh
snapshot per call. The reused-transaction path remained faster at 736,557
ops/s, as expected for different snapshot semantics.

Against `libmdbx==0.3.2` ctypes, the matched reused-transaction point, 100-key
batch, and cursor medians were 9.16x, 26.25x, and 33.47x faster respectively;
sample digests matched. Four-reader throughput ranged from 1.02 to 1.26 million
ops/s. Mixed-write progress completed in every run, and maximum RSS remained
within 49,416--49,592 KiB. The raw results and aggregate are in
`benchmarks/results/1.0.2-final/`.

## Recorded final-review release-candidate result

Three final-review pre-release-candidate runs (recorded with binding version
0.1.0a1) on 2026-08-29 used CPython 3.10.12, GCC 11.4,
Linux x86_64 under WSL2, and a database on WSL's native `/tmp` filesystem. The
single-thread workloads were pinned to CPU 0, the page cache was warmed before
timing, and the sample digests match across bindings.

| Workload | clibmdbx median | ctypes 0.3.2 median |
| --- | ---: | ---: |
| Warm point get | 1,127,294 ops/s | 81,555 ops/s |
| Point get P50 | 0.622 us | 8.416 us |
| Point get P95 | 0.698 us | 20.960 us |
| Point get P99 | 1.178 us | 50.305 us |
| 100-key batch get | 3,212,356 keys/s | 111,269 keys/s |
| Cursor scan | 4,097,711 rows/s | 97,192 rows/s |

The median advantages were 13.82x for point get, 28.87x for batch get and
42.16x for cursor scan. clibmdbx warm point-get range was 1,008,730 to
1,196,520 ops/s across the three measured runs. The exact inputs and aggregate
are `review-20260829-run1.json` through `review-20260829-run3.json` and
`review-20260829-summary.json`. Earlier pinned runs are retained for regression
history. Six additional unpinned runs are retained as
`wsl-x86_64-audit-run1.json` through `wsl-x86_64-audit-run6.json`; their 836,066
ops/s median and wide spread demonstrate the WSL host's scheduler noise rather
than being discarded. Raw files also contain four-thread read throughput,
mixed read/write throughput, CPU time, maximum RSS and build diagnostics. The
test is synthetic and page-cache-hot; it does not predict cold-device latency.
Mapped pages make RSS a high-water view of the process address space/working
set, not an allocation or leak measurement.

After adding the full wtdcode compatibility suite, a fresh same-code regression
run (`wsl-x86_64-wtd-parity-run.json`) produced 394,452 versus 60,688 warm point
gets/s (6.50×), 1,661,099 versus 77,127 batch keys/s (21.54×), and 2,143,105
versus 64,472 cursor rows/s (33.24×). Both sample digests matched. This single
run was slower than the recorded three-run median on both bindings because of
WSL host noise; it is retained rather than substituted for the three-run
summary.
