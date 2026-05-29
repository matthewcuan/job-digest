"""CLI entrypoint. Scheduling is owned by GitHub Actions, not this program."""
from __future__ import annotations

import sys

import typer
from loguru import logger

from job_aggregator import email_renderer
from job_aggregator.config import load_config, load_secrets, resolve_email
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

    # Optional housekeeping: keep the seen-store (and its state-branch history) small.
    if secrets.state_retention_days:
        storage.prune(secrets.state_retention_days)

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


@app.command()
def probe(
    companies: list[str] = typer.Argument(..., help="Company names to look up ATS slugs for"),
) -> None:
    """Find Greenhouse/Lever/Ashby board slugs for companies (for sources.*.companies)."""
    from job_aggregator.probe import probe_company

    found: dict[str, list[str]] = {"greenhouse": [], "lever": [], "ashby": []}
    for name in companies:
        hits = probe_company(name)
        if not hits:
            typer.echo(f"{name}: no public ATS board found (check the careers URL for the exact slug)")
            continue
        typer.echo(f"{name}:")
        for ats, slug, count in hits:
            typer.echo(f"  {ats:<10} {slug}  ({count} open jobs)")
            found[ats].append(slug)
    if any(found.values()):
        typer.echo("\n# Paste into config.yaml under `sources:`")
        for ats, slugs in found.items():
            if slugs:
                typer.echo(f"#   {ats}: {{ enabled: true, limit: 50, companies: {sorted(set(slugs))} }}")


@app.command()
def doctor(
    config: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Preflight: validate config, ping each enabled source, test SMTP login (no email sent)."""
    from job_aggregator.sources import build_sources

    problems = 0
    try:
        cfg = load_config(config)
        typer.echo("✓ config loaded")
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"✗ config: {exc}")
        raise typer.Exit(1)

    secrets = load_secrets()
    sources = build_sources(cfg, proxy_url=secrets.proxy_url)
    if not sources:
        typer.echo("✗ no sources enabled in config.sources")
        problems += 1
    for source, limit in sources:
        result = source.fetch(cfg.search, min(limit, 5))
        if result.ok:
            note = " (0 results — possible block)" if result.near_empty else ""
            typer.echo(f"✓ source {source.name}: {result.count} job(s){note}")
        else:
            typer.echo(f"✗ source {source.name}: {result.error}")
            problems += 1

    resolved = resolve_email(cfg.email, secrets)
    try:
        email_renderer.verify_login(resolved)
        typer.echo(f"✓ SMTP login OK ({resolved.host}:{resolved.port} as {resolved.user})")
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"✗ SMTP: {type(exc).__name__}: {exc}")
        problems += 1

    typer.echo("\nAll good." if problems == 0 else f"\n{problems} problem(s) found.")
    raise typer.Exit(0 if problems == 0 else 1)


@app.command("test-email")
def test_email(
    config: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Send a one-off sample digest to confirm delivery and formatting (check spam too)."""
    from job_aggregator.models import Job, SourceResult
    from job_aggregator.util import now_utc

    cfg = load_config(config)
    secrets = load_secrets()
    sample = Job(
        title="Senior Python Engineer (SAMPLE)",
        company="Example Co",
        location="Remote - US",
        salary="$160k–$210k/yr",
        description="Sample card from `test-email`.",
        description_snippet="This sample card confirms your email delivery and formatting work.",
        url="https://example.com/jobs/sample",
        source="greenhouse",
        posted_date=now_utc(),
        job_id="sample",
    )
    result = RunResult(
        source_results=[
            SourceResult("greenhouse", [sample], True, None, False),
            SourceResult("linkedin", [], False, "example failure: 429 (this is a test)", False),
        ],
        new_jobs=[sample],
        total_fetched=1,
        after_dedup=1,
        after_filter=1,
    )
    resolved = resolve_email(cfg.email, secrets)
    try:
        html, text = email_renderer.render_digest(result, cfg)
        email_renderer.send_email(resolved, f"{cfg.email.subject_prefix} test email", html, text)
        typer.echo(f"Sent a test digest to {resolved.recipient}. Check your inbox and spam folder.")
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"✗ failed to send: {type(exc).__name__}: {exc}")
        raise typer.Exit(1)


@app.command()
def prune(
    days: int = typer.Option(..., "--days", help="Drop seen entries older than this many days"),
) -> None:
    """Prune old entries from the seen-jobs store to keep the state branch small."""
    storage = get_storage(load_secrets())
    removed = storage.prune(days)
    typer.echo(f"Pruned {removed} entr{'y' if removed == 1 else 'ies'} older than {days} days from {storage.path}")


def _finish(result: RunResult) -> None:
    """Exit non-zero only when every attempted source failed (so Actions shows red)."""
    if result.all_failed:
        logger.error("All {} source(s) failed", result.attempted)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
