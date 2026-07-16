from __future__ import annotations

import argparse
import asyncio
import csv
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import httpx


ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE = ROOT / "rapidocr_test.png"
REPORT_DIR = ROOT / "reports"


def core_affinity_mask(core_count: int) -> int:
    if core_count < 1:
        raise ValueError("core_count must be at least 1")
    return (1 << core_count) - 1


def percentile(values: Iterable[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_run(
    core_count: int,
    concurrency: int,
    elapsed_seconds: float,
    ok_latencies: list[float],
    error_count: int,
    workers: int = 1,
    ort_threads: int | None = None,
) -> dict:
    success = len(ok_latencies)
    total = success + error_count
    qps = success / elapsed_seconds if elapsed_seconds > 0 else 0.0
    avg = sum(ok_latencies) / success if success else 0.0

    return {
        "core_count": core_count,
        "workers": workers,
        "ort_threads": ort_threads or "",
        "concurrency": concurrency,
        "requests": total,
        "success": success,
        "errors": error_count,
        "success_rate": round((success / total * 100.0) if total else 0.0, 2),
        "elapsed_s": round(elapsed_seconds, 3),
        "qps": round(qps, 3),
        "avg_ms": round(avg * 1000.0, 3),
        "p50_ms": round(percentile(ok_latencies, 50) * 1000.0, 3),
        "p90_ms": round(percentile(ok_latencies, 90) * 1000.0, 3),
        "p95_ms": round(percentile(ok_latencies, 95) * 1000.0, 3),
        "p99_ms": round(percentile(ok_latencies, 99) * 1000.0, 3),
    }


def parse_int_list(value: str) -> list[int]:
    items = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not items:
        raise argparse.ArgumentTypeError("list cannot be empty")
    if any(item < 1 for item in items):
        raise argparse.ArgumentTypeError("all values must be positive integers")
    return items


def parse_optional_int_list(value: str) -> list[int | None]:
    if not value or value.strip().lower() == "auto":
        return [None]
    return parse_int_list(value)


def build_server_env(
    base_env: dict[str, str],
    core_count: int,
    ort_threads: int | None,
    max_concurrency: int,
    preload: bool,
) -> dict[str, str]:
    env = base_env.copy()
    env.update(
        {
            "OMP_NUM_THREADS": str(core_count),
            "OPENBLAS_NUM_THREADS": str(core_count),
            "MKL_NUM_THREADS": str(core_count),
            "NUMEXPR_NUM_THREADS": str(core_count),
            "OCR_MAX_CONCURRENCY": str(max_concurrency),
            "OCR_PRELOAD": "true" if preload else "false",
        }
    )
    if ort_threads is not None:
        env["OCR_ORT_INTRA_THREADS"] = str(ort_threads)
        env["OCR_ORT_INTER_THREADS"] = "1"
    return env


def set_process_affinity(pid: int, core_count: int) -> None:
    mask = core_affinity_mask(core_count)
    if os.name == "nt":
        process_set_information = 0x0200
        process_query_information = 0x0400
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_set_information | process_query_information, False, pid
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel32.SetProcessAffinityMask(handle, mask):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.CloseHandle(handle)
        return

    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(pid, set(range(core_count)))


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_server_command(port: int, workers: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "ocr_server:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--log-level",
        "warning",
    ]


async def wait_for_health(base_url: str, process: subprocess.Popen, timeout_s: float) -> None:
    deadline = time.perf_counter() + timeout_s
    async with httpx.AsyncClient(timeout=2.0) as client:
        last_error = None
        while time.perf_counter() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"server exited with code {process.returncode}")
            try:
                response = await client.get(f"{base_url}/health")
                if response.status_code == 200:
                    return
            except httpx.HTTPError as exc:
                last_error = exc
            await asyncio.sleep(0.2)
    raise TimeoutError(f"server did not become healthy: {last_error}")


def start_server(
    core_count: int,
    port: int,
    workers: int,
    ort_threads: int | None,
    max_concurrency: int,
) -> subprocess.Popen:
    env = build_server_env(
        base_env=os.environ,
        core_count=core_count,
        ort_threads=ort_threads,
        max_concurrency=max_concurrency,
        preload=True,
    )
    process = subprocess.Popen(
        build_server_command(port=port, workers=workers),
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    set_process_affinity(process.pid, core_count)
    return process


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


async def warmup(base_url: str, image_path: Path, requests: int) -> dict:
    payload = {"image_path": str(image_path)}
    async with httpx.AsyncClient(timeout=60.0) as client:
        last_payload = {}
        for _ in range(requests):
            response = await client.post(f"{base_url}/api/measure/ocr", json=payload)
            response.raise_for_status()
            last_payload = response.json()
        return last_payload


async def benchmark_once(
    base_url: str,
    image_path: Path,
    core_count: int,
    workers: int,
    ort_threads: int | None,
    concurrency: int,
    duration_s: float,
) -> dict:
    payload = {"image_path": str(image_path)}
    ok_latencies: list[float] = []
    error_count = 0
    deadline = time.perf_counter() + duration_s

    async with httpx.AsyncClient(timeout=90.0) as client:
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal error_count
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                try:
                    response = await client.post(
                        f"{base_url}/api/measure/ocr", json=payload
                    )
                    response.raise_for_status()
                    elapsed = time.perf_counter() - started
                    async with lock:
                        ok_latencies.append(elapsed)
                except Exception:
                    async with lock:
                        error_count += 1

        run_started = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        elapsed_seconds = time.perf_counter() - run_started

    return summarize_run(
        core_count=core_count,
        workers=workers,
        ort_threads=ort_threads,
        concurrency=concurrency,
        elapsed_seconds=elapsed_seconds,
        ok_latencies=ok_latencies,
        error_count=error_count,
    )


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "core_count",
        "workers",
        "ort_threads",
        "concurrency",
        "requests",
        "success",
        "errors",
        "success_rate",
        "elapsed_s",
        "qps",
        "avg_ms",
        "p50_ms",
        "p90_ms",
        "p95_ms",
        "p99_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[dict]) -> str:
    lines = [
        "| CPU核心数 | Worker数 | ORT线程 | 并发数 | QPS | 成功率 | 平均延迟(ms) | P95(ms) | P99(ms) | 成功/总请求 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {core_count} | {workers} | {ort_threads} | {concurrency} | {qps:.3f} | "
            "{success_rate:.2f}% | {avg_ms:.1f} | {p95_ms:.1f} | {p99_ms:.1f} | "
            "{success}/{requests} |".format(**row)
        )
    return "\n".join(lines)


def best_rows_by_core(rows: list[dict]) -> list[dict]:
    cores = sorted({row["core_count"] for row in rows})
    best = []
    for core in cores:
        core_rows = [row for row in rows if row["core_count"] == core]
        best.append(max(core_rows, key=lambda row: row["qps"]))
    return best


def write_report(
    rows: list[dict],
    path: Path,
    csv_path: Path,
    image_path: Path,
    duration_s: float,
    warmup_requests: int,
    sample_response: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    best_rows = best_rows_by_core(rows)
    single_core_peak = next(
        (row["qps"] for row in best_rows if row["core_count"] == 1), 0.0
    )

    best_lines = [
        "| CPU核心数 | Worker数 | ORT线程 | 最佳并发 | 峰值QPS | 相对单核峰值 | P95(ms) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in best_rows:
        speedup = row["qps"] / single_core_peak if single_core_peak else 0.0
        best_lines.append(
            f"| {row['core_count']} | {row['workers']} | {row['ort_threads']} | "
            f"{row['concurrency']} | {row['qps']:.3f} | {speedup:.2f}x | "
            f"{row['p95_ms']:.1f} |"
        )

    best_overall = max(rows, key=lambda row: row["qps"])
    content = f"""# OCR服务单核/多核QPS压测报告

生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 测试口径

- 被测接口：`POST /api/measure/ocr`
- 启动方式：`uvicorn ocr_server:app`
- CPU控制：每轮启动独立服务进程，并设置进程 CPU 亲和性为前 N 个逻辑核心
- 线程控制：同步设置 `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`MKL_NUM_THREADS`、`NUMEXPR_NUM_THREADS`
- OCR控制：通过 `OCR_PRELOAD`、`OCR_MAX_CONCURRENCY`、`OCR_ORT_INTRA_THREADS`、`OCR_ORT_INTER_THREADS` 控制服务行为
- 压测请求：JSON `image_path`，固定图片 `{image_path.name}`
- 单轮时长：{duration_s} 秒
- 统计方式：到达单轮时长后停止发起新请求，已发起请求会等待完成；各组合真实完成耗时见 CSV/表格 `elapsed_s`
- 预热请求：{warmup_requests} 次
- 机器可见逻辑核心数：{os.cpu_count()}
- 明细数据：`{csv_path.name}`

预热样例响应：

```json
{json.dumps(sample_response, ensure_ascii=False, indent=2)}
```

## 结论

- 本轮最高 QPS：{best_overall['qps']:.3f}，出现在 {best_overall['core_count']} 核、{best_overall['workers']} worker、ORT线程 {best_overall['ort_threads']}、并发 {best_overall['concurrency']}。
- 单核峰值：{single_core_peak:.3f} QPS。
- 最优并发通常出现在延迟开始明显上升前；继续增加并发会提高排队时间，但不一定提高吞吐。

## 各核心数峰值

{chr(10).join(best_lines)}

## 全量结果

{markdown_table(rows)}
"""
    path.write_text(content, encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    image_path = Path(args.image)
    if not image_path.is_absolute():
        image_path = ROOT / image_path
    if not image_path.exists():
        raise FileNotFoundError(image_path)

    cpu_count = os.cpu_count() or 1
    core_counts = [core for core in args.core_counts if core <= cpu_count]
    if not core_counts:
        raise ValueError(f"no requested core count is <= available cores ({cpu_count})")

    rows: list[dict] = []
    sample_response: dict = {}
    for core_count in core_counts:
        for workers in args.workers:
            for ort_threads in args.ort_threads:
                port = available_port()
                base_url = f"http://127.0.0.1:{port}"
                max_concurrency = max(args.concurrency)
                process = start_server(
                    core_count=core_count,
                    port=port,
                    workers=workers,
                    ort_threads=ort_threads,
                    max_concurrency=max_concurrency,
                )
                try:
                    await wait_for_health(base_url, process, timeout_s=90.0)
                    sample_response = await warmup(
                        base_url, image_path, args.warmup_requests
                    )
                    for concurrency in args.concurrency:
                        print(
                            "running cores={core_count}, workers={workers}, "
                            "ort_threads={ort_threads}, concurrency={concurrency}".format(
                                core_count=core_count,
                                workers=workers,
                                ort_threads=ort_threads or "auto",
                                concurrency=concurrency,
                            ),
                            flush=True,
                        )
                        row = await benchmark_once(
                            base_url=base_url,
                            image_path=image_path,
                            core_count=core_count,
                            workers=workers,
                            ort_threads=ort_threads,
                            concurrency=concurrency,
                            duration_s=args.duration,
                        )
                        print(
                            "  qps={qps:.3f}, avg={avg_ms:.1f}ms, p95={p95_ms:.1f}ms, "
                            "success={success}/{requests}, errors={errors}".format(**row),
                            flush=True,
                        )
                        rows.append(row)
                finally:
                    stop_server(process)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = REPORT_DIR / f"ocr_qps_results_{timestamp}.csv"
    report_path = REPORT_DIR / f"ocr_qps_report_{timestamp}.md"
    write_csv(rows, csv_path)
    write_report(
        rows=rows,
        path=report_path,
        csv_path=csv_path,
        image_path=image_path,
        duration_s=args.duration,
        warmup_requests=args.warmup_requests,
        sample_response=sample_response,
    )
    print(f"csv: {csv_path}", flush=True)
    print(f"report: {report_path}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark OCR API QPS by CPU cores.")
    parser.add_argument("--image", default=str(DEFAULT_IMAGE), help="image path to OCR")
    parser.add_argument(
        "--core-counts",
        type=parse_int_list,
        default=parse_int_list("1,2,4,8"),
        help="comma-separated CPU core counts, for example 1,2,4,8",
    )
    parser.add_argument(
        "--workers",
        type=parse_int_list,
        default=parse_int_list("1"),
        help="comma-separated uvicorn worker counts, for example 1,2,4",
    )
    parser.add_argument(
        "--ort-threads",
        type=parse_optional_int_list,
        default=parse_optional_int_list("auto"),
        help="comma-separated ONNXRuntime intra-op thread counts, or auto",
    )
    parser.add_argument(
        "--concurrency",
        type=parse_int_list,
        default=parse_int_list("1,2,4,8,16"),
        help="comma-separated concurrency levels, for example 1,2,4,8,16",
    )
    parser.add_argument("--duration", type=float, default=10.0, help="seconds per run")
    parser.add_argument(
        "--warmup-requests", type=int, default=2, help="warmup OCR requests per server"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
