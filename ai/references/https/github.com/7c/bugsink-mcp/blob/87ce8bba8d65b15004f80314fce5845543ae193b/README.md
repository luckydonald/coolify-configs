# Bugsink MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server for interacting with [Bugsink](https://www.bugsink.com/) error tracking via LLMs.

This server enables AI assistants like Claude, Cursor, and other MCP-compatible tools to query and analyze errors from your Bugsink instance.

> **This is a fork of [j-shelfwood/bugsink-mcp](https://github.com/j-shelfwood/bugsink-mcp)** by Joris Schelfhout, who built the original v0.1 / v0.2 server (basic read tools, mutations, stacktrace rendering). All credit for the foundation goes to upstream.
>
> **Fork:** [`7c/bugsink-mcp`](https://github.com/7c/bugsink-mcp) — adds a forensics layer for incident analysis, cursor pagination, a class-based modular structure, and duration-shorthand time inputs. See [What this fork adds](#what-this-fork-adds) below.

## What this fork adds

Started from upstream v0.2.0. Everything below is new in this fork.

### v0.4.1 — Parallel scans + time budget (timeout fix)
- **Parallel detail fetches.** `EventScanner` now fans out per-issue event-detail HTTP calls with bounded concurrency (default `8`, exposed as the `concurrency` param). Typical 5–10× wall-clock speedup on `search_events`, `aggregate_events`, `cross_tabulate`, `histogram`, `sample_events`, `get_project_stats`. Issues are still walked serially to preserve the `since` short-circuit.
- **Per-call wall-clock budget.** New `time_budget_ms` param (default `200000` = 200s, sized below typical MCP client timeouts of ~240s). When the budget is exceeded, the scan returns its partial result with `stop_reason: "time-budget-exhausted"` and `complete: false` instead of letting the client cancel the whole call. Result envelopes now include `elapsed_ms`.
- **Issue-aware visitor.** `EventScanner.forEach` now passes `(event, lightweight, issue)` so consumers can read issue-level fields (`message`, `calculated_type`, `transaction`) directly from `Issue` rather than rummaging in `event.data`. New `DerivedPathResolver.getWithIssue(event, issue, path)` helper.
- **Stderr heartbeats** every ~15s during long scans (`scanned=N fetched=M matched=K`) — visible in the MCP server log so users can see a long-running call is still making progress.

### v0.4.0 — Bulk analytics & cross-tabulation
- **`aggregate_summary`** — fast aggregation that picks one of three strategies based on `group_by`:
  - *issue-field* (exact, **zero event fetches**): `message`, `calculated_type`, `transaction`, `is_resolved`, `is_muted`
  - *lightweight-derived* (exact, no detail fetch): `timestamp_hour`, `timestamp_day`, `timestamp_5min`
  - *sampled-per-issue* (approximate, one detail fetch per issue × `digested_event_count`): `tags.*`, `level`, `request.host`, `request.ua_family`. Response declares `strategy` and includes a `warning` when sampled.
- **`cross_tabulate`** — two-dimensional group-by. For each top-N `group_by` value, returns top-M `breakdown_by` values and counts. Single scan, both axes counted at once.
- **`sample_events`** — projects each matched event down to a flat dict of just the requested dotted-path fields (~95% smaller payload than `search_events`).
- **`histogram`** — numeric bucketing. Either explicit breakpoints (`[0,100,500,1000,5000,15000]`) or auto `bucket_size`. Reports `null_or_missing`, `min_observed`, `max_observed`, plus open-ended overflow.
- **Duration shorthand** for `since`/`until` everywhere: `"30s"`, `"30m"`, `"2h"`, `"1d"`, `"1w"` — interpreted as "<duration> ago from now". ISO-8601 still works.
- `search_events.summary_only` now defaults to **`true`** (lightweight envelopes; flip to `false` for full `data`). **Breaking change vs v0.3.0.**

### v0.3.0 — Forensics MVP
- **`get_event_full`** — return the COMPLETE event JSON. Accepts public Sentry `event_id` (32-hex or dashed UUID) OR Bugsink-internal UUID; falls back to project scan when needed.
- **`search_events`** — cross-issue scan with time-range, message, tag, dotted-path (`fields`), and free-text (`query`) filters. Uses message ≈ `issue.calculated_value` to skip whole issues.
- **`aggregate_events`** — exact group-by + count over a dotted path on `event.data`. Fetches detail per event.
- **`get_project_stats`** — total + breakdown over a window, defaults `group_by` to `message`.
- **Cursor pagination** in `BugsinkClient` with `iterateIssues` / `iterateEvents` async iterators (upstream client paged once).
- **Class-based modular refactor**: `index.ts` went from 554 → 70 lines; tools split into `src/tools/*`, forensics primitives into `src/forensics/*` (`EventScanner`, `EventAggregator`, `SummaryAggregator`, `EventCrossTabulator`, `EventHistogrammer`, `EventProjector`), shared utilities into `src/utils/*` (`PathResolver`, `EventIdNormalizer`, `TimeRange`, `DerivedPathResolver`, `TimestampBucket`, `UrlHostExtractor`, `UserAgentClassifier`).
- **OpenAPI reference** — full Bugsink API documented in [`docs/BUGSINK-API.md`](docs/BUGSINK-API.md), generated from the live `/api/canonical/0/schema/` spec.

### Honest disclosure on `aggregate_summary`
Bugsink's lightweight `EventList` envelope does **not** include `data.tags` / `data.message` / `data.level` — only `id, event_id, issue, project, timestamp, ingested_at, digested_at, digest_order, grouping`. Genuinely fast tag aggregation is therefore impossible without server-side help. The *sampled-per-issue* strategy is an opt-in approximation: accurate for fields constant within an issue (`level`, often `request.host`), badly skewed for high-cardinality per-event fields (`tags.juh`, `tags.cs`). The response always declares `strategy` so callers know whether the count is exact or sampled — use `aggregate_events` for exact tag counts.

## Features (full tool list)

### Read & summarize (from upstream)
- **List Projects / Teams / Issues / Events / Releases**
- **Get Project / Issue / Event / Release** — human-readable summaries
- **Get Stacktrace** — pre-rendered Markdown
- **Test Connection** — verify API connectivity

### Mutations (from upstream v0.2)
- **Create / Update Project**, **Create / Update Team**, **Create Release**

### Forensics (added in this fork)
- `get_event_full`, `search_events`, `sample_events`
- `aggregate_events`, `aggregate_summary`, `cross_tabulate`, `histogram`
- `get_project_stats`

See [What this fork adds](#what-this-fork-adds) for the full description of each forensics tool.

### Introspection
- **`get_version`** — returns the running server's `name`, `version`, `description`, `homepage`, `bugsink_url`, and `node_version`. The `version` value is read at runtime from `package.json` so it always matches the published artifact.

> Agents that need to assert a specific version before issuing version-sensitive tool calls can either inspect `serverInfo.version` from the MCP `initialize` response, or call `get_version` mid-session. Example assertion:
>
> ```jsonc
> // tools/call → get_version
> { "name": "bugsink-mcp", "version": "0.4.0", ... }
> ```

## Installation

### Via npx (Recommended)

```bash
npx bugsink-mcp
```

### Global Install

```bash
npm install -g bugsink-mcp
```

### From Source

```bash
git clone https://github.com/7c/bugsink-mcp.git
cd bugsink-mcp
npm install
npm run build
```

> Original upstream repo: [`j-shelfwood/bugsink-mcp`](https://github.com/j-shelfwood/bugsink-mcp)

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BUGSINK_URL` | Yes | Your Bugsink instance URL (e.g., `https://error-tracking.example.com`) |
| `BUGSINK_TOKEN` | Yes | API token for authentication |

### Generating an API Token

```bash
# Via Bugsink management command
bugsink-manage create_auth_token
```

Or through the Bugsink web UI under Settings > API Tokens.

## MCP Client Configuration

### Claude Desktop

Add to your Claude Desktop configuration (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "bugsink": {
      "command": "npx",
      "args": ["bugsink-mcp"],
      "env": {
        "BUGSINK_URL": "https://your-bugsink-instance.com",
        "BUGSINK_TOKEN": "your-api-token"
      }
    }
  }
}
```

### Claude Code CLI

```bash
claude mcp add bugsink -- npx bugsink-mcp
```

Then set environment variables in your shell or `.env` file.

### Cursor

Add to your Cursor MCP settings:

```json
{
  "mcpServers": {
    "bugsink": {
      "command": "npx",
      "args": ["bugsink-mcp"],
      "env": {
        "BUGSINK_URL": "https://your-bugsink-instance.com",
        "BUGSINK_TOKEN": "your-api-token"
      }
    }
  }
}
```

## Available Tools

### `test_connection`
Test connectivity to your Bugsink instance.

### `list_projects`
List all projects in the Bugsink instance.

### `get_project`
Get detailed information about a specific project including DSN.

**Parameters:**
- `project_id` (number, required): The project ID

### `list_teams`
List all teams in the Bugsink instance.

### `list_issues`
List issues for a specific project.

**Parameters:**
- `project_id` (number, required): The project ID
- `status` (string, optional): Filter by status ('unresolved', 'resolved', 'muted')
- `limit` (number, optional): Max results (default: 25)

### `get_issue`
Get detailed information about a specific issue.

**Parameters:**
- `issue_id` (number, required): The issue ID

### `list_events`
List events (individual error occurrences) for a specific issue.

**Parameters:**
- `issue_id` (number, required): The issue ID
- `limit` (number, optional): Max results (default: 10)

### `get_event`
Human-readable event summary including stacktrace, tags, contexts.

**Parameters:**
- `event_id` (string, required): Bugsink-internal event UUID

### `get_event_full`
Returns the complete event JSON (raw `data` payload + Bugsink envelope). Accepts public Sentry `event_id` (32-hex or dashed) or Bugsink-internal UUID.

**Parameters:**
- `event_id` (string, required): Public Sentry id or Bugsink-internal UUID
- `project_id` (number, optional): Restrict fallback scan to one project
- `max_scanned` (number, optional, default 5000): Cap on events examined during fallback scan

### `search_events`
Cross-issue event scan with filters. Returns full event JSON for each match.

**Parameters:**
- `project_id` (number, required)
- `since` / `until` (ISO-8601 strings, optional): time window
- `message` (object, optional): `{value, exact?, case_sensitive?}` — also short-circuits issue iteration when `data.message` ≈ `issue.calculated_value`
- `tags` (object, optional): equality filters on `data.tags`, AND-combined
- `fields` (object, optional): dotted-path equality filters on `data` (e.g. `{"extra.diag.env.dnt":"true"}`)
- `query` (string, optional): case-insensitive substring search across JSON-serialized `data`
- `summary_only` (bool, optional, default false): skip fetching full `data`, return lightweight envelope only
- `limit` (number, optional, default 50, max 500): max matched events
- `max_fetched` (number, optional, default 500, max 5000): cap on full event fetches per call
- `max_scanned` (number, optional, default 5000, max 50000): cap on lightweight events examined per call

### `aggregate_events`
Group-by + count over a dotted path on `event.data`. Sorted descending.

**Parameters:**
- `project_id` (number, required)
- `group_by` (string, required): dotted path (e.g. `tags.page_host`, `extra.diag.timing.protocol`, `request.headers.User-Agent`, `level`)
- `since`, `until`, `message`, `tags`, `fields`, `query` — same as `search_events`
- `top_n` (number, optional, default 20, max 500)
- `max_fetched` (number, optional, default 2000)
- `max_scanned` (number, optional, default 5000)

### `get_project_stats`
Event totals + group-by counts over a window. Useful for sent-vs-arrived sanity checks.

**Parameters:**
- `project_id` (number, required)
- `since`, `until`, `message`, `tags`, `fields` — same shape as `search_events`
- `group_by` (string, optional, default `"message"`): dotted path on `event.data`
- `max_fetched` (number, optional, default 5000), `max_scanned` (number, optional, default 20000)

## Example Usage

Once configured, you can ask your AI assistant:

- "List all projects in Bugsink"
- "Show me the latest issues for project 1"
- "What's the stacktrace for issue #42?"
- "Get the details of the most recent error event"
- **Forensics:** "Show me the top 20 page_hosts for `bad_xhr_status` events on project 2 in the last 24h"
- **Forensics:** "Give me the full payload for event_id `e1807dca09774e30b9c6c98b4eb96267` from project 2"
- **Forensics:** "How many events arrived between 17:00 and 18:00 UTC on project 2, broken down by message?"

## Development

```bash
# Install dependencies
npm install

# Run in development mode
npm run dev

# Build for production
npm run build

# Run tests
npm test
```

## API Compatibility

This server is designed for [Bugsink](https://www.bugsink.com/), a self-hosted error tracking platform. Bugsink uses its own REST API (`/api/canonical/0/`) which is different from Sentry's API.

**Note:** This server does NOT work with Sentry or Sentry-hosted services. For Sentry, use the official [sentry-mcp](https://github.com/getsentry/sentry-mcp) server.

## License

MIT — same as upstream.

## Credits

- **Upstream / original author:** [Joris Schelfhout](https://github.com/j-shelfwood) — [`j-shelfwood/bugsink-mcp`](https://github.com/j-shelfwood/bugsink-mcp). Built the original MCP server (v0.1) and the mutation tools (v0.2). All thanks for the foundation.
- **Fork maintainer:** [`7c/bugsink-mcp`](https://github.com/7c/bugsink-mcp) — forensics layer (v0.3 / v0.4), cursor pagination, class-based modular structure.

## Contributing

For features specific to this fork (forensics, aggregation, etc.) please open an issue or PR on [`7c/bugsink-mcp`](https://github.com/7c/bugsink-mcp).

For changes to the underlying client / mutation tools that should also flow upstream, consider opening a PR against [`j-shelfwood/bugsink-mcp`](https://github.com/j-shelfwood/bugsink-mcp) as well.
