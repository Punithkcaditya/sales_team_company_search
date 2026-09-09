# Company Research Tool

A sales rep types a company name. An AI agent searches the live web, and a
structured pre-meeting briefing streams onto the screen section by section.
Past briefings are saved, browsable, and deletable.

```
backend/    FastAPI + SQLite + the research agent
frontend/   React + TypeScript (Vite)
```

---

## Running it

Two commands. **Neither requires an API key** — without one the app runs in demo
mode and still works end to end (see [Demo mode](#demo-mode)).

**1. Install**

```bash
./setup.sh
```

<details>
<summary>Windows PowerShell</summary>

```powershell
.\setup.ps1
```
</details>

<details>
<summary>Or by hand</summary>

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cd ../frontend && npm install
```
</details>

**2. Run**

```bash
./dev.sh
```

<details>
<summary>Windows PowerShell</summary>

```powershell
.\dev.ps1
```
</details>

Then open <http://localhost:5173>. The backend runs on port 8000; Vite proxies
`/api` to it, so the browser only ever talks to one origin.

**Tests**

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm test
```

Requires Python 3.11+ and Node 18+.

---

## API keys

```bash
cp backend/.env.example backend/.env
```

Then set **one** key:

| Variable | Where to get it | Notes |
| --- | --- | --- |
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | Free tier. Google Search is built into the model, so this is the only key needed. |

An Anthropic implementation is also included and needs two keys
(`ANTHROPIC_API_KEY` + `SERPER_API_KEY`), since Claude has no built-in search.
`LLM_PROVIDER` pins a provider; left at `auto` it uses whichever keys are
present, preferring Gemini.

Keys are read from the environment or `backend/.env` (git-ignored) and never
committed. `GET /api/health` reports the active provider, so you can always tell
what you are running:

```json
{"status": "ok", "mode": "live", "provider": "gemini"}
```

### Cost

Demo mode makes no model or search API calls. Live research is subject to your
provider's billing tier and quotas. A completed Gemini briefing normally uses
six model requests: one grounded research request and five writing requests.
Free Google Search allowance alone does not guarantee a number of free briefings:
request and token limits also apply. A paid-tier key can incur token charges even
when grounding is within its free allowance. See [Google's current pricing](https://ai.google.dev/gemini-api/docs/pricing)
and verify your project's billing tier in AI Studio. Google may use free-tier
request data to improve its products.

### Usage and navigation

The status panel labels demo/live mode and shows the **app's shared daily research
allowance**, not the provider's remaining tokens. `GET /api/usage` returns this
status; `provider_tokens_remaining` is always `null` because this app cannot
observe all project-wide provider usage or calculate an exact remaining balance.

The default allowance is **20 live research attempts per UTC day**, shared by all
visitors. Override it in `backend/.env` or the backend's environment:

```dotenv
DAILY_RESEARCH_LIMIT=20
```

Set `DAILY_RESEARCH_LIMIT=0` to disable only the app limit. Demo searches never
consume it. Live attempts are counted before research starts, including failed
or cancelled attempts, because those can already have used provider resources.
Invalid input and duplicate concurrent requests do not consume it. SQLite stores
the counter atomically, so restarting the backend or deleting reports does not
restore the allowance. It resets at 00:00 UTC; the UI shows that time in the
visitor's timezone and refreshes availability every minute, on focus, and after
research. Gemini's own daily reset and other limits are independent.

At zero app allowance, the server rejects new research with HTTP 429 and code
`daily_limit`. Provider quota errors use SSE code `quota_exceeded` and stop further
section requests, including when the quota is hit halfway through a report.
Saved briefings remain available. This attempt cap is not a money budget or a
guarantee of free provider usage.

**Back to search** returns to the starting view, clears and focuses the search
box, and cancels an active stream. Saved reports stay in history.

### Sharing a deployed link

The React frontend can be hosted on Vercel, with `frontend` as the project root,
`npm run build` as the build command, and `dist` as the output directory. Set
`VITE_API_BASE` to the HTTPS origin of your deployed backend before building.
The local Vite `/api` proxy is a development setting; it does not exist in the
production build. Set the backend's `CORS_ORIGINS` to include the exact frontend
origin. Keep provider API keys only in the backend's environment, never in any
`VITE_*` variable.

The Python backend and its SQLite file need a host with persistent disk. A local
SQLite database on Vercel Functions is not durable or shared between instances;
moving the file to `/tmp` would not preserve report history or the usage counter.
See [Vercel's SQLite guidance](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel).
Keep SQLite on a persistent backend host to preserve the assignment's database
requirement. Hosting costs depend on the selected provider and plan.

This app has no authentication: everyone with access to the backend shares the
allowance and can read or delete saved briefings. Share only suitable demo data.
No deployment is created by the local setup scripts.

### Demo mode

With no key set, `build_agent` returns `DemoResearchAgent` — the same protocol,
the same event stream, the same pacing, but canned content and no network calls.
Every UI state (searching, per-item streaming, unresearchable company) is
reachable without credentials.

**This is a provider swap, not a code path.** Both real agents
(`agent/gemini_agent.py`, `agent/anthropic_agent.py`) are written as production
code against their real APIs, and `tests/test_gemini_agent.py` and
`tests/test_anthropic_agent.py` exercise them against faked SDKs — the Gemini
tests use the SDK's own event classes, so a change in the provider's event shapes
fails the suite rather than production.

---

## Choices

**LLM — Gemini 2.5 Flash, via the `google-genai` SDK's Interactions API.** The
deciding factor was that Google Search is a first-class built-in tool on the
model. That collapses two integrations into one: no second search vendor, no
second key, no separate rate limit to reason about — and the model's chosen
queries and its source citations both come back through the same stream, which
is exactly what the UI needs to show progress and cite sources. Flash is the
right tier here because the hard judgment is *what to search for*, not prose
generation, and a rep is waiting on the result.

**Search — Gemini's built-in Google Search grounding.** Real Google results,
declared as a tool on the request (`tools=[{"type": "google_search"}]`). The
model issues the queries; each arrives mid-stream as a `google_search_call`
delta and is forwarded to the browser, so the rep watches the actual research
happen rather than a spinner. Citations come back as `url_citation` annotations
and become the report's source list.

**Database — SQLite.** Reports store sections as a JSON column, with a separate
small table for the shared daily allowance. Report sections are
always read and written as a whole document and never queried field by field, so
five normalised tables would buy nothing but joins.

**A second provider is included.** `agent/anthropic_agent.py` implements the same
`ResearchAgent` protocol with Claude plus Serper for search. It is not required
to run the app — it exists because the provider seam is the one place I wanted to
prove was real rather than asserted, and because the two providers solve search
in genuinely different ways (built-in tool vs. an external search API behind a
function call).

---

## How the agent works

Two phases, deliberately separated (`backend/app/agent/`):

**1. Gather** — one grounded call. The model runs Google Search itself and
returns a prompted JSON digest, validated locally: the company's canonical name, a
`researchable` flag, and dense factual findings. That flag is how gibberish input
gets an honest "we couldn't find that" instead of a hallucinated briefing, and an
empty findings set is treated the same way — a confident digest with no evidence
behind it never becomes a briefing.

*(The Anthropic implementation does the same job as an explicit tool loop:
`web_search` and `finish_research` as function declarations, searches in a turn
run concurrently and return in one user message, and turn counts are capped.)*

**2. Write** — one call per section, in the order a rep prepares, from the
evidence gathered in phase 1. These calls are **ungrounded**: the evidence is
already in hand, so no further searching happens. One briefing therefore costs
exactly one grounded prompt.

Splitting the phases means a slow search never blocks the first words appearing,
and each writing prompt stays short enough to stay honest.

### Streaming

`POST /api/research` returns `text/event-stream`. Each section streams in the
form that suits it:

| Section | Streams as | Why |
| --- | --- | --- |
| Overview | `section_delta` — text fragments | Prose should appear as it's written |
| Key people, News, Risks | `section_item` — one entry at a time | Bullets land individually (JSON Lines, parsed incrementally) |
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
| `GET` | `/api/health` | Status, mode, and active provider |

Every error response is `{"message": "..."}` — one shape, so the client has one
code path and a user never sees a stack trace. Invalid input is 422, a duplicate
concurrent run is 409.

---

## Edge cases handled

- **Missing section data** — sections render "Nothing found on recent news"
  rather than collapsing; unavailable financial metrics show "Not disclosed"
  rather than a blank or a fabricated number.
- **One section failing** — logged, closed, and the other four still save. Only
  a total provider outage aborts the run.
- **Unresearchable input** — the agent reports it; nothing is saved.
- **Rapid successive searches** — the previous run is aborted before the next
  starts, so two streams never write into one view.
- **Duplicate concurrent research** — `ActiveResearch` refuses a second run for
  the same company (normalised for case and whitespace) with a 409.
- **Unmount / navigate away mid-stream** — the fetch is aborted, which closes the
  SSE response and tears the server-side run down. Nothing is saved.
- **Backend unreachable** — "Can't reach the server. Is the backend running?"
- **Stream dies mid-run** — detected client-side, with a "Try again" button.
- **Malformed model output** — a bad JSON line is dropped, not fatal. Losing one
  bullet beats losing the report.

Also built: cancel, a per-section "researching" indicator, ⌘/Ctrl+K to focus
search, relative timestamps, and a responsive layout.

---

## Trade-offs

- **Two phases means two round trips before writing starts.** Search results
  arrive first, so the rep sees real progress (the actual queries) within a
  second or two, and the writing prompts stay small and grounded. A single
  mega-prompt would start writing sooner but hallucinate more.
- **Sections are written sequentially, not in parallel.** Running all five at
  once would be faster wall-clock, but the report would fill in unpredictably and
  the token spend would spike. Sequential matches how a rep reads.
- **Sections are stored as a JSON blob.** Fine for reading whole reports; if this
  ever needed "show me every company with a CISO", it would need normalising.
- **`ActiveResearch` is in-process.** Correct for one server, which is all SQLite
  supports anyway. Multiple workers would need shared state.
- **No retry or backoff beyond the SDK's own.** A rate-limited run surfaces a
  readable message and the rep retries.
- **Grounding hides the raw search results.** With Gemini the model reads the
  results and hands back written findings, so the app never sees the raw snippets
  the way it would with an external search API. Simpler and cheaper, but it means
  trusting the model's summarisation — which is why the digest is
  validated locally and an empty findings set is treated as "not found".
- **Optimistic delete.** The row disappears immediately and comes back if the
  server disagrees — the right call for an action a rep does casually.
- **Plain CSS, no component library.** Faster than configuring one for a
  five-component app, and nothing to fight over specificity.

## With more time

- **Cache briefings per company** for a short TTL. Two reps prepping for the same
  account today each spend a grounded request right now.
- **Per-section regeneration** — "this news section is thin, dig deeper" without
  re-running everything.
- **Streaming financials via partial-JSON parsing**, so the figures fill in like
  the rest instead of appearing whole.
- **Freshness signals in the UI** — flag when the newest source is six months old,
  so a rep knows the briefing is thin rather than assuming nothing happened.
- **Evals on the agent.** Right now correctness is judged by reading output. A
  small graded set (does it find the real CEO, does it avoid inventing a market
  cap for a private company) would let the prompts be tuned with evidence.
- **A cheaper model for the writing phase**, measured against those evals rather
  than assumed.
- **Contract tests between the SSE event types and the TypeScript union**, which
  are hand-mirrored today.

---

## Packaging for submission

```bash
zip -r company-research.zip . \
  -x '*/node_modules/*' '*/__pycache__/*' '*/.venv/*' '*/dist/*' \
     '*/.git/*' '*.db' '*/.env'
```
