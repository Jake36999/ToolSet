#!/usr/bin/env python3
"""runtime_end_watcher.py — Phase 8.

Lightweight post-exit runtime evidence watcher.

Runs a target command via subprocess.Popen, streams stdout/stderr to log files
without ever holding the full output in memory, optionally samples process
metrics at a configurable interval, and writes a structured report bundle to
--out-dir after the process exits.

Output bundle (all written to --out-dir):
  stdout.log            Full stdout of the target process.
  stderr.log            Full stderr of the target process.
  runtime_metrics.json  Timing, exit code, and metrics summary.
  timeline.csv          Timestamped metric samples (one row per interval).
  stdout_tail.txt       Last 50 lines of stdout, redacted for sensitive content.
  stderr_tail.txt       Last 50 lines of stderr, redacted for sensitive content.
  runtime_summary.md    Human-readable Markdown summary.

Exit codes:
  Mirrors the target process returncode on normal completion.
  Returns 1 on timeout or start failure.

NOTE: --cmd must be the last option on the command line.
      All tokens after --cmd are passed directly to the target process.

Examples:
  python runtime_end_watcher.py --out-dir ./reports --cmd python -c "print(1)"
  python runtime_end_watcher.py --timeout 30 --name ci_run --out-dir ./out \\
      --cmd pytest tests/ -v
"""

import argparse
import csv
import datetime
import json
import os
import pathlib
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Optional heavy imports — degrade gracefully when unavailable
# ---------------------------------------------------------------------------

try:
    import psutil as _psutil  # type: ignore[import]
    _PSUTIL_AVAILABLE = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False

try:
    from aletheia_tool_core.reports import write_json_report, write_markdown_report
    from aletheia_tool_core.security import sanitize_content
    _CORE_AVAILABLE = True
except ImportError:
    _CORE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WATCHER_VERSION: str = "v8.0"
TAIL_LINES: int = 50
_CHUNK_SIZE: int = 8192   # characters read per drain-thread iteration
_ENCODING: str = "utf-8"

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current local time as an ISO 8601 string."""
    return datetime.datetime.now().isoformat()


def _drain_pipe(pipe: Any, log_path: pathlib.Path) -> None:
    """Thread target: drain a text pipe in fixed-size chunks to a log file.

    Reads up to _CHUNK_SIZE characters at a time so that arbitrarily large
    output is never held in memory.  The log file is flushed after each
    chunk so it is readable even if the target process is still running.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding=_ENCODING, errors="replace") as fh:
        while True:
            chunk: str = pipe.read(_CHUNK_SIZE)
            if not chunk:
                break
            fh.write(chunk)
            fh.flush()


def _sample_loop(
    proc: subprocess.Popen,
    interval_s: float,
    samples: List[Dict[str, Any]],
    stop_event: threading.Event,
    full_metrics: bool,
) -> None:
    """Thread target: periodically append a metric snapshot to *samples*.

    Degrades gracefully:
    - If psutil is unavailable, each snapshot records only a timestamp.
    - If the process has already exited between snapshots, the psutil error
      is recorded in the snapshot rather than raising.

    Args:
        proc:         The running subprocess.
        interval_s:   Seconds to wait between snapshots.
        samples:      Shared list to append dicts to (no lock needed — only
                      this thread appends; main thread reads after join).
        stop_event:   Set by the main thread when the process exits.
        full_metrics: True → attempt psutil CPU/memory/thread stats.
                      False → record timestamp only (no psutil attempt).
    """
    while not stop_event.wait(interval_s):
        snapshot: Dict[str, Any] = {"timestamp": _now_iso()}
        if full_metrics and _PSUTIL_AVAILABLE and _psutil is not None:
            try:
                ps = _psutil.Process(proc.pid)
                # cpu_percent(interval=None) returns utilisation since last call.
                snapshot["cpu_percent"] = ps.cpu_percent(interval=None)
                mem = ps.memory_info()
                snapshot["rss_bytes"] = mem.rss
                snapshot["vms_bytes"] = mem.vms
                try:
                    snapshot["num_threads"] = ps.num_threads()
                except Exception:
                    pass
            except Exception as exc:
                # Process may have exited between the stop check and here.
                snapshot["metrics_error"] = str(exc)
        else:
            snapshot["psutil_available"] = _PSUTIL_AVAILABLE
        samples.append(snapshot)


def _read_tail(log_path: pathlib.Path, n_lines: int = TAIL_LINES) -> List[str]:
    """Return the last *n_lines* lines of *log_path* using a rolling buffer.

    Never holds more than *n_lines* + 1 lines in memory regardless of file
    size.  Returns an empty list if the file is absent or empty.
    """
    if not log_path.exists() or log_path.stat().st_size == 0:
        return []
    ring: List[str] = []
    with log_path.open("r", encoding=_ENCODING, errors="replace") as fh:
        for line in fh:
            ring.append(line.rstrip("\n"))
            if len(ring) > n_lines:
                ring.pop(0)
    return ring


def _write_tail_file(out_path: pathlib.Path, lines: List[str]) -> None:
    """Write *lines* to *out_path* with sensitive-content redaction applied."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    if _CORE_AVAILABLE:
        content = sanitize_content(content)  # type: ignore[name-defined]
    with out_path.open("w", encoding=_ENCODING) as fh:
        fh.write(content)
        if content and not content.endswith("\n"):
            fh.write("\n")


# ---------------------------------------------------------------------------
# Artefact writers
# ---------------------------------------------------------------------------


def _write_metrics_json(
    out_path: pathlib.Path,
    *,
    run_name: str,
    cmd: List[str],
    exit_code: Optional[int],
    timed_out: bool,
    start_iso: str,
    end_iso: str,
    duration_s: float,
    metrics_mode: str,
    sample_count: int,
    start_failed: bool = False,
    start_error: str = "",
) -> None:
    data: Dict[str, Any] = {
        "watcher_version": WATCHER_VERSION,
        "run_name": run_name,
        "cmd": cmd,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "start_failed": start_failed,
        "start_error": start_error,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "duration_s": round(duration_s, 6),
        "metrics_mode": metrics_mode,
        "psutil_available": _PSUTIL_AVAILABLE,
        "sample_count": sample_count,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_json_report(data, out_path)  # type: ignore[name-defined]
    else:
        with out_path.open("w", encoding=_ENCODING) as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")


def _write_timeline_csv(
    out_path: pathlib.Path,
    samples: List[Dict[str, Any]],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "timestamp",
        "cpu_percent",
        "rss_bytes",
        "vms_bytes",
        "num_threads",
        "metrics_error",
        "psutil_available",
    ]
    with out_path.open("w", newline="", encoding=_ENCODING) as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for sample in samples:
            writer.writerow({f: sample.get(f, "") for f in fieldnames})


def _write_summary_md(
    out_path: pathlib.Path,
    *,
    run_name: str,
    cmd: List[str],
    exit_code: Optional[int],
    timed_out: bool,
    start_iso: str,
    end_iso: str,
    duration_s: float,
    sample_count: int,
    stdout_tail_lines: int,
    stderr_tail_lines: int,
    start_failed: bool = False,
    start_error: str = "",
) -> None:
    if timed_out:
        status = "TIMEOUT"
    elif start_failed:
        status = "START_FAILURE"
    elif exit_code == 0:
        status = "PASS"
    else:
        status = "FAIL"

    overview: Dict[str, str] = {
        "Run name":    run_name,
        "Status":      status,
        "Exit code":   str(exit_code) if exit_code is not None else "N/A",
        "Timed out":   str(timed_out),
        "Start":       start_iso,
        "End":         end_iso,
        "Duration (s)": f"{duration_s:.3f}",
    }
    if start_failed:
        overview["Start error"] = start_error

    sections: Dict[str, Any] = {
        "Overview": overview,
        "Command": " ".join(cmd) if cmd else "(none)",
        "Metrics": {
            "Samples collected": str(sample_count),
            "psutil available":  str(_PSUTIL_AVAILABLE),
        },
        "Output logs": {
            "stdout tail lines": str(stdout_tail_lines),
            "stderr tail lines": str(stderr_tail_lines),
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_markdown_report(  # type: ignore[name-defined]
            "Runtime End Watcher Report", sections, out_path,
        )
    else:
        lines = ["# Runtime End Watcher Report", ""]
        for sec_title, sec_body in sections.items():
            lines.append(f"## {sec_title}")
            lines.append("")
            if isinstance(sec_body, dict):
                for k, v in sec_body.items():
                    lines.append(f"- **{k}**: {v}")
            else:
                lines.append(str(sec_body))
            lines.append("")
        with out_path.open("w", encoding=_ENCODING) as fh:
            fh.write("\n".join(lines).rstrip() + "\n")


# ---------------------------------------------------------------------------
# Report bundle coordinator
# ---------------------------------------------------------------------------


def _produce_report_bundle(
    out_dir: pathlib.Path,
    *,
    run_name: str,
    cmd: List[str],
    exit_code: Optional[int],
    timed_out: bool,
    start_iso: str,
    end_iso: str,
    duration_s: float,
    samples: List[Dict[str, Any]],
    stdout_log: pathlib.Path,
    stderr_log: pathlib.Path,
    metrics_mode: str,
    start_failed: bool = False,
    start_error: str = "",
) -> None:
    """Write all seven artefacts to *out_dir*."""
    out_dir.mkdir(parents=True, exist_ok=True)

    stdout_tail = _read_tail(stdout_log)
    stderr_tail = _read_tail(stderr_log)

    _write_metrics_json(
        out_dir / "runtime_metrics.json",
        run_name=run_name,
        cmd=cmd,
        exit_code=exit_code,
        timed_out=timed_out,
        start_iso=start_iso,
        end_iso=end_iso,
        duration_s=duration_s,
        metrics_mode=metrics_mode,
        sample_count=len(samples),
        start_failed=start_failed,
        start_error=start_error,
    )
    _write_timeline_csv(out_dir / "timeline.csv", samples)
    _write_tail_file(out_dir / "stdout_tail.txt", stdout_tail)
    _write_tail_file(out_dir / "stderr_tail.txt", stderr_tail)
    _write_summary_md(
        out_dir / "runtime_summary.md",
        run_name=run_name,
        cmd=cmd,
        exit_code=exit_code,
        timed_out=timed_out,
        start_iso=start_iso,
        end_iso=end_iso,
        duration_s=duration_s,
        sample_count=len(samples),
        stdout_tail_lines=len(stdout_tail),
        stderr_tail_lines=len(stderr_tail),
        start_failed=start_failed,
        start_error=start_error,
    )


# ---------------------------------------------------------------------------
# Core execution engine
# ---------------------------------------------------------------------------


def _run_watched(args: argparse.Namespace) -> int:
    """Execute the target command, collect evidence, write the report bundle.

    Returns:
        The watcher exit code — mirrors the subprocess returncode on normal
        completion, or 1 on timeout / start failure.
    """
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    stdout_log = out_dir / "stdout.log"
    stderr_log = out_dir / "stderr.log"
    cmd: List[str] = list(args.cmd) if args.cmd else []

    # Build subprocess environment.
    env = os.environ.copy()
    if args.python_faultevidence:
        env["PYTHONFAULTHANDLER"] = "1"
    if args.python_tracemalloc:
        env["PYTHONTRACEMALLOC"] = "1"

    start_iso = _now_iso()
    start_time = datetime.datetime.now()
    samples: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Attempt to launch the target process                                #
    # ------------------------------------------------------------------ #
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=_ENCODING,
            errors="replace",
            env=env,
        )
    except (FileNotFoundError, PermissionError, OSError, ValueError) as exc:
        end_iso = _now_iso()
        duration_s = (datetime.datetime.now() - start_time).total_seconds()
        print(f"ERROR: Failed to start process: {exc}", file=sys.stderr)
        _produce_report_bundle(
            out_dir,
            run_name=args.name,
            cmd=cmd,
            exit_code=None,
            timed_out=False,
            start_iso=start_iso,
            end_iso=end_iso,
            duration_s=duration_s,
            samples=[],
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            metrics_mode=args.metrics_mode,
            start_failed=True,
            start_error=str(exc),
        )
        return 1

    # ------------------------------------------------------------------ #
    # Drain stdout / stderr to log files in background threads            #
    # ------------------------------------------------------------------ #
    drain_out = threading.Thread(
        target=_drain_pipe,
        args=(proc.stdout, stdout_log),
        daemon=True,
    )
    drain_err = threading.Thread(
        target=_drain_pipe,
        args=(proc.stderr, stderr_log),
        daemon=True,
    )
    drain_out.start()
    drain_err.start()

    # ------------------------------------------------------------------ #
    # Optional metrics sampling thread                                    #
    # ------------------------------------------------------------------ #
    stop_sampling = threading.Event()
    sample_thread: Optional[threading.Thread] = None
    if args.metrics_mode != "none" and args.sample_seconds > 0:
        sample_thread = threading.Thread(
            target=_sample_loop,
            args=(
                proc,
                args.sample_seconds,
                samples,
                stop_sampling,
                args.metrics_mode == "full",
            ),
            daemon=True,
        )
        sample_thread.start()

    # ------------------------------------------------------------------ #
    # Wait for the process (with optional hard timeout)                   #
    # ------------------------------------------------------------------ #
    timed_out = False
    try:
        if args.timeout is not None and args.timeout > 0:
            proc.wait(timeout=args.timeout)
        else:
            proc.wait()
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()  # reap the terminated process

    # ------------------------------------------------------------------ #
    # Shut down helper threads                                            #
    # ------------------------------------------------------------------ #
    stop_sampling.set()
    if sample_thread is not None:
        sample_thread.join(timeout=2.0)

    drain_out.join(timeout=10.0)
    drain_err.join(timeout=10.0)

    end_iso = _now_iso()
    duration_s = (datetime.datetime.now() - start_time).total_seconds()
    exit_code: int = proc.returncode  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    # Write report bundle                                                 #
    # ------------------------------------------------------------------ #
    _produce_report_bundle(
        out_dir,
        run_name=args.name,
        cmd=cmd,
        exit_code=exit_code,
        timed_out=timed_out,
        start_iso=start_iso,
        end_iso=end_iso,
        duration_s=duration_s,
        samples=samples,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        metrics_mode=args.metrics_mode,
    )

    return 1 if timed_out else exit_code


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="runtime_end_watcher",
        description=(
            "Lightweight post-exit runtime evidence watcher (Phase 8).\n"
            "Streams target-process output to log files and writes a\n"
            "structured report bundle after the process exits."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "NOTE: --cmd must be the last option on the command line.\n"
            "All tokens after --cmd are passed as-is to the target process.\n"
        ),
    )
    parser.add_argument(
        "--name",
        default="run",
        metavar="NAME",
        help="Human-readable name for this run. Used in report titles. Default: 'run'.",
    )
    parser.add_argument(
        "--cmd",
        nargs=argparse.REMAINDER,
        required=True,
        metavar="ARG",
        help=(
            "Command to execute. MUST be the last option. "
            "All following tokens form the target command."
        ),
    )
    parser.add_argument(
        "--sample-seconds",
        type=float,
        default=5.0,
        dest="sample_seconds",
        metavar="N",
        help="Process metric sampling interval in seconds. Default: 5.0.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Kill the process after this many seconds. Default: no timeout.",
    )
    parser.add_argument(
        "--out-dir",
        default="runtime_watcher_out",
        dest="out_dir",
        metavar="DIR",
        help="Directory to write all output files. Default: 'runtime_watcher_out'.",
    )
    parser.add_argument(
        "--metrics-mode",
        choices=["none", "basic", "full"],
        default="basic",
        dest="metrics_mode",
        help=(
            "Metrics collection mode: "
            "none=disable sampling; "
            "basic=timestamp-only samples (default); "
            "full=timestamps + psutil CPU/memory/threads if available."
        ),
    )
    parser.add_argument(
        "--python-faultevidence",
        action="store_true",
        dest="python_faultevidence",
        help="Set PYTHONFAULTHANDLER=1 in the target process environment.",
    )
    parser.add_argument(
        "--python-tracemalloc",
        action="store_true",
        dest="python_tracemalloc",
        help="Set PYTHONTRACEMALLOC=1 in the target process environment.",
    )

    args = parser.parse_args()

    if not args.cmd:
        parser.error("--cmd requires at least one argument (the command to run).")

    sys.exit(_run_watched(args))


if __name__ == "__main__":
    main()
