#!/usr/bin/env python3
"""Download full-quality public SRA objects from NCBI AWS and create FASTQ."""

from __future__ import annotations

import concurrent.futures
import contextlib
from collections import deque
import dataclasses
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


PLUGIN_ID = "sra_data_download_high_speed_auto"
ACCESSION_RE = re.compile(r"^[SED]RR\d+$", re.IGNORECASE)
S3_ROOT = "https://sra-pub-run-odp.s3.amazonaws.com/sra"
PROGRESS_INTERVAL_SECONDS = 30
DOWNLOAD_PROGRESS_INTERVAL_SECONDS = 10
SEGMENT_SIZE_BYTES = 64 * 1024 * 1024
READ_BLOCK_BYTES = 4 * 1024 * 1024
STOP_EVENT = threading.Event()
ACTIVE_PROCESSES: set[subprocess.Popen] = set()
ACTIVE_LOCK = threading.Lock()


class WorkflowError(RuntimeError):
    pass


class Stopped(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class SraSpec:
    accession: str
    url: str
    size: int
    etag: str


@dataclasses.dataclass(frozen=True)
class Segment:
    index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1


def log(message: str) -> None:
    print(f"[SRA HIGH SPEED] {message}", flush=True)


def format_size(size: int) -> str:
    value = float(max(size, 0))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def parse_int(name: str, value: str | None, default: int, minimum: int, maximum: int) -> int:
    raw = str(default) if value is None or not value.strip() else value.strip()
    try:
        result = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def unique_accessions(text: str) -> list[str]:
    values = [item.upper() for item in re.split(r"[\s,;]+", text.strip()) if item]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not ACCESSION_RE.fullmatch(value):
            raise ValueError(f"Invalid run accession: {value}")
        if value not in seen:
            result.append(value)
            seen.add(value)
    if not result:
        raise ValueError("No SRA run accessions were provided")
    return result


def load_accessions(env: dict[str, str]) -> list[str]:
    mode = env.get("input_type", "single").strip() or "single"
    if mode == "single":
        text = env.get("accession", "")
    elif mode == "multi_text":
        text = env.get("accession_text", "")
    elif mode == "list_file":
        filename = env.get("accession_file", "").strip()
        if not filename:
            raise ValueError("Accession list file is required")
        path = Path(filename)
        if not path.is_file():
            raise ValueError(f"Accession list file does not exist: {path}")
        text = path.read_text(encoding="utf-8-sig")
    else:
        raise ValueError(f"Unsupported input type: {mode}")
    return unique_accessions(text)


def direct_opener():
    return build_opener(ProxyHandler({}))


def fetch_sra_spec(accession: str, attempts: int = 5) -> SraSpec:
    url = f"{S3_ROOT}/{accession}/{accession}"
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if STOP_EVENT.is_set():
            raise Stopped("Stopped by user")
        try:
            request = Request(url, method="HEAD", headers={"User-Agent": "PrimiGenius-S3-FASTQ/1.0"})
            with direct_opener().open(request, timeout=45) as response:
                size = int(response.headers.get("Content-Length", "0"))
                etag = response.headers.get("ETag", "").strip('"')
                if size <= 0:
                    raise WorkflowError("S3 did not provide a valid object size")
                if response.headers.get("Accept-Ranges", "").lower() != "bytes":
                    raise WorkflowError("S3 endpoint does not advertise byte-range support")
                return SraSpec(accession, response.geturl(), size, etag)
        except (OSError, ValueError, WorkflowError) as exc:
            last_error = exc
            if attempt < attempts:
                delay = min(3 * (2 ** (attempt - 1)), 30)
                log(f"{accession}: S3 metadata attempt {attempt}/{attempts} failed; retrying in {delay}s")
                time.sleep(delay)
    raise WorkflowError(f"Unable to read NCBI S3 metadata for {accession}: {last_error}")


def make_segments(total_size: int, segment_size: Optional[int] = None) -> list[Segment]:
    if segment_size is None:
        segment_size = SEGMENT_SIZE_BYTES
    if total_size <= 0 or segment_size <= 0:
        raise ValueError("total_size and segment_size must be positive")
    result: list[Segment] = []
    for index, start in enumerate(range(0, total_size, segment_size)):
        result.append(Segment(index, start, min(start + segment_size, total_size) - 1))
    return result


def checkpoint_metadata_path(cache_root: Path, accession: str) -> Path:
    return cache_root / f".{accession}.download.json"


def segment_path(cache_root: Path, accession: str, segment: Segment) -> Path:
    return cache_root / f".{accession}.part-{segment.index:05d}-{segment.start}-{segment.end}"


def clear_checkpoints(cache_root: Path, accession: str) -> None:
    checkpoint_metadata_path(cache_root, accession).unlink(missing_ok=True)
    for path in cache_root.glob(f".{accession}.part-*"):
        if path.is_file() and not path.is_symlink():
            path.unlink()


def completed_segment_bytes(
    cache_root: Path,
    accession: str,
    segments: Iterable[Segment],
) -> int:
    total = 0
    for segment in segments:
        path = segment_path(cache_root, accession, segment)
        with contextlib.suppress(OSError):
            total += min(path.stat().st_size, segment.size)
    return total


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def prepare_parts(spec: SraSpec, cache_root: Path) -> list[Segment]:
    segments = make_segments(spec.size)
    metadata_path = checkpoint_metadata_path(cache_root, spec.accession)
    expected = {
        "schema_version": 1,
        "accession": spec.accession,
        "url": spec.url,
        "size": spec.size,
        "etag": spec.etag,
        "segment_size": SEGMENT_SIZE_BYTES,
    }
    cache_root.mkdir(parents=True, exist_ok=True)
    if metadata_path.exists() or any(cache_root.glob(f".{spec.accession}.part-*")):
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if existing != expected:
            clear_checkpoints(cache_root, spec.accession)
            log(f"{spec.accession}: stale S3 checkpoints removed because source metadata changed")
    write_json_atomic(metadata_path, expected)
    for segment in segments:
        path = segment_path(cache_root, spec.accession, segment)
        if path.exists() and path.stat().st_size > segment.size:
            path.unlink()
    return segments


def download_one_segment(
    spec: SraSpec,
    segment: Segment,
    cache_root: Path,
    retries: int,
    cancel_event: threading.Event,
) -> None:
    path = segment_path(cache_root, spec.accession, segment)
    for attempt in range(1, retries + 1):
        if STOP_EVENT.is_set():
            raise Stopped("Stopped by user")
        if cancel_event.is_set():
            raise WorkflowError("parallel download cancelled after another segment failed")
        current = path.stat().st_size if path.exists() else 0
        if current == segment.size:
            return
        if current > segment.size:
            path.unlink()
            current = 0
        range_start = segment.start + current
        headers = {
            "Range": f"bytes={range_start}-{segment.end}",
            "User-Agent": "PrimiGenius-S3-FASTQ/1.0",
            "Accept-Encoding": "identity",
        }
        if spec.etag:
            headers["If-Match"] = f'"{spec.etag}"'
        try:
            request = Request(spec.url, headers=headers)
            with direct_opener().open(request, timeout=30) as response:
                if response.status != 206:
                    raise WorkflowError(f"Range request returned HTTP {response.status}")
                content_range = response.headers.get("Content-Range", "")
                if not content_range.startswith(f"bytes {range_start}-"):
                    raise WorkflowError(f"unexpected Content-Range: {content_range!r}")
                with path.open("ab") as handle:
                    while True:
                        if STOP_EVENT.is_set():
                            raise Stopped("Stopped by user")
                        if cancel_event.is_set():
                            raise WorkflowError("parallel download cancelled")
                        block = response.read(READ_BLOCK_BYTES)
                        if not block:
                            break
                        handle.write(block)
            actual = path.stat().st_size
            if actual == segment.size:
                return
            raise WorkflowError(f"short segment: {actual}/{segment.size} bytes")
        except Stopped:
            raise
        except (HTTPError, URLError, OSError, WorkflowError) as exc:
            if attempt >= retries:
                raise WorkflowError(
                    f"segment {segment.index + 1} failed after {retries} attempts: {exc}"
                ) from exc
            delay = min(2 ** (attempt - 1), 30)
            log(
                f"{spec.accession}: segment {segment.index + 1} interrupted; "
                f"resuming in {delay}s ({attempt}/{retries})"
            )
            time.sleep(delay)


def assemble_segments(
    spec: SraSpec,
    destination: Path,
    cache_root: Path,
    segments: list[Segment],
) -> None:
    temporary = destination.with_suffix(destination.suffix + ".assembling")
    with contextlib.suppress(OSError):
        temporary.unlink()
    written = 0
    next_report = 10
    log(f"{spec.accession}: assembling verified S3 ranges into one SRA file")
    with temporary.open("wb") as output:
        for segment in segments:
            path = segment_path(cache_root, spec.accession, segment)
            if not path.is_file() or path.stat().st_size != segment.size:
                raise WorkflowError(f"segment {segment.index + 1} is incomplete during assembly")
            with path.open("rb") as source:
                for block in iter(lambda: source.read(READ_BLOCK_BYTES), b""):
                    if STOP_EVENT.is_set():
                        raise Stopped("Stopped by user")
                    output.write(block)
                    written += len(block)
                    percent = int(written * 100 / spec.size)
                    if percent >= next_report:
                        log(f"{spec.accession}: SRA assembly {percent}%")
                        next_report += 10
    if temporary.stat().st_size != spec.size:
        raise WorkflowError("assembled SRA size mismatch")
    os.replace(temporary, destination)


def useful_diagnostics(lines: list[str]) -> list[str]:
    selected = [
        line
        for line in lines
        if any(word in line.lower() for word in ("error", "failed", "timeout", "denied", "not found"))
    ]
    return (selected or lines)[-6:]


def run_process(
    command: list[str],
    label: str,
    progress: Callable[[], None] | None = None,
) -> int:
    if STOP_EVENT.is_set():
        raise Stopped("Stopped by user")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    captured: deque[str] = deque(maxlen=160)

    def capture_output() -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                captured.append(line)

    reader = threading.Thread(target=capture_output, name=f"{label}-output", daemon=True)
    reader.start()
    with ACTIVE_LOCK:
        ACTIVE_PROCESSES.add(process)
    next_progress = time.monotonic() + PROGRESS_INTERVAL_SECONDS
    try:
        while True:
            try:
                code = process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if STOP_EVENT.is_set():
                    with contextlib.suppress(ProcessLookupError):
                        process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        with contextlib.suppress(ProcessLookupError):
                            process.kill()
                        process.wait()
                    raise Stopped("Stopped by user")
                now = time.monotonic()
                if progress is not None and now >= next_progress:
                    with contextlib.suppress(Exception):
                        progress()
                    next_progress = now + PROGRESS_INTERVAL_SECONDS
        reader.join(timeout=2)
        if code != 0:
            for line in useful_diagnostics(list(captured)):
                log(f"{label} detail: {line}")
        return code
    finally:
        with ACTIVE_LOCK:
            ACTIVE_PROCESSES.discard(process)
        with contextlib.suppress(Exception):
            if process.stdout is not None:
                process.stdout.close()


def validate_sra(path: Path) -> bool:
    log(f"{path.stem}: validating downloaded full-quality SRA")
    return run_process(["vdb-validate", str(path)], f"{path.stem} vdb-validate") == 0


def download_sra(
    spec: SraSpec,
    cache_root: Path,
    connections: int,
    retries: int,
) -> Path:
    cache_root.mkdir(parents=True, exist_ok=True)
    destination = cache_root / f"{spec.accession}.sra"
    if destination.is_file() and destination.stat().st_size == spec.size:
        if validate_sra(destination):
            log(f"{spec.accession}: verified existing SRA; download skipped")
            return destination
        destination.unlink()
        log(f"{spec.accession}: invalid cached SRA removed; starting a clean download")
    elif destination.exists():
        destination.unlink()

    segments = prepare_parts(spec, cache_root)
    resumed = completed_segment_bytes(cache_root, spec.accession, segments)
    log(
        f"{spec.accession}: downloading full-quality SRA from NCBI AWS "
        f"({connections} parallel ranges, {len(segments)} fixed checkpoints, resume enabled)"
    )
    if resumed:
        log(
            f"{spec.accession}: resumed at {resumed * 100 / spec.size:.1f}% "
            f"({format_size(resumed)}/{format_size(spec.size)})"
        )
    cancel_event = threading.Event()
    started = time.monotonic()
    last_time = started
    last_bytes = resumed
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(connections, len(segments)))
    futures = [
        executor.submit(download_one_segment, spec, segment, cache_root, retries, cancel_event)
        for segment in segments
    ]
    try:
        while True:
            done, _ = concurrent.futures.wait(
                futures,
                timeout=1,
                return_when=concurrent.futures.FIRST_EXCEPTION,
            )
            failed = next((future.exception() for future in done if future.exception() is not None), None)
            if failed is not None:
                cancel_event.set()
                raise failed
            if len(done) == len(futures):
                break
            now = time.monotonic()
            if now - last_time >= DOWNLOAD_PROGRESS_INTERVAL_SECONDS:
                completed = completed_segment_bytes(cache_root, spec.accession, segments)
                interval = max(now - last_time, 0.001)
                current_speed = max(completed - last_bytes, 0) / interval
                average_speed = max(completed - resumed, 0) / max(now - started, 0.001)
                eta_seconds = (spec.size - completed) / current_speed if current_speed > 0 else 0
                eta_text = f", ETA {eta_seconds / 60:.1f} min" if eta_seconds > 0 else ""
                log(
                    f"{spec.accession}: S3 download {completed * 100 / spec.size:.1f}% "
                    f"({format_size(completed)}/{format_size(spec.size)}), "
                    f"now {current_speed * 8 / 1_000_000:.1f} Mbit/s, "
                    f"average {average_speed * 8 / 1_000_000:.1f} Mbit/s{eta_text}"
                )
                last_time = now
                last_bytes = completed
    finally:
        cancel_event.set()
        executor.shutdown(wait=True, cancel_futures=True)

    completed = completed_segment_bytes(cache_root, spec.accession, segments)
    if completed != spec.size:
        raise WorkflowError(f"S3 ranges incomplete: {completed}/{spec.size} bytes")
    log(f"{spec.accession}: all S3 ranges downloaded; assembling local SRA")
    assemble_segments(spec, destination, cache_root, segments)
    if not validate_sra(destination):
        destination.unlink(missing_ok=True)
        clear_checkpoints(cache_root, spec.accession)
        raise WorkflowError("assembled SRA failed NCBI internal validation; clean checkpoints removed")
    clear_checkpoints(cache_root, spec.accession)
    log(f"{spec.accession}: SRA size and NCBI internal checks passed")
    return destination


def work_size(directory: Path) -> int:
    total = 0
    with contextlib.suppress(OSError):
        for path in directory.glob("*.fastq"):
            total += path.stat().st_size
    return total


def convert_to_fastq(
    accession: str,
    sra_path: Path,
    work_dir: Path,
    threads: int,
) -> tuple[str, list[Path]]:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    temp_dir = work_dir / ".fasterq_tmp"
    temp_dir.mkdir()
    started = time.monotonic()
    log(f"{accession}: converting validated SRA to FASTQ with {threads} threads")

    def progress() -> None:
        elapsed = max(time.monotonic() - started, 1)
        log(f"{accession}: fasterq-dump running {elapsed / 60:.1f} min, wrote {format_size(work_size(work_dir))}")

    code = run_process(
        [
            "fasterq-dump",
            str(sra_path),
            "--split-files",
            "--skip-technical",
            "--threads",
            str(threads),
            "--temp",
            str(temp_dir),
            "--outdir",
            str(work_dir),
            "--force",
        ],
        f"{accession} fasterq-dump",
        progress=progress,
    )
    if code != 0:
        raise WorkflowError(f"fasterq-dump exited with code {code}")
    with contextlib.suppress(OSError):
        shutil.rmtree(temp_dir)
    outputs = sorted(work_dir.glob("*.fastq"))
    if not outputs:
        raise WorkflowError("fasterq-dump produced no FASTQ files")
    if any(not path.is_file() or path.stat().st_size <= 0 for path in outputs):
        raise WorkflowError("fasterq-dump produced an empty or invalid FASTQ")
    layout = detect_fastq_layout(accession, outputs)
    outputs = normalize_fastq_names(accession, layout, outputs)
    log(f"{accession}: automatically detected {layout}; fasterq-dump produced {len(outputs)} FASTQ file(s)")
    return layout, outputs


def detect_fastq_layout(accession: str, paths: Iterable[Path]) -> str:
    names = sorted({path.name for path in paths})
    pattern = re.compile(rf"^{re.escape(accession)}(?:_(\d+))?\.fastq$")
    matches = [pattern.fullmatch(name) for name in names]
    if len(names) == 1 and matches[0] is not None:
        return "SINGLE"
    if (
        len(names) == 2
        and all(match is not None and match.group(1) is not None for match in matches)
        and len({int(match.group(1)) for match in matches if match is not None}) == 2
    ):
        return "PAIRED"
    raise WorkflowError(
        "automatic layout detection rejected unexpected FASTQ set: "
        f"{names}; expected one biological-read file or two indexed biological-read files"
    )


def normalize_fastq_names(accession: str, layout: str, paths: list[Path]) -> list[Path]:
    if layout == "SINGLE":
        ordered = list(paths)
        targets = [ordered[0].with_name(f"{accession}.fastq")]
    elif layout == "PAIRED":
        ordered = sorted(
            paths,
            key=lambda path: int(re.search(r"_(\d+)\.fastq$", path.name).group(1)),
        )
        targets = [
            ordered[0].with_name(f"{accession}_1.fastq"),
            ordered[1].with_name(f"{accession}_2.fastq"),
        ]
    else:
        raise WorkflowError(f"unsupported FASTQ layout: {layout}")
    if all(source == target for source, target in zip(ordered, targets)):
        return ordered
    temporary: list[Path] = []
    for index, source in enumerate(ordered, start=1):
        temp = source.with_name(f".{accession}.normalize-{index}.fastq")
        os.replace(source, temp)
        temporary.append(temp)
    for temp, target in zip(temporary, targets):
        os.replace(temp, target)
    log(f"{accession}: normalized fasterq-dump read numbering to {[path.name for path in targets]}")
    return targets


def compress_fastqs(accession: str, paths: list[Path], threads: int) -> list[Path]:
    workers = min(2, len(paths))
    per_file_threads = max(1, threads // workers)
    log(
        f"{accession}: compressing {len(paths)} FASTQ file(s) with pigz "
        f"({workers} jobs, {per_file_threads} threads each)"
    )

    def compress(path: Path) -> Path:
        result = path.with_name(path.name + ".gz")
        started = time.monotonic()

        def progress() -> None:
            elapsed = max(time.monotonic() - started, 1)
            written = result.stat().st_size if result.exists() else 0
            log(
                f"{accession}: pigz compressing {path.name}, "
                f"elapsed {elapsed / 60:.1f} min, compressed output {format_size(written)}"
            )

        code = run_process(
            ["pigz", "-6", "-f", "-p", str(per_file_threads), str(path)],
            f"{accession} pigz {path.name}",
            progress=progress,
        )
        if code != 0:
            raise WorkflowError(f"pigz failed for {path.name} with code {code}")
        if not result.is_file() or result.stat().st_size <= 0:
            raise WorkflowError(f"pigz did not create {result.name}")
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        compressed = list(executor.map(compress, paths))
    return sorted(compressed)


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def gzip_test(path: Path, progress: Optional[Callable[[int, int], None]] = None) -> None:
    total = path.stat().st_size
    next_report = time.monotonic() + PROGRESS_INTERVAL_SECONDS
    with path.open("rb") as raw_handle:
        with gzip.GzipFile(fileobj=raw_handle, mode="rb") as gzip_handle:
            for _ in iter(lambda: gzip_handle.read(8 * 1024 * 1024), b""):
                now = time.monotonic()
                if progress is not None and now >= next_report:
                    progress(min(raw_handle.tell(), total), total)
                    next_report = now + PROGRESS_INTERVAL_SECONDS


def verify_fastqs(accession: str, paths: Iterable[Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in paths:
        log(f"{accession}: checking gzip stream for {path.name}")
        try:
            gzip_test(
                path,
                progress=lambda done, total: log(
                    f"{accession}: gzip check {path.name} {done * 100 / total:.1f}%"
                ),
            )
        except (OSError, EOFError) as exc:
            raise WorkflowError(f"gzip integrity check failed for {path.name}: {exc}") from exc
        log(f"{accession}: calculating MD5 for {path.name}")
        records.append({"name": path.name, "size": path.stat().st_size, "md5": md5_file(path)})
        log(f"{accession}: {path.name} passed gzip and MD5 checks")
    return records


def manifest_payload(
    spec: SraSpec,
    layout: str,
    records: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "plugin_id": PLUGIN_ID,
        "accession": spec.accession,
        "layout": layout,
        "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "NCBI AWS Open Data full-quality SRA",
        "source_url": spec.url,
        "source_size": spec.size,
        "source_etag": spec.etag,
        "files": records,
    }


def verify_completed(
    final_root: Path,
    manifest_path: Path,
    accession: str,
) -> tuple[str, list[Path]]:
    if final_root.is_symlink() or not final_root.is_dir() or not manifest_path.is_file():
        raise WorkflowError("completed FASTQ directory or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("plugin_id") != PLUGIN_ID or manifest.get("accession") != accession:
        raise WorkflowError("manifest identity mismatch")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise WorkflowError("manifest has no FASTQ records")
    layout = str(manifest.get("layout", "")).upper()
    if layout not in {"SINGLE", "PAIRED"}:
        raise WorkflowError("manifest has no valid SINGLE/PAIRED layout")
    paths: list[Path] = []
    for record in records:
        name = str(record.get("name", ""))
        if Path(name).name != name or not name.endswith(".fastq.gz"):
            raise WorkflowError("manifest contains an invalid FASTQ filename")
        path = final_root / name
        if not path.is_file() or path.stat().st_size != int(record.get("size", -1)):
            raise WorkflowError(f"completed file size mismatch: {path.name}")
        log(f"{accession}: rechecking completed file {path.name}")
        if md5_file(path) != record.get("md5"):
            raise WorkflowError(f"completed file MD5 mismatch: {path.name}")
        gzip_test(
            path,
            progress=lambda done, total: log(
                f"{accession}: completed gzip check {path.name} {done * 100 / total:.1f}%"
            ),
        )
        paths.append(path)
    expected = (
        {f"{accession}.fastq.gz"}
        if layout == "SINGLE"
        else {f"{accession}_1.fastq.gz", f"{accession}_2.fastq.gz"}
    )
    if {path.name for path in paths} != expected:
        raise WorkflowError(f"completed output does not match manifest layout {layout}")
    return layout, sorted(paths)


def accession_fastq_candidates(final_root: Path, accession: str) -> list[Path]:
    candidates = set(final_root.glob(f"{accession}_*.fastq.gz"))
    single = final_root / f"{accession}.fastq.gz"
    if single.exists():
        candidates.add(single)
    return sorted(path for path in candidates if path.is_file() and not path.is_symlink())


def quarantine_files(
    paths: Iterable[Path],
    rejected_root: Path,
    accession: str,
    reason: str,
) -> None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = rejected_root / f"{accession}.{stamp}.{reason}"
    suffix = 1
    while destination.exists():
        destination = rejected_root / f"{accession}.{stamp}.{reason}.{suffix}"
        suffix += 1
    destination.mkdir(parents=True, exist_ok=False)
    for path in existing:
        os.replace(path, destination / path.name)
    log(f"Preserved previous invalid output in {destination}")


def process_accession(
    accession: str,
    output_root: Path,
    connections: int,
    threads: int,
    retries: int,
    keep_sra_cache: bool,
) -> tuple[str, str, list[str]]:
    final_root = output_root / "s3_full_quality_fastq"
    manifest_root = output_root / ".s3_fastq_manifests"
    cache_root = output_root / ".s3_sra_cache"
    work_dir = output_root / ".s3_fastq_work" / accession
    rejected_root = output_root / ".s3_fastq_rejected"
    manifest_path = manifest_root / f"{accession}.json"
    final_root.mkdir(parents=True, exist_ok=True)
    manifest_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    if manifest_path.exists():
        try:
            layout, paths = verify_completed(final_root, manifest_path, accession)
            log(f"{accession}: completed {layout} output verified; skipped")
            return "SKIPPED", layout, [str(path) for path in paths]
        except (OSError, ValueError, json.JSONDecodeError, WorkflowError) as exc:
            log(f"{accession}: previous completed output is invalid ({exc})")
            quarantine_files(
                [*accession_fastq_candidates(final_root, accession), manifest_path],
                rejected_root,
                accession,
                "completed-invalid",
            )
    else:
        incomplete = accession_fastq_candidates(final_root, accession)
        if incomplete:
            log(f"{accession}: found FASTQ without a completion manifest")
            quarantine_files(incomplete, rejected_root, accession, "publish-incomplete")

    spec = fetch_sra_spec(accession)
    free_bytes = shutil.disk_usage(output_root).free
    required_bytes = spec.size * 8
    if free_bytes < required_bytes:
        raise WorkflowError(
            f"insufficient free space: need about {format_size(required_bytes)}, "
            f"available {format_size(free_bytes)}"
        )
    log(
        f"{accession}: full-quality SRA size {format_size(spec.size)}; "
        f"temporary-space check passed"
    )
    sra_path = download_sra(spec, cache_root, connections, retries)
    try:
        layout, fastqs = convert_to_fastq(accession, sra_path, work_dir, threads)
        compressed = compress_fastqs(accession, fastqs, threads)
        records = verify_fastqs(accession, compressed)
        paths = [final_root / str(record["name"]) for record in records]
        for source, destination in zip(compressed, paths):
            os.replace(source, destination)
        write_json_atomic(manifest_path, manifest_payload(spec, layout, records))
        log(
            f"{accession}: published verified {layout} FASTQ output "
            f"({len(paths)} file(s)) directly to {final_root}"
        )
    finally:
        if work_dir.exists():
            with contextlib.suppress(OSError):
                shutil.rmtree(work_dir)
    if not keep_sra_cache:
        with contextlib.suppress(OSError):
            sra_path.unlink()
        log(f"{accession}: validated SRA cache removed after successful publication")
    return "SUCCESS", layout, [str(path) for path in paths]


def write_summary(path: Path, rows: list[tuple[str, str, str, str, str]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("accession\tlayout\tstatus\tfastq_files\tmessage\n")
        for accession, layout, status, files, message in rows:
            safe_message = message.replace("\t", " ").replace("\n", " ")
            handle.write(f"{accession}\t{layout}\t{status}\t{files}\t{safe_message}\n")
    os.replace(temporary, path)


def write_run_status(
    path: Path,
    state: str,
    total: int,
    completed: int,
    failures: int,
    message: str,
) -> None:
    write_json_atomic(
        path,
        {
            "schema_version": 1,
            "plugin_id": PLUGIN_ID,
            "state": state,
            "total_accessions": total,
            "completed_accessions": completed,
            "failed_accessions": failures,
            "message": message,
            "updated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
    )


def handle_signal(signum, _frame) -> None:
    STOP_EVENT.set()
    log(f"received signal {signum}; stopping active process(es), resumable download data is retained")
    with ACTIVE_LOCK:
        processes = list(ACTIVE_PROCESSES)
    for process in processes:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()


def main() -> int:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)

    env = dict(os.environ)
    accessions = load_accessions(env)
    output_root = Path(env.get("output_dir", "/workspace")).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    connections = parse_int("download_connections", env.get("download_connections"), 8, 1, 16)
    threads = parse_int("threads", env.get("threads"), 12, 2, 32)
    retries = parse_int("retry_count", env.get("retry_count"), 8, 1, 12)
    keep_sra_cache = parse_bool(env.get("keep_sra_cache"), True)

    for executable in ("vdb-validate", "fasterq-dump", "pigz"):
        if shutil.which(executable) is None:
            raise WorkflowError(f"runtime is missing {executable}; rebuild the plugin image")

    lock_file = output_root / ".s3_fastq.lock"
    summary_file = output_root / "s3_fastq_download_summary.tsv"
    status_file = output_root / ".s3_fastq_status.json"
    rows: list[tuple[str, str, str, str, str]] = []
    failures = 0
    write_run_status(status_file, "RUNNING", len(accessions), 0, 0, "workflow started")
    with lock_file.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkflowError("Another NCBI S3 FASTQ task is using this output directory") from exc

        log(
            f"starting {len(accessions)} accession(s) in listed order, S3 connections={connections}, "
            f"conversion threads={threads}; proxy variables are disabled"
        )
        log("source is NCBI AWS Open Data full-quality SRA, not SRA Lite")
        log("FASTQ layout is detected independently for every accession: SINGLE or PAIRED")
        for accession in accessions:
            try:
                status, layout, files = process_accession(
                    accession,
                    output_root,
                    connections,
                    threads,
                    retries,
                    keep_sra_cache,
                )
                rows.append((accession, layout, status, ";".join(files), "verified"))
            except Stopped:
                rows.append((accession, "", "INTERRUPTED", "", "stopped by user; resumable S3 data retained"))
                write_summary(summary_file, rows)
                write_run_status(
                    status_file,
                    "INTERRUPTED",
                    len(accessions),
                    len(rows) - 1,
                    failures,
                    "stopped by user; resumable download data retained",
                )
                log("WORKFLOW_COMPLETE status=INTERRUPTED exit_code=143")
                return 143
            except Exception as exc:
                failures += 1
                log(f"ERROR {accession}: {exc}")
                rows.append((accession, "", "FAILED", "", str(exc)))
            write_summary(summary_file, rows)
            write_run_status(
                status_file,
                "RUNNING",
                len(accessions),
                len(rows),
                failures,
                f"processed {len(rows)}/{len(accessions)} accession(s)",
            )

    if failures:
        log(f"completed with {failures}/{len(accessions)} accession(s) failed")
        write_run_status(
            status_file,
            "FAILED",
            len(accessions),
            len(rows),
            failures,
            f"{failures}/{len(accessions)} accession(s) failed",
        )
        log("WORKFLOW_COMPLETE status=FAILED exit_code=1")
        return 1
    log(f"all {len(accessions)} accession(s) completed; summary: {summary_file}")
    write_run_status(
        status_file,
        "SUCCESS",
        len(accessions),
        len(rows),
        0,
        "all accessions completed and verified",
    )
    log("WORKFLOW_COMPLETE status=SUCCESS exit_code=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Stopped:
        log("WORKFLOW_COMPLETE status=INTERRUPTED exit_code=143")
        raise SystemExit(143)
    except Exception as exc:
        log(f"ERROR: {exc}")
        log("WORKFLOW_COMPLETE status=FAILED exit_code=1")
        raise SystemExit(1)
