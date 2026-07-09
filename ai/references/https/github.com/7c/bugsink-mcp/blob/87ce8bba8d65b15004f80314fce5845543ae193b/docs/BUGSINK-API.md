# Bugsink API Reference

> **Source:** [`https://bugreport.uk/api/canonical/0/schema/swagger-ui/`](https://bugreport.uk/api/canonical/0/schema/swagger-ui/)
> **Spec:** OpenAPI 3.0.3 — `Bugsink` v1.0.0
> **Base URL:** `https://bugreport.uk/api/canonical/0`
> **Auth:** `Authorization: Bearer <token>` (HTTP Bearer; security scheme `BearerAuth`)

All list endpoints are cursor-paginated and return `{ next, previous, results: [...] }`.

---

## Table of contents

- [Events](#events)
- [Issues](#issues)
- [Projects](#projects)
- [Releases](#releases)
- [Teams](#teams)
- [Schemas](#schemas)
- [Authentication](#authentication)

---

## Events

> List requires `?issue=<uuid>`. Optional `?order=asc|desc` (default: `desc`).
> List view ordered by `digest_order` and **omits `data`** for size.
> Retrieve includes the full `data` payload (pure PK lookup; filters/order ignored).

### `GET /events/`

List events for an issue.

**Query parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `issue` | query | uuid | yes | Filter events by issue UUID |
| `order` | query | enum(`asc`,`desc`) | no | Sort order of `digest_order` (default `desc`) |
| `cursor` | query | string | no | Pagination cursor |

**Responses**

- `200` → [`PaginatedEventListList`](#paginatedeventlistlist)

### `GET /events/{id}/`

Retrieve a single event with full `data`.

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `id` | uuid | Bugsink-internal event UUID |

**Responses**

- `200` → [`EventDetail`](#eventdetail)

### `GET /events/{id}/stacktrace/`

Render the event's stacktrace (frames, source, locals) as Markdown-like text.

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `id` | uuid | Bugsink-internal event UUID |

**Responses**

- `200` → `text/markdown` (string body)

---

## Issues

> List requires `?project=<id>`. Optional `?order=asc|desc` (default `asc`) and `?sort=digest_order|last_seen` (default `digest_order`).
> Retrieve is a pure PK lookup; soft-deletes implied.

### `GET /issues/`

List issues for a project.

**Query parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `project` | query | integer | yes | Filter issues by project id |
| `sort` | query | enum(`digest_order`,`last_seen`) | no | Sort mode (default `digest_order`) |
| `order` | query | enum(`asc`,`desc`) | no | Sort order (default `asc`) |
| `cursor` | query | string | no | Pagination cursor |

**Responses**

- `200` → [`PaginatedIssueList`](#paginatedissuelist)

### `GET /issues/{id}/`

Retrieve a single issue.

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `id` | uuid | Issue UUID |

**Responses**

- `200` → [`Issue`](#issue)

---

## Projects

> `GET /projects/` — list ordered by `name ASC`, hides soft-deleted, optional `?team=<uuid>` filter.
> `GET /projects/{pk}/` — detail (pure PK).
> `POST /projects/` — create `{team, name, visibility?}`.
> `PATCH /projects/{pk}/` — partial update.
> `DELETE` — `405 Method Not Allowed`.

### `GET /projects/`

**Query parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `team` | query | uuid | no | Optional filter by team UUID |
| `cursor` | query | string | no | Pagination cursor |

**Responses**

- `200` → [`PaginatedProjectListList`](#paginatedprojectlistlist)

### `POST /projects/`

Create a project.

**Request body** (`application/json`) → [`ProjectCreateUpdate`](#projectcreateupdate)

**Responses**

- `201` → [`ProjectCreateUpdate`](#projectcreateupdate)

### `GET /projects/{id}/`

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `id` | integer | Project id |

**Responses**

- `200` → [`ProjectDetail`](#projectdetail)

### `PATCH /projects/{id}/`

Partial update.

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `id` | integer | Project id |

**Request body** (`application/json`) → [`PatchedProjectCreateUpdate`](#patchedprojectcreateupdate)

**Responses**

- `200` → [`ProjectCreateUpdate`](#projectcreateupdate)

---

## Releases

> List requires `?project=<id>`. Ordered by `sort_epoch`. Create allowed; delete is a TODO.

### `GET /releases/`

**Query parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `project` | query | integer | yes | Filter releases by project id |
| `cursor` | query | string | no | Pagination cursor |

**Responses**

- `200` → [`PaginatedReleaseListList`](#paginatedreleaselistlist)

### `POST /releases/`

Create a release.

**Request body** (`application/json`) → [`ReleaseCreate`](#releasecreate)

**Responses**

- `201` → [`ReleaseCreate`](#releasecreate)

### `GET /releases/{id}/`

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `id` | uuid | Release UUID |

**Responses**

- `200` → [`ReleaseDetail`](#releasedetail)

---

## Teams

> `GET /teams/` — list ordered by `name ASC`.
> `GET /teams/{pk}/` — detail (pure PK).
> `POST /teams/` — create `{name, visibility?}`.
> `PATCH /teams/{pk}/` — partial update.
> `DELETE` — `405 Method Not Allowed`.

### `GET /teams/`

**Query parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `cursor` | query | string | no | Pagination cursor |

**Responses**

- `200` → [`PaginatedTeamListList`](#paginatedteamlistlist)

### `POST /teams/`

Create a team.

**Request body** (`application/json`) → [`TeamCreateUpdate`](#teamcreateupdate)

**Responses**

- `201` → [`TeamCreateUpdate`](#teamcreateupdate)

### `GET /teams/{id}/`

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `id` | uuid | Team UUID |

**Responses**

- `200` → [`TeamDetail`](#teamdetail)

### `PATCH /teams/{id}/`

Partial update.

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `id` | uuid | Team UUID |

**Request body** (`application/json`) → [`PatchedTeamCreateUpdate`](#patchedteamcreateupdate)

**Responses**

- `200` → [`TeamCreateUpdate`](#teamcreateupdate)

---

## Schemas

Required fields are marked **(req)**. `readOnly` fields are returned by the server but not accepted in writes.

### `EventDetail`

> Detail view: includes full `data` payload.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | readOnly, Bugsink-internal **(req)** |
| `event_id` | uuid | readOnly, as per the sent data **(req)** |
| `issue` | uuid | **(req)** |
| `project` | integer | **(req)** |
| `grouping` | integer | **(req)** |
| `digest_order` | int64 (0 … 9223372036854775807) | **(req)** |
| `timestamp` | date-time | **(req)** |
| `ingested_at` | date-time | **(req)** |
| `digested_at` | date-time | **(req)** |
| `data` | object (free-form) | readOnly **(req)** |
| `stacktrace_md` | string | readOnly **(req)** |

### `EventList`

> Lightweight list view: excludes the (potentially large) `data` field.

Same as `EventDetail` minus `data` and `stacktrace_md`.

### `Issue`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | readOnly **(req)** |
| `project` | integer | **(req)** |
| `digest_order` | int64 | **(req)** |
| `first_seen` | date-time | **(req)** |
| `last_seen` | date-time | **(req)** |
| `digested_event_count` | int64 | **(req)** |
| `stored_event_count` | integer | readOnly **(req)** |
| `calculated_type` | string (≤128) | |
| `calculated_value` | string (≤1024) | |
| `transaction` | string (≤200) | |
| `is_resolved` | boolean | |
| `is_resolved_by_next_release` | boolean | |
| `is_muted` | boolean | |

### `ProjectDetail`

| Field | Type | Notes |
|---|---|---|
| `id` | integer | readOnly **(req)** |
| `team` | uuid \| null | |
| `name` | string (≤255) | **(req)** |
| `slug` | string (≤50, `^[-a-zA-Z0-9_]+$`) | **(req)** |
| `dsn` | string | readOnly **(req)** |
| `digested_event_count` | integer | readOnly **(req)** |
| `stored_event_count` | integer | readOnly **(req)** |
| `alert_on_new_issue` | boolean | |
| `alert_on_regression` | boolean | |
| `alert_on_unmute` | boolean | |
| `visibility` | [`ProjectVisibilityEnum`](#projectvisibilityenum) | **(req)** |
| `retention_max_event_count` | int64 | |

### `ProjectList`

Identical shape to `ProjectDetail`.

### `ProjectCreateUpdate`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | readOnly **(req)** |
| `team` | uuid | **(req)** |
| `name` | string (≤255) | **(req)** |
| `visibility` | [`ProjectVisibilityEnum`](#projectvisibilityenum) | |
| `alert_on_new_issue` | boolean | |
| `alert_on_regression` | boolean | |
| `alert_on_unmute` | boolean | |
| `retention_max_event_count` | int64 | |

### `PatchedProjectCreateUpdate`

Same as `ProjectCreateUpdate` but **all fields optional** (PATCH semantics).

### `ProjectVisibilityEnum`

`joinable` · `discoverable` · `team_members`

### `ReleaseCreate`

| Field | Type | Notes |
|---|---|---|
| `project` | integer | **(req)** |
| `version` | string | **(req)** |
| `timestamp` | date-time | optional; defaults to server time |

### `ReleaseDetail`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | readOnly **(req)** |
| `project` | integer | **(req)** |
| `version` | string (≤250) | **(req)** |
| `date_released` | date-time | |
| `semver` | string | readOnly **(req)** |
| `is_semver` | boolean | readOnly **(req)** |
| `sort_epoch` | integer | readOnly **(req)** |

### `ReleaseList`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | readOnly **(req)** |
| `project` | integer | **(req)** |
| `version` | string (≤250) | **(req)** |
| `date_released` | date-time | |

### `TeamCreateUpdate`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | readOnly **(req)** |
| `name` | string (≤255) | **(req)** |
| `visibility` | [`TeamVisibilityEnum`](#teamvisibilityenum) | |

### `PatchedTeamCreateUpdate`

Same as `TeamCreateUpdate` but **all fields optional**.

### `TeamDetail`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | readOnly **(req)** |
| `name` | string (≤255) | **(req)** |
| `visibility` | [`TeamVisibilityEnum`](#teamvisibilityenum) | **(req)** |

### `TeamList`

Same shape as `TeamDetail`.

### `TeamVisibilityEnum`

`joinable` · `discoverable` · `hidden`

### Pagination wrappers

All list responses share the same envelope:

```json
{
  "next": "https://bugreport.uk/...?cursor=cD00ODY%3D",
  "previous": null,
  "results": [ /* ...items... */ ]
}
```

#### `PaginatedEventListList`

`results: EventList[]`

#### `PaginatedIssueList`

`results: Issue[]`

#### `PaginatedProjectListList`

`results: ProjectList[]`

#### `PaginatedReleaseListList`

`results: ReleaseList[]`

#### `PaginatedTeamListList`

`results: TeamList[]`

---

## Authentication

```
Authorization: Bearer <token>
```

OpenAPI security scheme:

```yaml
securitySchemes:
  BearerAuth:
    type: http
    scheme: bearer
    bearerFormat: token
```

Generate a token via the Bugsink web UI (Settings → API Tokens) or the management command:

```bash
bugsink-manage create_auth_token
```

---

## Quick `curl` examples

```bash
export BUGSINK_URL="https://bugreport.uk"
export BUGSINK_TOKEN="<your-token>"

# List projects
curl -H "Authorization: Bearer $BUGSINK_TOKEN" \
  "$BUGSINK_URL/api/canonical/0/projects/"

# List issues for project 1, newest first
curl -H "Authorization: Bearer $BUGSINK_TOKEN" \
  "$BUGSINK_URL/api/canonical/0/issues/?project=1&sort=last_seen&order=desc"

# List events for an issue
curl -H "Authorization: Bearer $BUGSINK_TOKEN" \
  "$BUGSINK_URL/api/canonical/0/events/?issue=<issue-uuid>&order=desc"

# Get a single event with full data
curl -H "Authorization: Bearer $BUGSINK_TOKEN" \
  "$BUGSINK_URL/api/canonical/0/events/<event-uuid>/"

# Get an event's stacktrace as Markdown
curl -H "Authorization: Bearer $BUGSINK_TOKEN" \
  "$BUGSINK_URL/api/canonical/0/events/<event-uuid>/stacktrace/"

# Create a release
curl -X POST -H "Authorization: Bearer $BUGSINK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project": 1, "version": "1.2.3"}' \
  "$BUGSINK_URL/api/canonical/0/releases/"
```
