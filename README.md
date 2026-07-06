# Zammad Reporting Toolkit

![CI](https://github.com/MarcosLealGh/zammad-reporting-toolkit/actions/workflows/ci.yml/badge.svg)

Service-desk metrics extraction for [Zammad](https://zammad.org/) via its REST API — built and used in a real production deployment (~150 users), where it turned a reactive support inbox into a measurable operation.

## The problem

Zammad's built-in reporting doesn't answer the questions management actually asks:

- How long until a user gets a **first response**?
- What's the real **resolution time** per technical area or branch office?
- How many tickets never got an agent reply at all?

Without numbers, IT support is invisible work. With numbers, it's a business case.

## The solution

`extraer_tickets.py` pulls every ticket from a Zammad group through the REST API (paginated, custom fields included), walks each ticket's article history, and computes per-ticket time metrics:

- **First response time** — ticket creation → first agent reply (minutes)
- **Resolution time** — ticket creation → close (minutes)
- **Unanswered tickets** — tickets with zero agent replies
- **Aggregated summaries** — totals, resolution rate and time averages grouped by any custom field (technical area, branch, category…)

Output is plain CSV: ready for Excel, pandas, or a Word report generator.

## Results in production

Used as the data source for monthly operations reports presented to management:

- **100+ tickets** analyzed in the first month of operation
- **86% resolution rate** measured (not estimated) for IT
- Per-branch and per-area breakdowns that justified staffing and infrastructure decisions

## Install

```bash
pip install -e .        # or: pip install -e ".[dev]" for tests + linter
```

## Usage

```bash
cp .env.example .env    # then fill in, or export the variables directly
export ZAMMAD_URL="https://your-zammad-server"
export ZAMMAD_TOKEN="your_api_token"     # Zammad: Profile → Token Access (ticket.agent)

zammad-report --group 3
```

Everything is configured through CLI flags and environment variables — no editing source:

```
--group N              group ID to extract (required)
--exclude-states ...   state IDs to skip (default: 5 7 = merged, spam)
--resolved-state NAME  state name that counts as "resolved" (default: closed)
--output-dir DIR       where to write the CSVs (default: current dir)
-v / --verbose         DEBUG logging
```

Output:

```
tickets.csv               one row per ticket, with time metrics
resumen_<field>.csv       aggregated metrics per custom-field value
```

## Design notes

- **No credentials in code** — server URL and token come from environment variables.
- **Secure by default** — TLS certificate verification is **on**; internal deployments with a self-signed certificate opt out explicitly with `ZAMMAD_VERIFY_SSL=false` (a warning is logged).
- **Pure, tested metric functions** — `calcular_tiempos` and `construir_resumen` take their inputs as arguments and are covered by unit tests, so the time math is verifiable without a live server.
- **`expand=true` everywhere** — the Zammad API returns bare IDs without it; the script always requests expanded objects so CSVs contain human-readable names.
- **Diagnostics vs. output** — status/errors go to `stderr` via `logging`; the report itself goes to `stdout` and the CSV files.
- Code comments and CLI output are in Spanish (built for a Spanish-speaking operations team).

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

## License

MIT — see [LICENSE](LICENSE).
