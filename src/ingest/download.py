"""
Download the selected IPEDS complete data files.

Reads config/ipeds_manifest.csv, filters it against the selection patterns in
config/config.yaml, and downloads each matching table as a zip into
data/raw/<survey>/.

Design decisions worth defending:

  Idempotent. A file already on disk is skipped, so the download can be
  interrupted and resumed without starting over. Roughly 300 files against a
  public federal server should not be re-fetched because a laptop slept.

  Every attempt is recorded. data/logs/download_log.csv gets one row per
  table with the outcome, HTTP status, byte count and elapsed time, whether
  it succeeded, was skipped, or failed. "Which files do we actually have and
  why is one missing" must be answerable from a file, not from memory.

  Missing files are expected, not fatal. Some tables genuinely do not exist
  for some years. A 404 is logged as MISSING and the run continues.

  Polite. One second between requests by default, and a real User-Agent, so
  the run is identifiable in NCES logs rather than looking like a scraper.

Run:
    python -m src.ingest.download --dry-run     show what would be downloaded
    python -m src.ingest.download --limit 5     download the first five only
    python -m src.ingest.download               download everything selected
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

from src.common import PROJECT_ROOT, get_config, get_logger, resolve_path

log = get_logger("ingest.download")

MANIFEST_PATH = PROJECT_ROOT / "config" / "ipeds_manifest.csv"

# The manifest carries a url column, but it was built from the documentation
# table name, which is not always the download file name. This module rebuilds
# the URL from the resolved download name instead. Kept here rather than
# imported from build_manifest so the two stages stay independent.
DATA_FILE_URL = "https://nces.ed.gov/ipeds/datacenter/data/{table}.zip"

USER_AGENT = (
    "ipeds-ir-warehouse/0.1 (academic research portfolio project; "
    "https://github.com/celsia-arockiadass/ipeds-ir-warehouse)"
)


@dataclass
class DownloadResult:
    table_name: str
    download_name: str
    survey: str
    collection_year: str
    outcome: str  # DOWNLOADED | SKIPPED_EXISTS | MISSING | KNOWN_MISSING | FAILED
    http_status: str
    bytes: int
    seconds: float
    path: str
    url: str


def apply_name_overrides(table_name: str, overrides: list[dict[str, str]]) -> str:
    """
    Translate a documentation table name into the download file name.

    These are not the same thing. Tablesdoc lists tables as they exist in the
    Access database, where a column limit forces very wide tables to be split
    into _P1 and _P2 parts. The CSV complete data files have no such limit and
    ship the table undivided. Student Financial Aid is the case that bit us:
    SFA2223_P1 and SFA2223_P2 in the docs are one SFA2223.zip in the data.
    """
    for rule in overrides:
        pattern = re.compile(rule["pattern"], re.IGNORECASE)
        if pattern.match(table_name):
            return pattern.sub(rule["replacement"], table_name)
    return table_name


def is_known_missing(table_name: str, patterns: list[str]) -> bool:
    """True if NCES is known not to publish this table, confirmed by a real 404."""
    return any(re.match(p, table_name, re.IGNORECASE) for p in patterns)


def survey_folder(survey: str) -> str:
    """
    Turn a survey name into a folder name.

    'Student Financial Aid' becomes 'student_financial_aid'. Grouping raw
    files by survey rather than dumping 300 zips into one directory makes the
    raw layer navigable by a human, which matters when something looks wrong.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", survey.lower()).strip("_")
    return slug or "unknown_survey"


def load_manifest() -> list[dict[str, str]]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"{MANIFEST_PATH} not found. Run: python -m src.ingest.build_manifest"
        )
    with open(MANIFEST_PATH, "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def select(records: list[dict[str, str]], patterns: list[str],
           overrides: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Filter the manifest to the tables we intend to download, resolve each to
    its real download file name, and collapse duplicates.

    The deduplication matters. SFA2223_P1 and SFA2223_P2 are two rows in the
    documentation but one file on the server, so requesting both would fetch
    the same 1.5 MB twice and write it twice.
    """
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    selected = [
        record
        for record in records
        if any(pattern.match(record["table_name"]) for pattern in compiled)
    ]

    seen: set[str] = set()
    resolved: list[dict[str, str]] = []
    for record in selected:
        download_name = apply_name_overrides(record["table_name"], overrides)
        if download_name in seen:
            continue
        seen.add(download_name)
        record = dict(record)
        record["download_name"] = download_name
        resolved.append(record)

    return sorted(resolved, key=lambda r: (r["survey"], r["download_name"]))


def download_one(record: dict[str, str], raw_root: Path, timeout: int,
                 retries: int, backoff: int) -> DownloadResult:
    table = record["table_name"]
    download = record["download_name"]
    url = DATA_FILE_URL.format(table=download)
    folder = raw_root / survey_folder(record["survey"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{download}.zip"

    if target.exists() and target.stat().st_size > 0:
        return DownloadResult(
            table, download, record["survey"], record["collection_year"],
            "SKIPPED_EXISTS", "", target.stat().st_size, 0.0, str(target), url,
        )

    started = time.time()
    last_status = ""

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url, timeout=timeout, headers={"User-Agent": USER_AGENT}
            )
        except requests.RequestException as error:
            last_status = type(error).__name__
            log.warning("%s attempt %d raised %s", table, attempt, error)
            time.sleep(backoff)
            continue

        last_status = str(response.status_code)

        if response.status_code == 200:
            # A zip file starts with the bytes 'PK'. Checking this catches the
            # case where the server returns an HTML error page with a 200
            # status, which would otherwise be saved as a corrupt zip and only
            # fail much later during extraction.
            if not response.content.startswith(b"PK"):
                log.error(
                    "%s returned 200 but the body is not a zip (starts with %r)",
                    table, response.content[:16],
                )
                return DownloadResult(
                    table, download, record["survey"], record["collection_year"],
                    "FAILED", "200_not_zip", len(response.content),
                    round(time.time() - started, 2), "", url,
                )

            target.write_bytes(response.content)
            return DownloadResult(
                table, download, record["survey"], record["collection_year"],
                "DOWNLOADED", "200", len(response.content),
                round(time.time() - started, 2), str(target), url,
            )

        if response.status_code == 404:
            log.warning("%s not published (404)", download)
            return DownloadResult(
                table, download, record["survey"], record["collection_year"],
                "MISSING", "404", 0, round(time.time() - started, 2), "", url,
            )

        log.warning("%s attempt %d returned HTTP %d", table, attempt,
                    response.status_code)
        time.sleep(backoff)

    return DownloadResult(
        table, download, record["survey"], record["collection_year"],
        "FAILED", last_status, 0, round(time.time() - started, 2), "", url,
    )


def write_log(results: list[DownloadResult]) -> Path:
    log_path = resolve_path("logs") / "download_log.csv"
    with open(log_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(DownloadResult.__dataclass_fields__.keys())
        )
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))
    return log_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Download selected IPEDS data files")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be downloaded, make no requests")
    parser.add_argument("--limit", type=int, default=None,
                        help="download at most this many files")
    args = parser.parse_args()

    config = get_config()
    ingest = config["ingest"]
    selection = config["selection"]
    patterns = selection["include_patterns"]
    overrides = selection.get("download_name_overrides", []) or []
    known_missing = selection.get("known_missing_patterns", []) or []

    records = load_manifest()
    selected = select(records, patterns, overrides)

    log.info("Manifest holds %d tables, %d distinct files selected for download",
             len(records), len(selected))

    by_survey: dict[str, int] = {}
    for record in selected:
        by_survey[record["survey"]] = by_survey.get(record["survey"], 0) + 1
    for survey in sorted(by_survey):
        log.info("    %-38s %3d", survey, by_survey[survey])

    renamed = [
        r for r in selected if r["download_name"] != r["table_name"]
    ]
    if renamed:
        log.info("%d files use a download name that differs from the "
                 "documentation table name, see config.yaml", len(renamed))

    skipped_known = [
        r for r in selected if is_known_missing(r["download_name"], known_missing)
    ]
    if skipped_known:
        log.info("%d files are known not to be published and will not be "
                 "requested", len(skipped_known))

    if args.dry_run:
        log.info("Dry run. Nothing downloaded.")
        for record in selected[:10]:
            marker = "" if record["download_name"] == record["table_name"] else \
                f"  (documented as {record['table_name']})"
            log.info("    %-16s %s%s", record["download_name"],
                     DATA_FILE_URL.format(table=record["download_name"]), marker)
        return

    if args.limit:
        selected = selected[: args.limit]
        log.info("Limited to first %d files", len(selected))

    raw_root = resolve_path("raw")
    timeout = ingest.get("request_timeout_seconds", 120)
    retries = ingest.get("max_retries", 3)
    backoff = ingest.get("retry_backoff_seconds", 5)
    delay = ingest.get("delay_between_requests_seconds", 1)

    results: list[DownloadResult] = []
    started = time.time()

    for index, record in enumerate(selected, start=1):
        if is_known_missing(record["download_name"], known_missing):
            results.append(
                DownloadResult(
                    record["table_name"], record["download_name"],
                    record["survey"], record["collection_year"],
                    "KNOWN_MISSING", "", 0, 0.0, "",
                    DATA_FILE_URL.format(table=record["download_name"]),
                )
            )
            continue

        result = download_one(record, raw_root, timeout, retries, backoff)
        results.append(result)

        if result.outcome == "DOWNLOADED":
            log.info("[%3d/%3d] %-16s %8d bytes in %.1fs",
                     index, len(selected), result.download_name, result.bytes,
                     result.seconds)
            time.sleep(delay)
        elif result.outcome == "SKIPPED_EXISTS":
            log.info("[%3d/%3d] %-16s already present, skipped",
                     index, len(selected), result.download_name)
        else:
            log.warning("[%3d/%3d] %-16s %s",
                        index, len(selected), result.download_name, result.outcome)

    log_path = write_log(results)
    elapsed = time.time() - started

    counts: dict[str, int] = {}
    for result in results:
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    total_bytes = sum(r.bytes for r in results if r.outcome == "DOWNLOADED")

    log.info("=" * 70)
    log.info("Download complete in %.1f minutes", elapsed / 60)
    log.info("Attempted        : %d", len(results))
    for outcome in ("DOWNLOADED", "SKIPPED_EXISTS", "KNOWN_MISSING",
                    "MISSING", "FAILED"):
        log.info("  %-16s %d", outcome, counts.get(outcome, 0))
    log.info("Bytes downloaded : %.1f MB", total_bytes / 1_048_576)
    log.info("Per file log     : %s", log_path)
    if counts.get("FAILED"):
        log.error("There are FAILED rows. Rerun to retry them; existing files "
                  "are skipped so only the failures are attempted again.")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
