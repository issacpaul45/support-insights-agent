# Support Insights Agent

A Django REST API that answers questions about synthetic support tickets with OpenAI function calling and PostgreSQL.

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

The ticket description is treated as untrusted data. The API avoids returning customer email addresses. OpenAI credentials stay on the server and should never be put in Postman.
