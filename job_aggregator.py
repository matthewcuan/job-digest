"""CLI entrypoint. Scheduling is owned by GitHub Actions, not this program."""
from __future__ import annotations

import sys

import typer
from loguru import logger

from job_aggregator import email_renderer
from job_aggregator.config import load_config, load_secrets
from job_aggregator.pipeline import RunResult
from job_aggregator.pipeline import run as run_pipeline
from job_aggregator.storage import get_storage

app = typer.Typer(
    add_completion=False,
    help="Aggregate job listings from job boards + ATS feeds and email a digest of new ones.",
)


def _log_summary(result: RunResult) -> None:
    for sr in result.source_results:
        if sr.ok:
            logger.info("  {} -> {} jobs{}", sr.source, sr.count, " (empty!)" if sr.near_empty else "")
        else:
            logger.warning("  {} -> FAILED: {}", sr.source, sr.error)
    logger.info(
        "Summary: {} new (fetched {}, after dedup {}, after filter {}, seen-skipped {})",
        len(result.new_jobs),
        result.total_fetched,
        result.after_dedup,
        result.after_filter,
        result.seen_skipped,
    )


@app.command()
def run(
    config: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the digest to stdout; do NOT email or record state."
    ),
) -> None:
    """One-shot run: fetch, filter, email new jobs, record them. (What Actions calls.)"""
    cfg = load_config(config)
    secrets = load_secrets()
    storage = get_storage(secrets)

    result = run_pipeline(cfg, secrets, storage=storage)
    _log_summary(result)

    if dry_run:
        _, text = email_renderer.render_digest(result, cfg)
        typer.echo("\n" + text)
        logger.info("[dry-run] no email sent, no state recorded")
        _finish(result)
        return

    try:
        sent = email_renderer.send_digest(result, cfg, secrets)
    except Exception as exc:  # noqa: BLE001 — surface send failures as a non-zero exit
        logger.error("Email send failed: {}: {}", type(exc).__name__, exc)
        raise typer.Exit(code=1)

    # Record only after a successful send so a delivery failure lets the next run retry.
    if result.new_jobs:
        recorded = storage.record(result.new_jobs)
        logger.info("Recorded {} job(s) to seen-store", recorded)
    elif sent:
        logger.info("Email sent (no new jobs to record)")

    _finish(result)


@app.command("reset-db")
def reset_db(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Clear the seen-jobs store (the next run re-emails everything that matches)."""
    secrets = load_secrets()
    storage = get_storage(secrets)
    if not yes:
        typer.confirm(f"Clear seen-store at {storage.path}?", abort=True)
    storage.reset()


def _finish(result: RunResult) -> None:
    """Exit non-zero only when every attempted source failed (so Actions shows red)."""
    if result.all_failed:
        logger.error("All {} source(s) failed", result.attempted)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
