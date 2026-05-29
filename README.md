# Job Aggregator → Email Digest

Aggregates job listings from major boards (LinkedIn, Indeed, Glassdoor, Google Jobs,
ZipRecruiter via [JobSpy](https://github.com/speedyapply/JobSpy)) **and** company-direct
feeds (Greenhouse, Lever, Ashby, and Workday), de-duplicates and filters them against your search
criteria, remembers what it has already shown you, and emails an HTML digest of **new**
listings. It runs on a schedule via GitHub Actions.

## How it's designed (read this first)

LinkedIn / Indeed / Glassdoor actively block scrapers, and **GitHub Actions runners use
IP ranges these sites block especially aggressively.** So this tool treats **partial
failure as the normal case**:

- **ATS APIs (Greenhouse/Lever/Ashby) are the reliable sources** from CI — they're
  public/programmatic. Add your target companies' ATS board slugs and you'll get data
  even when the scraped boards are blocked.
- Scraped boards (LinkedIn/Indeed/Glassdoor/Google) are **best-effort** and off or
  unreliable from CI IPs. Google in particular returns 0 from datacenter IPs (it withholds
  its jobs-widget markup), so it ships **disabled** — enable it only behind a residential
  `PROXY_URL`. Each source is isolated:
  one failing never stops the others, and the email header shows each source's health
  (🟢 ok / ⚪ 0 results / 🔴 failed) so a silently-broken board is visible.
- The run exits non-zero **only if every source failed** (so the Actions run goes red).
  If *all* sources fail it also emails a failure summary (configurable).

## Quick start (local)

```bash
# 1. Create a virtualenv and install deps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
#   If you hit "ensurepip is not available" (Debian/Ubuntu), either
#   `sudo apt install python3-venv`, or bootstrap pip without it:
#     python3 -m venv .venv --without-pip
#     curl -sSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python

# 2. Configure
cp config.yaml.example config.yaml      # edit search criteria + which sources/companies
cp .env.example .env                     # edit SMTP credentials

# 3. Preview without sending anything
.venv/bin/python job_aggregator.py run --dry-run

# 4. Real run (sends email, records seen jobs)
.venv/bin/python job_aggregator.py run
```

`--dry-run` prints the plain-text digest to stdout and does **not** send email or touch the
seen-store, so it's safe to run repeatedly while tuning `config.yaml`.

### CLI

```
python job_aggregator.py run                 # one-shot: fetch, filter, email, record (what Actions runs)
python job_aggregator.py run --dry-run        # print digest to stdout; no email, no state change
python job_aggregator.py run -c other.yaml    # use a different config file
python job_aggregator.py reset-db             # clear the seen-jobs store (prompts; -y to skip)

# Setup helpers
python job_aggregator.py probe "Stripe" "Notion"   # find ATS board slugs for companies
python job_aggregator.py doctor                    # validate config + ping sources + test SMTP login
python job_aggregator.py test-email                # send a one-off sample digest (confirm delivery/spam)
python job_aggregator.py prune --days 90           # drop seen entries older than N days
```

There is no `schedule` command — GitHub Actions owns scheduling.

**First-time setup flow:** `probe` your target companies → paste the slugs into `config.yaml`
→ fill in `.env` → `doctor` to confirm everything resolves and SMTP logs in → `test-email`
to confirm the message arrives → `run --dry-run` to eyeball the digest → push & schedule.

`STATE_RETENTION_DAYS` (env, optional) auto-prunes seen entries older than that many days at
the end of each `run`, keeping the `state` branch small. The workflow sets it to 90.

## Configuration

`config.yaml` (committed) holds search criteria, source toggles, and email settings.
Credentials come **only** from environment variables / `.env` / GitHub Secrets — never put a
password in `config.yaml`. See `config.yaml.example` for every option with comments.

**Criteria highlights** (full filtering happens client-side, since boards ignore filters):

- `must_have` — AND filter: a job must contain **every** term (in title or description).
- `nice_to_have` — ranking boost only; never excludes a job.
- `work_mode` — `any | remote | hybrid | onsite`. Only *remote* is a hard server-side flag.
- `salary_min` / `date_posted_hours` — **keep-but-flag**: a job that doesn't *list* a salary
  (or date) is kept, not dropped; only a job that lists a *lower* salary / *older* date is excluded.
- `experience_level` — lenient heuristic on the title (e.g. won't show "Senior X" when you
  asked for "entry").

### Adding ATS companies (the reliable path)

Greenhouse/Lever/Ashby APIs serve one company's board given its slug. Find the slug in the
careers URL and add it under `sources.<ats>.companies`:

| Source | Careers URL | `companies:` value |
|-----|-------------|--------------|
| Greenhouse | `job-boards.greenhouse.io/<slug>` | slug, e.g. `stripe` |
| Lever | `jobs.lever.co/<slug>` | slug, e.g. `leverdemo` |
| Ashby | `jobs.ashbyhq.com/<slug>` (**case-sensitive**) | slug, e.g. `Ashby` |
| Workday | `<tenant>.<dc>.myworkdayjobs.com/.../<Site>` | the **full career-site URL** |

Greenhouse/Lever/Ashby take board **slugs** (use `probe` to find them). **Workday** takes
the full career-site URL (each company is its own tenant) — it's how to reach large
employers that don't use the others. Of the "Magnificent 7", only **Nvidia** is on Workday;
the rest run custom portals. Workday fetches per-job detail, so keep its `limit` modest.

```yaml
sources:
  greenhouse:
    enabled: true
    limit: 50
    companies: ["stripe", "airbnb", "databricks"]
  workday:
    enabled: true
    limit: 15
    companies: ["https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"]
```

### Targeting specific companies on Indeed

For big employers that *don't* expose an ATS/Workday board (Apple, Microsoft, Google,
Amazon, Meta, Tesla — the rest of the "Magnificent 7"), use `search.target_companies`.
Indeed honors a `company:"…"` query operator, so the aggregator runs one Indeed search per
listed company and keeps only jobs whose employer actually matches:

```yaml
search:
  must_have: ["software engineer"]
  target_companies: ["Apple", "Google", "Amazon", "Meta"]
sources:
  indeed: { enabled: true, limit: 25 }   # this feature is most reliable on Indeed
```

It applies to any keyword-search board, but Indeed is the one that reliably honors the
operator (and works best from CI). Leave `target_companies` empty to search all employers.

## Email (Gmail app password)

Gmail blocks plain password SMTP. With 2-Step Verification enabled, create an **app password**:

1. Google Account → **Security** → enable **2-Step Verification**.
2. Security → **App passwords** → generate one (name it e.g. "job-digest").
3. Use the 16-character value (no spaces) as `SMTP_PASSWORD`.
4. `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`/`EMAIL_TO` = your address.

Any STARTTLS SMTP server works; just set the four `SMTP_*` vars accordingly.

## Deploying on GitHub Actions

The workflow is `.github/workflows/job-digest.yml` (schedule + manual `workflow_dispatch`).

### 1. Add Secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Required | Notes |
|--------|----------|-------|
| `SMTP_HOST` | ✅ | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | ✅ | e.g. `587` |
| `SMTP_USER` | ✅ | sender address |
| `SMTP_PASSWORD` | ✅ | Gmail app password / SMTP password |
| `EMAIL_TO` | ✅ | recipient address |
| `PROXY_URL` | ➖ | optional residential proxy (see below) |

The workflow needs no other setup — it uses the built-in `GITHUB_TOKEN` to manage the
`state` branch (the workflow has `permissions: contents: write`).

### 2. Editing the schedule (cron is UTC!)

The two `cron:` lines in the workflow are **UTC**. Convert from your local time:

```
local_hour_UTC = local_hour - UTC_offset
# US Eastern (UTC-4 in summer): 8am ET = 12:00 UTC, 6pm ET = 22:00 UTC
# US Pacific (UTC-7 in summer): 8am PT = 15:00 UTC, 6pm PT = 01:00 UTC (next day)
```

```yaml
on:
  schedule:
    - cron: "0 12 * * *"   # 12:00 UTC
    - cron: "0 22 * * *"   # 22:00 UTC
```

Note: GitHub does **not** adjust for daylight saving, and scheduled runs can be delayed
under load. Use **Run workflow** (manual dispatch) to test immediately.

### 3. The `state` branch (seen-jobs persistence)

Runners are ephemeral, so the seen-jobs store lives on a dedicated **`state` branch**:

- Each run checks the `state` branch out into `./.state/`, reads/writes
  `.state/seen_jobs.jsonl`, then commits & pushes it back if it changed.
- The branch is **created automatically on the first run** (orphan branch, no shared history).
- Because the store is plain JSONL, the branch's commit history is a readable audit log of
  every listing the tool has ever emailed.

**To reset it** (re-email everything that currently matches): delete the `state` branch —

```bash
git push origin --delete state
```

The next run recreates it empty. Locally, `python job_aggregator.py reset-db` clears
`.state/seen_jobs.jsonl`.

### 4. Adding a proxy (if GitHub IPs get fully blocked)

LinkedIn in particular rate-limits datacenter IPs fast. To route JobSpy through a
residential proxy, add a `PROXY_URL` secret:

```
PROXY_URL=user:pass@host:port
```

It's passed to JobSpy's `proxies` automatically; leave it unset to run direct. ATS sources
don't need it.

## State, ToS & expectations

- **ToS / rate limiting:** LinkedIn/Indeed/Glassdoor prohibit automated scraping and block
  aggressively. This tool does **not** try to defeat Cloudflare or auth walls — if a source
  blocks us, it's logged, shown in the email's failure summary, and the run continues with
  the others. Use it for your own personal job search and keep volumes modest. The ATS APIs
  are public and meant to be consumed programmatically — prefer them.
- **Empty is normal from CI.** Don't be surprised if LinkedIn/Indeed return 0 from Actions;
  that's why the ATS boards are first-class. Google is disabled by default (it returns 0
  from CI IPs); Indeed is hit-or-miss but often works.

## Architecture

```
job_aggregator.py            CLI (Typer): run / run --dry-run / reset-db
job_aggregator/
  config.py                  pydantic config (YAML) + pydantic-settings secrets (env)
  models.py                  Job + SourceResult dataclasses
  sources/
    base.py                  JobSource interface (fetch() wraps _fetch with failure isolation)
    jobspy_source.py         one instance per board; per-site scrape_jobs() call
    greenhouse.py / lever.py / ashby.py   ATS public-API adapters (by slug)
    workday.py                 Workday CXS adapter (by career-site URL; list + per-job detail)
  pipeline.py                fetch → flag → dedup → filter → seen-filter → rank
  dedup.py                   exact (job_id) + fuzzy (rapidfuzz) merge, richest description wins
  rank.py                    must_have density + nice_to_have bonus + recency
  storage.py                 Storage interface + JsonlStorage (.state/seen_jobs.jsonl)
  email_renderer.py          Jinja2 HTML+text render, smtplib send
  templates/                 email.html.j2, email.txt.j2
```

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests cover the storage round-trip, dedup/merge, source field-mapping (incl. Lever's
epoch-ms dates and Greenhouse's entity-escaped HTML), failure isolation, and an end-to-end
pipeline run with mocked sources rendered to email.
