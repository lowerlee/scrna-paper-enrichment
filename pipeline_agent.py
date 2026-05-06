#!/usr/bin/env python3
"""
Pipeline observer agent.
Runs run_pipeline.py, annotates phase transitions, parses metrics,
then calls Claude to generate a markdown observation report.
"""
import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(override=True)

import anthropic

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")

# Ordered phase triggers — first match per line wins
PHASE_TRIGGERS = [
    ("fetch",    re.compile(r"Fetching bioRxiv")),
    ("dedup",    re.compile(r"New: \d+")),
    ("classify", re.compile(r"Classifying \[1/")),
    ("output",   re.compile(r"Digest written")),
]

PHASE_LABELS = {
    "fetch":    "Fetching papers from bioRxiv",
    "dedup":    "Deduplicating against database",
    "classify": "Classifying papers",
    "output":   "Writing digest",
}


def _parse_metrics(log_lines: list[str]) -> dict:
    m = {}
    page_count = 0
    for line in log_lines:
        hit = re.search(r"Fetch complete — (\d+) total items", line)
        if hit:
            m["total_items"] = int(hit.group(1))

        hit = re.search(r"Kept (\d+) papers", line)
        if hit:
            m["kept_papers"] = int(hit.group(1))

        if re.search(r"page \d+.\d+ / \d+ fetched", line):
            page_count += 1

        hit = re.search(r"New: (\d+)\s*\|\s*Already in DB \(skipped\): (\d+)", line)
        if hit:
            m["new_papers"] = int(hit.group(1))
            m["skipped_db"] = int(hit.group(2))

        hit = re.search(r"Skipping (\d+) papers with empty abstracts", line)
        if hit:
            m["skipped_abstract"] = int(hit.group(1))

        hit = re.search(r"Classified: (\d+) relevant, (\d+) not relevant, (\d+) errors", line)
        if hit:
            m["relevant"] = int(hit.group(1))
            m["not_relevant"] = int(hit.group(2))
            m["classify_errors"] = int(hit.group(3))

    m["fetch_pages"] = page_count
    return m


def _collect_errors(log_lines: list[str]) -> list[str]:
    return [l for l in log_lines if " ERROR " in l or " WARNING " in l]


def _generate_observations(client: anthropic.Anthropic, log_lines, phase_timings, metrics, errors, total_time, pipeline_args, returncode) -> str:
    def fmt(v):
        return str(v) if v is not None else "—"

    filter_rate = (
        f"{100 * metrics['kept_papers'] / metrics['total_items']:.1f}%"
        if metrics.get("total_items") and metrics.get("kept_papers")
        else "—"
    )

    prompt = f"""Analyze this bioRxiv paper classification pipeline run and write a concise ## Observations section.

Pipeline args: {' '.join(pipeline_args) if pipeline_args else '(none)'}
Exit code: {returncode}
Total runtime: {total_time:.1f}s

Phase timings:
{chr(10).join(f'  {PHASE_LABELS.get(k, k)}: {v:.1f}s' for k, v in phase_timings.items())}

Metrics:
  bioRxiv items returned: {fmt(metrics.get('total_items'))}
  Papers matching categories: {fmt(metrics.get('kept_papers'))} ({filter_rate} filter rate)
  Pages paginated: {metrics.get('fetch_pages', 0)}
  New (post-dedup): {fmt(metrics.get('new_papers'))}
  Skipped (already in DB): {fmt(metrics.get('skipped_db'))}
  Skipped (no abstract): {fmt(metrics.get('skipped_abstract'))}
  Relevant: {fmt(metrics.get('relevant'))}
  Not relevant: {fmt(metrics.get('not_relevant'))}
  Classification errors: {fmt(metrics.get('classify_errors'))}

Errors/warnings:
{chr(10).join(errors) if errors else '(none)'}

Focus on:
- bioRxiv fetch efficiency (filter rate, pages, time)
- Classification error rate and likely causes
- Which phase dominated total runtime and whether that's expected
- Specific actionable suggestions backed by the numbers above

Be concise. Bullet points. No generic advice."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=[{
            "type": "text",
            "text": "You write concise, data-driven pipeline run observations. Reference actual numbers. No filler.",
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _write_report(path, pipeline_args, returncode, total_time, phase_timings, metrics, errors, observations):
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "Success" if returncode == 0 else f"Failed (exit {returncode})"

    def fmt(v):
        return str(v) if v is not None else "—"

    filter_rate = (
        f"{100 * metrics['kept_papers'] / metrics['total_items']:.1f}%"
        if metrics.get("total_items") and metrics.get("kept_papers")
        else "—"
    )

    phase_rows = "\n".join(
        f"| {PHASE_LABELS.get(k, k)} | {v:.1f}s |"
        for k, v in phase_timings.items()
    )
    missing = [name for name in PHASE_LABELS if name not in phase_timings]
    phase_warning = (
        f"\n> Warning: phases not detected: {', '.join(missing)}\n"
        if missing else ""
    )

    errors_md = "\n".join(f"- `{e.strip()}`" for e in errors) if errors else "_None_"

    report = f"""# Pipeline Run Report

**Date:** {run_date}
**Args:** `{' '.join(pipeline_args) if pipeline_args else '(none)'}`
**Status:** {status}
**Total time:** {total_time:.1f}s

## Phase Breakdown
{phase_warning}
| Phase | Duration |
|-------|----------|
{phase_rows}

## bioRxiv Fetch Performance

| Metric | Value |
|--------|-------|
| Total items returned | {fmt(metrics.get('total_items'))} |
| Matching categories | {fmt(metrics.get('kept_papers'))} |
| Filter rate | {filter_rate} |
| Pages paginated | {metrics.get('fetch_pages', 0)} |
| Fetch duration | {phase_timings.get('fetch', 0):.1f}s |

## Classification Results

| Metric | Value |
|--------|-------|
| New papers (post-dedup) | {fmt(metrics.get('new_papers'))} |
| Skipped (already in DB) | {fmt(metrics.get('skipped_db'))} |
| Skipped (no abstract) | {fmt(metrics.get('skipped_abstract'))} |
| Relevant | {fmt(metrics.get('relevant'))} |
| Not relevant | {fmt(metrics.get('not_relevant'))} |
| Classification errors | {fmt(metrics.get('classify_errors'))} |

## Errors & Warnings

{errors_md}

## Observations

{observations}
"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(report)


def main():
    parser = argparse.ArgumentParser(description="Run pipeline and generate an observation report.")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD")
    parser.add_argument("--db", metavar="PATH")
    args = parser.parse_args()

    pipeline_args = []
    if args.from_date:
        pipeline_args += ["--from", args.from_date]
    if args.db:
        pipeline_args += ["--db", args.db]

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_pipeline.py")
    cmd = [sys.executable, script] + pipeline_args

    print(f"[agent] Starting pipeline: {' '.join(cmd)}\n")

    phase_timings: dict[str, float] = {}
    current_phase: str | None = None
    phase_start: float | None = None
    log_lines: list[str] = []

    start_time = time.time()

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        log_lines.append(line)
        print(line)

        for phase, pattern in PHASE_TRIGGERS:
            if pattern.search(line) and phase != current_phase:
                now = time.time()
                if current_phase and phase_start is not None:
                    phase_timings[current_phase] = now - phase_start
                current_phase = phase
                phase_start = now
                print(f"\n[agent] ── {PHASE_LABELS[phase].upper()} ──\n")
                break

    proc.wait()
    total_time = time.time() - start_time

    if current_phase and phase_start is not None:
        phase_timings[current_phase] = time.time() - phase_start

    missing_phases = [name for name in PHASE_LABELS if name not in phase_timings]
    if missing_phases:
        print(f"\n[agent] Warning: phases not detected in log: {', '.join(missing_phases)}")

    metrics = _parse_metrics(log_lines)
    errors = _collect_errors(log_lines)

    print(f"\n[agent] Run complete in {total_time:.1f}s. Generating report...")

    client = anthropic.Anthropic()
    observations = _generate_observations(
        client, log_lines, phase_timings, metrics, errors,
        total_time, pipeline_args, proc.returncode,
    )

    report_name = datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".md"
    report_path = os.path.join(REPORTS_DIR, report_name)
    _write_report(report_path, pipeline_args, proc.returncode, total_time,
                  phase_timings, metrics, errors, observations)

    print(f"[agent] Report saved: {report_path}")


if __name__ == "__main__":
    main()
