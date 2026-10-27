# Support Insights Agent

A Django REST API and a local MCP server for querying synthetic support tickets, backed by PostgreSQL. The existing REST/OpenAI agent and the MCP server are separate entry points that share the same Django service layer.

## Run locally

1. Create a virtual environment and install dependencies:

   ```sh
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Start PostgreSQL and configure the app:

   ```sh
   docker compose up -d db
   cp .env.example .env
   ```

   Replace `DJANGO_SECRET_KEY` and put your OpenAI API key in `.env` as `OPENAI_API_KEY`. Generate a Django key with `python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'`. The default model is `gpt-6-luna`; set `OPENAI_MODEL` to another Responses API model if needed.

3. Create the schema, seed synthetic records, and run Django:

   ```sh
   python manage.py migrate
   python manage.py seed_tickets
   python manage.py runserver
   ```

The API is available at `http://127.0.0.1:8000/api/`. The agent accepts `POST /api/agent/` with a JSON body containing a `question` string. The importable Postman collection is in `postman/Support Insights Agent.postman_collection.json`.

## MCP server

[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) is a standard way for AI hosts to discover and invoke tools exposed by a server. This project uses the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) to expose ticket operations over the real MCP protocol. This is separate from the OpenAI function-calling flow used by `POST /api/agent/`.

The server uses local **stdio** transport: an MCP host starts it as a subprocess and exchanges MCP messages over standard input/output. The request path is:

`MCP host → stdio MCP protocol → tickets/mcp_server.py (Django setup) → tickets/mcp_tools.py → tickets/services.py → Django ORM/PostgreSQL`

The REST API and its OpenAI agent keep running independently. The MCP tools reuse `tickets/services.py`; they do not add another database query implementation or call a model provider.

### Install and start

Use Python 3.11 or newer. The MCP dependency is constrained to the SDK's 2.x major version in `requirements.txt`.

```sh
source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d db
python manage.py migrate
python manage.py seed_tickets
```

Configure `DJANGO_SECRET_KEY` and the `DB_*` variables in `.env` as described above. The MCP server needs those Django/database settings. It does not need `OPENAI_API_KEY`; that key is used by the existing REST agent only.

An MCP client normally launches the server for you. To inspect it interactively with the official MCP Inspector, run this from the project root:

```sh
npx -y @modelcontextprotocol/inspector .venv/bin/python -m tickets.mcp_server
```

In Inspector, connect to the stdio server, open **Tools**, select a tool to view its schema, and invoke it with JSON arguments. For example, `get_ticket_summary` can be called with `{"category":"payment"}`. On success, inspect the structured JSON result; on failure, check the tool result's `isError` flag and safe message. The Inspector runs outside this Python dependency set and requires Node.js/npm.

### Available tools

| Tool | Arguments | Result |
| --- | --- | --- |
| `search_tickets` | `category` (optional: `payment`, `login`, `billing`, `other`); `status` (optional: `open`, `in_progress`, `resolved`); `limit` (optional, 1–10; default 10) | Object with `tickets`; each item has `id`, `title`, `description`, `category`, and `status`. |
| `get_ticket_summary` | `category` (optional category enum) | Object with `total_tickets`, `unresolved_tickets`, `by_status`, `by_category`, and `unresolved_by_category`. |
| `get_common_unresolved_causes` | `category` (optional category enum); `limit` (optional, 1–5; default 5) | Object with `common_causes`; each item has `title`, `ticket_count`, and representative `ticket_ids`. |

All tools return structured JSON. These example results match a fresh database after `seed_tickets`; counts and IDs vary if the database contains different records.

`search_tickets`:

```json
{
  "tickets": [
    {
      "id": 100,
      "title": "Payment pending after charge",
      "description": "Payment is charged but order remains pending.",
      "category": "payment",
      "status": "open"
    }
  ]
}
```

`get_ticket_summary`:

```json
{
  "total_tickets": 100,
  "unresolved_tickets": 67,
  "by_status": [
    {"status": "open", "count": 44},
    {"status": "in_progress", "count": 23},
    {"status": "resolved", "count": 33}
  ],
  "by_category": [
    {"category": "payment", "count": 30},
    {"category": "login", "count": 26},
    {"category": "billing", "count": 30},
    {"category": "other", "count": 14}
  ],
  "unresolved_by_category": [
    {"category": "payment", "count": 22},
    {"category": "login", "count": 14},
    {"category": "billing", "count": 21},
    {"category": "other", "count": 10}
  ]
}
```

`get_common_unresolved_causes`:

```json
{
  "common_causes": [
    {
      "title": "Payment pending after charge",
      "ticket_count": 12,
      "ticket_ids": [100, 92, 90]
    }
  ]
}
```

An empty search returns `{"tickets":[]}`, an empty cause query returns `{"common_causes":[]}`, and summaries preserve zero counts. Invalid enum values, extra arguments, or limits outside the documented range return an MCP tool error. Database failures return a generic retry message and do not include SQL or credentials.

Repeated titles are a proxy for common causes, not verified root causes. `ticket_ids` in that result are representative examples, up to three per repeated title; they are not all tickets included in the count. Ticket descriptions are untrusted text. Results never include customer email addresses, timestamps, or server credentials.

### Client configuration

Add a server entry to your MCP client's configuration, replacing the placeholders with local values. Keep this configuration private because the database password and Django secret key are supplied to the child process through its environment.

```json
{
  "mcpServers": {
    "support-insights": {
      "command": "/absolute/path/to/support-insights-agent/.venv/bin/python",
      "args": ["-m", "tickets.mcp_server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/support-insights-agent",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_SECRET_KEY": "<local-django-secret>",
        "DB_NAME": "support_insights",
        "DB_USER": "support_agent",
        "DB_PASSWORD": "<local-postgres-password>",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432"
      }
    }
  }
}
```

This example does not include an OpenAI API key because MCP tool calls go directly to the Django services. MCP stdio is intended for a local trusted host. A remote deployment should use the SDK's Streamable HTTP transport behind TLS and an authentication/authorization layer; this project does not currently provide a remote MCP endpoint.

### Tests and evaluations

Run deterministic Django and MCP tests with:

```sh
python manage.py test
```

The suite checks tool discovery and schemas, service-backed results, validation, empty data, email redaction, safe failure responses, a real MCP SDK client over stdio, and the existing REST/OpenAI-agent paths. Tests do not call the paid model API.

The 13 natural-language cases in `tickets/evaluation_cases.json` define expected tool choices and database-grounded checks for counts, category rankings, repeated titles, cited IDs, and search behavior. To compare the live OpenAI agent against them, run:

```sh
python manage.py evaluate_agent --live --max-latency-ms 30000
```

This opt-in command makes up to 13 live agent evaluations (normally two Responses API requests per case), reports request and answer failure rates plus median/p95 latency, and exits nonzero on a failed check. It is separate from the normal test suite and can incur API charges. The empty-search evaluation case is skipped when the configured database contains matching rows; deterministic tests cover empty results with an isolated fixture.

The evaluation checks are heuristic because the agent returns natural language. Review failed case output and the underlying tool data before treating a score as a production guarantee.

## Postman requests and example responses

Import `postman/Support Insights Agent.postman_collection.json` into Postman, start the Django server, then send a request to `POST {{baseUrl}}/api/agent/`. The collection sets `baseUrl` to `http://127.0.0.1:8000`; use `Content-Type: application/json` and put the request body in **Body → raw → JSON**. The OpenAI API key stays in the server's `.env` file and is not sent from Postman.

Try these request bodies:

**Count unresolved tickets**

```json
{
  "question": "How many tickets are unresolved?"
}
```

**Compare categories**

```json
{
  "question": "Which support category has the most unresolved tickets? Compare all categories and mention ties."
}
```

**Find individual tickets**

```json
{
  "question": "Find up to five unresolved payment tickets. Give each ticket's ID in the format #123 and include its title and status."
}
```

An aggregate question returns no ticket sources because it uses counts rather than individual records. Example response (the counts and latency depend on your seeded database and model):

```json
{
  "question": "Which category has the most unresolved tickets?",
  "answer": "Payment has the most unresolved tickets, with 28.",
  "sources": [],
  "tools_used": [
    "ticket_summary_tool"
  ],
  "model": "gpt-6-luna",
  "latency_ms": 4161
}
```

For a question about individual tickets, the response includes the source IDs used in the answer. This example shows the response shape; ticket IDs, titles, statuses, and latency vary with your database and model:

```json
{
  "question": "Find up to five unresolved payment tickets. Give each ticket's ID in the format #123 and include its title and status.",
  "answer": "#17 — Payment failed at checkout (open)\n#42 — Payment pending after charge (in_progress)",
  "sources": [
    17,
    42
  ],
  "tools_used": [
    "search_ticket_tool"
  ],
  "model": "gpt-6-luna",
  "latency_ms": 3200
}
```

## Reliability and latency choices

- OpenAI's Responses API uses strict tool schemas and limits an answer to one tool-selection turn and one result turn.
- Relevant tool calls can be selected together, so a question that needs both totals and common causes does not require serial tool-selection turns.
- The OpenAI client and its HTTP connection pool are reused. Requests have a configurable timeout and one SDK retry for transient errors.
- Ticket totals and grouped counts come from one grouped database query. Aggregate summaries do not fetch or cite example tickets. Recent-ticket queries use database indexes and only load fields needed by the agent.
- Common-cause results include exact counts plus up to three real example ticket IDs per title. The API returns source IDs separately, appends representative verified IDs if the model omits citations, and rejects citations not returned by tools.
- The billable agent endpoint has a configurable rate limit. Set `AGENT_RATE_LIMIT` to tune it for your deployment.

Ticket text is untrusted. MCP results omit the `customer_email` field and redact email addresses and common credential/token formats from returned ticket text. This demo is intended for synthetic data; do not point it at production ticket text without adding an organization-specific data-loss prevention policy. The MCP server does not return its configuration or credentials. The OpenAI API key stays on the server and should never be put in Postman.
