# Company Research Tool

A sales rep types a company name. An AI agent researches it with live Google
search, and a structured pre-meeting briefing streams onto the screen section by
section. Past briefings are saved, browsable, and deletable.

```
backend/    FastAPI + SQLite + the research agent
frontend/   React + TypeScript (Vite)
```

Requires **Python 3.11+** and **Node 18+**.

---

## Quick start

```bash
./setup.sh      # installs backend and frontend dependencies
./dev.sh        # runs the API on :8000 and the UI on :5173
```

<details>
<summary>Windows PowerShell</summary>

```powershell
.\setup.ps1
.\dev.ps1
```
</details>

<details>
<summary>Or step by step</summary>

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt      # Windows: .venv\Scripts\pip
cd ../frontend
npm install
```

Then, in two terminals:

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000 --reload
cd frontend && npm run dev
```
</details>

Open <http://localhost:5173>. Vite proxies `/api` to the backend, so the browser
only ever talks to one origin.

**The app runs with no API keys at all.** Without them it starts in demo mode:
canned research, no network calls, but every screen and state is reachable. Add
keys (below) to do real research.

### Tests

```bash
cd backend && .venv/bin/python -m pytest      # 82 tests
cd frontend && npm test                       # 45 tests
```

---

## API keys

Two keys, both free, neither needs a credit card.

```bash
cp backend/.env.example backend/.env
```

### 1. `GEMINI_API_KEY` — the model

1. Go to <https://aistudio.google.com/apikey>
2. Sign in with any Google account
3. **Create API key** → choose or create a project
4. Paste it into `backend/.env`

Free tier, no billing account required. Leaving the free tier means explicitly
enabling billing, so an unattended key cannot run up a bill — it returns `429`
instead. Limits are metered **per project, per minute** (roughly 20
requests/minute on the free tier) and reset within the minute.

### 2. `SERPER_API_KEY` — Google search results

1. Go to <https://serper.dev/api-key>
2. Sign in with Google
3. Paste it into `backend/.env`

2,500 free queries on signup, no card required.

### Checking it worked

```bash
curl http://localhost:8000/api/health
```

`{"provider": "gemini"}` means live research. `{"provider": "demo"}` means a key
is missing — the server log says which. `LLM_PROVIDER=demo` forces demo mode
even with keys present.

### Two limits, and which one you hit

The banner above the search box shows this app's **own** daily limit, default 50
briefings, resetting at 00:00 UTC. That is a deliberate guard, not a bug and not
a spending cap — the free tier cannot be billed. It exists so a day of clicking
cannot quietly exhaust the provider allowance. `DAILY_RESEARCH_LIMIT=0` removes
it.

Separately, Gemini's free tier is rate limited **per minute** (roughly 20
requests per project). A briefing costs about four, so several in quick
succession can hit it even with app allowance to spare. The agent reads the
cooldown the API returns and waits it out; if it is genuinely exhausted the UI
says so and the run is not saved.

---

## Choices

**Model — Gemini, via the `google-genai` Interactions API.** Two models, because
the two phases are different jobs: `gemini-flash-latest` runs the research loop,
where the model decides what to search for, and `gemini-flash-lite-latest`
writes the briefing from evidence already gathered. That second job is shallow
work and the rep is waiting, so it gets the faster model. Quota is also metered
per model, so the two phases draw on separate allowances.

**Search — Serper (Google results as JSON).** Gemini has a built-in
`google_search` tool, which would have meant one key instead of two — but it has
no free-tier quota, so a free key is refused with `429` the moment it is used.
Serper returns real Google SERPs (organic, news, knowledge panel) over one REST
call, and its news block carries dates, which is what makes "recent news"
actually recent. Running search client-side also makes the agent legible: you
can read the loop rather than trust a black box.

**Database — SQLite, one table.** Sections are stored as a JSON column. They are
always read and written as a whole document and never queried field by field, so
five normalised tables would buy nothing but joins.

---

## How the agent works

Two phases (`backend/app/agent/`):

**1. Gather** — an agentic tool loop. The model gets two tools, `web_search` and
`finish_research`, and decides what to look for. Searches issued in one turn run
concurrently and come back in a single message. The loop ends when the model
calls `finish_research`, which also returns the company's canonical name and a
`researchable` flag. That flag is how gibberish gets an honest "we couldn't find
that" instead of a hallucinated briefing, and an empty evidence set is treated
the same way. Turn and search counts are capped so a confused model cannot loop
indefinitely.

**2. Write** — one request produces all five sections, marked up so they can be
split apart as they stream. Five separate calls meant five round trips with the
whole search corpus re-sent each time; this is one round trip and one copy.

### Streaming

`POST /api/research` returns `text/event-stream`. Each section streams in the
form that suits it:

| Section | Streams as | Why |
| --- | --- | --- |
| Overview | `section_delta` — text fragments | Prose should appear as it is written |
| Key people, News, Risks | `section_item` — one entry at a time | Bullets land individually |
| Financials | `section_end` — one validated object | A half-rendered revenue figure is worse than a late one |

Event vocabulary: `status`, `search`, `section_start`, `section_delta`,
`section_item`, `section_end`, `done`, `error`. The client ignores names it does
not recognise, so adding an event never breaks an older frontend.

`EventSource` only does GET, and a company name belongs in a body rather than a
URL, so the frontend reads the response stream and parses SSE itself
(`frontend/src/api/stream.ts`). That also makes cancelling research just an
`AbortController.abort()`.

### REST

| Method | Path | |
| --- | --- | --- |
| `POST` | `/api/research` | SSE stream; saves on completion |
| `GET` | `/api/reports` | Summaries, newest first |
| `GET` | `/api/reports/{id}` | Full report (404 if gone) |
| `DELETE` | `/api/reports/{id}` | 204, or 404 |
| `GET` | `/api/health` | Status and active provider |

Every error response is `{"message": "..."}` — one shape, so the client has one
code path and a user never sees a stack trace. Invalid input is 422, a duplicate
concurrent run is 409.

---

## Edge cases handled

- **Missing section data** — sections say "Nothing found on recent news" rather
  than collapsing; unavailable financial metrics show "Not disclosed" rather
  than a blank or a fabricated number.
- **Unresearchable input** — the agent reports it; nothing is saved.
- **Rate limits** — the free tier meters per minute and states its own cooldown,
  which the agent waits out rather than failing. If it is genuinely exhausted,
  the rep is told it resets within a minute, not shown a stack trace.
- **Partial failure while writing** — sections already written are kept and
  closed, so the UI never leaves a section spinning.
- **Malformed model output** — a bad JSON line is dropped, not fatal. Losing one
  bullet beats losing the report.
- **Rapid successive searches** — the previous run is aborted before the next
  starts, so two streams never write into one view.
- **Duplicate concurrent research** — a second run for the same company
  (normalised for case and whitespace) is refused with a 409.
- **Unmount / navigate away mid-stream** — the fetch is aborted, which closes the
  SSE response and tears the server-side run down. Nothing is saved.
- **Backend unreachable** — "Can't reach the server. Is the backend running?"
- **Stream dies mid-run** — detected client-side, with a "Try again" button.
- **Half-configured install** — a pinned provider with no search key falls back
  to demo mode and says so, rather than reporting every company as "not found".

Also built: cancel, a per-section "researching" indicator, ⌘/Ctrl+K to focus
search, relative timestamps, and a responsive layout.

---

## Trade-offs

- **Two phases means a round trip before writing starts.** Search results come
  first, so the rep sees the actual queries within a second or two and the
  writing prompt stays small and grounded. One mega-prompt would start writing
  sooner and hallucinate more.
- **All five sections in one request.** Cheaper and faster, but a failure
  partway truncates the briefing rather than costing a single section. The
  pipeline keeps and closes whatever arrived before the failure.
- **A faster model writes the briefing.** Lower latency and a separate quota, at
  some cost in prose quality. `GEMINI_WRITER_MODEL` reverts it to one model.
- **Sections stored as a JSON blob.** Fine for reading whole reports; a query
  like "every company with a CISO" would need normalising.
- **In-process duplicate-run guard.** Correct for one server, which is all
  SQLite supports anyway. Multiple workers would need shared state.
- **Optimistic delete.** The row disappears immediately and comes back if the
  server disagrees — the right call for an action a rep does casually.
- **Plain CSS, no component library.** Faster than configuring one for a
  five-component app, and nothing to fight over specificity.

## With more time

- **Cache briefings per company** for a short TTL. Two reps prepping the same
  account today each pay for the research.
- **Per-section regeneration** — "this news section is thin, dig deeper" without
  re-running everything.
- **Freshness signals in the UI** — flag when the newest source is six months
  old, so a rep knows a briefing is thin rather than assuming nothing happened.
- **Evals on the agent.** Correctness is judged by reading output today. A small
  graded set (does it find the real CEO, does it avoid inventing a market cap for
  a private company) would let the prompts be tuned on evidence rather than
  taste.
- **Contract tests between the SSE event types and the TypeScript union**, which
  are hand-mirrored today.
- **Streaming financials via partial-JSON parsing**, so the figures fill in like
  the rest instead of appearing whole.
