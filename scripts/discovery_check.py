#!/usr/bin/env python3
"""Probe and benchmark Klimatronik LAN discovery against real devices."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import select
import socket
import struct
import time
from dataclasses import dataclass
from typing import Callable

ACK_TOKEN = b"ccmdiAuthorizecerrceok"
PORT = 8080


@dataclass(slots=True)
class ProbeResult:
    ip: str
    ok: bool
    duration_ms: float
    error: str | None = None


def _auth_frame() -> bytes:
    payload = b"ccmdiAuthorizecpin" + bytes([0x1A, 0xFF, 0xFF, 0x00, 0x00])
    return struct.pack(">HB", len(payload) + 1, 0xA2) + payload


def _probe_legacy(ip: str, port: int, connect_timeout: float, probe_timeout: float) -> ProbeResult:
    start = time.perf_counter()
    frame = _auth_frame()
    for _ in range(2):
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection((ip, port), timeout=connect_timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.sendall(frame)
            sock.setblocking(False)
            raw = b""
            deadline = time.time() + probe_timeout
            while time.time() < deadline:
                try:
                    chunk = sock.recv(8192)
                except BlockingIOError:
                    time.sleep(0.03)
                    continue
                if not chunk:
                    break
                raw += chunk
                if ACK_TOKEN in raw:
                    return ProbeResult(ip=ip, ok=True, duration_ms=(time.perf_counter() - start) * 1000.0)
        except OSError as err:
            last_error = str(err)
        else:
            last_error = None
        finally:
            if sock is not None:
                sock.close()
        time.sleep(0.05)
    return ProbeResult(
        ip=ip,
        ok=False,
        duration_ms=(time.perf_counter() - start) * 1000.0,
        error=last_error,
    )


def _probe_fast(ip: str, port: int, connect_timeout: float, probe_timeout: float) -> ProbeResult:
    start = time.perf_counter()
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((ip, port), timeout=connect_timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(_auth_frame())

        raw = b""
        deadline = time.monotonic() + probe_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            wait = min(remaining, 0.15)
            readable, _, _ = select.select([sock], [], [], wait)
            if not readable:
                continue
            chunk = sock.recv(8192)
            if not chunk:
                break
            raw += chunk
            if ACK_TOKEN in raw:
                return ProbeResult(ip=ip, ok=True, duration_ms=(time.perf_counter() - start) * 1000.0)
        return ProbeResult(ip=ip, ok=False, duration_ms=(time.perf_counter() - start) * 1000.0, error="ack timeout")
    except OSError as err:
        return ProbeResult(ip=ip, ok=False, duration_ms=(time.perf_counter() - start) * 1000.0, error=str(err))
    finally:
        if sock is not None:
            sock.close()


def _scan(
    hosts: list[str],
    *,
    jobs: int,
    connect_timeout: float,
    probe_timeout: float,
    probe_fn: Callable[[str, int, float, float], ProbeResult],
    port: int,
    host_attempts: int,
) -> tuple[list[str], list[ProbeResult], float]:
    started = time.perf_counter()
    results: list[ProbeResult] = []
    def probe_with_retries(ip: str) -> ProbeResult:
        last = ProbeResult(ip=ip, ok=False, duration_ms=0.0, error="not attempted")
        started = time.perf_counter()
        for _ in range(max(1, host_attempts)):
            last = probe_fn(ip, port, connect_timeout, probe_timeout)
            if last.ok:
                return ProbeResult(
                    ip=ip,
                    ok=True,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    error=None,
                )
            time.sleep(0.03)
        return ProbeResult(
            ip=ip,
            ok=False,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            error=last.error,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [
            pool.submit(probe_with_retries, ip)
            for ip in hosts
        ]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())
    discovered = sorted(result.ip for result in results if result.ok)
    total_ms = (time.perf_counter() - started) * 1000.0
    return discovered, results, total_ms


def _hosts_from_args(subnet: str | None, ips: list[str]) -> list[str]:
    if ips:
        return sorted(set(ips))
    if subnet:
        return [f"{subnet}.{host}" for host in range(1, 255)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Klimatronik discovery tester")
    parser.add_argument("--subnet", help="Subnet prefix A.B.C (example: 192.168.31)")
    parser.add_argument("--ips", nargs="*", default=[], help="Explicit IPs to probe")
    parser.add_argument("--expected", nargs="*", default=[], help="IPs that must be found")
    parser.add_argument("--mode", choices=["legacy", "fast", "both"], default="both")
    parser.add_argument("--jobs", type=int, default=64)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--connect-timeout", type=float, default=0.2)
    parser.add_argument("--probe-timeout", type=float, default=0.8)
    parser.add_argument("--host-attempts", type=int, default=2, help="Retries per host before failing")
    parser.add_argument("--limit", type=int, default=0, help="Cap discovered list length (0 means no cap)")
    parser.add_argument("--repeats", type=int, default=1, help="Run each mode N times")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    hosts = _hosts_from_args(args.subnet, args.ips)
    if not hosts:
        raise SystemExit("Pass either --ips or --subnet")

    modes = [args.mode] if args.mode in {"legacy", "fast"} else ["legacy", "fast"]
    probe_map: dict[str, Callable[[str, int, float, float], ProbeResult]] = {
        "legacy": _probe_legacy,
        "fast": _probe_fast,
    }

    reports = []
    for mode in modes:
        runs = []
        for _ in range(max(1, args.repeats)):
            discovered, results, total_ms = _scan(
                hosts,
                jobs=args.jobs,
                connect_timeout=args.connect_timeout,
                probe_timeout=args.probe_timeout,
                probe_fn=probe_map[mode],
                port=args.port,
                host_attempts=args.host_attempts,
            )
            if args.limit > 0:
                discovered = discovered[: args.limit]
            errors = [r for r in results if not r.ok and r.error]
            runs.append(
                {
                    "discovered": discovered,
                    "discovered_count": len(discovered),
                    "elapsed_ms": round(total_ms, 2),
                    "avg_probe_ms": round(sum(r.duration_ms for r in results) / max(1, len(results)), 2),
                    "error_examples": [e.error for e in errors[:5]],
                }
            )
        reports.append({"mode": mode, "hosts_scanned": len(hosts), "runs": runs})

    required = set(args.expected)
    fast_report = next((r for r in reports if r["mode"] == "fast"), reports[0])
    run_sets = [set(run["discovered"]) for run in fast_report["runs"]]
    missing_per_run = [sorted(required - found_set) for found_set in run_sets]
    missing = missing_per_run[0] if missing_per_run else sorted(required)
    success = all(not miss for miss in missing_per_run)

    output = {
        "ok": success,
        "required": sorted(required),
        "missing_first_run": missing,
        "missing_by_run": missing_per_run,
        "reports": reports,
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        for report in reports:
            print(f"{report['mode']}: {len(report['runs'])} run(s), hosts={report['hosts_scanned']}")
            for idx, run in enumerate(report["runs"], start=1):
                print(
                    f"  run {idx}: discovered {run['discovered_count']} in {run['elapsed_ms']} ms"
                )
                print("    " + ", ".join(run["discovered"]) if run["discovered"] else "    (none)")
        if required:
            print(f"required: {', '.join(sorted(required))}")
            print(
                "missing by run: "
                + " | ".join(", ".join(miss) if miss else "(none)" for miss in missing_per_run)
            )

    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
