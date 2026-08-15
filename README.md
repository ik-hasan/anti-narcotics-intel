# Narco-Graph Intel

Graph-RAG anti-narcotics intelligence platform with adaptive OSINT crawling.

Public narcotics reporting is scattered across news sites, press notes and portals,
and keyword search cannot show how people, drugs, places and cases connect. This
system ingests that text into a Neo4j knowledge graph, answers questions with
vector retrieval plus constrained graph traversal, flags suspicious network
patterns, and — when it does not have enough confident evidence — searches and
crawls the public web to fill the gap before answering again.

**Team:** The Conflicters — Kamal Kumar, Waseem Akram, Abhishek Kumar, Ikramul Hasan, Deepika Bhagat

---

## How it works

```
             ┌──────────────────────────────────────────┐
   query ───▶│ 1. LLM: intent + entity constraints     │
             │ 2. Voyage: embed query                   │
             │ 3. Neo4j vector search, Top-K = 100      │
             │ 4. Gate: ≥10 chunks scoring ≥ 0.80 ?     │
             └──────────────┬───────────────┬───────────┘
                       YES  │               │  NO
                            ▼               ▼
        Mode A — Retrieve                Mode B — Discover
        constrained 2–3 hop walk         search → filter → crawl
        risk rules                       clean → chunk → embed
        LLM composes Top-N               upsert Neo4j → re-run Mode A
```

Mode B is the part that makes this adaptive: low confidence triggers self-expansion
of the knowledge base instead of a "no results" answer.

## Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI (Python 3.11) | async, small footprint |
| Graph + vectors | Neo4j AuraDB Free | relationships and vector index in one store |
| Embeddings | Voyage `voyage-4-lite` | API-based, no model weights to host |
| LLM | Groq (`llama-3.3-70b-versatile`) | extraction and answer composition |
| Search | SearXNG → DuckDuckGo → Google CSE | redundant discovery layer |
| Crawl | Scrapy (subprocess) → httpx + trafilatura fallback | top 10–15 pages; Scrapy isolated from the API process |
| Extraction | trafilatura | main-content and boilerplate removal |
| Frontend | Next.js + Cytoscape.js | query console and graph view |

### Deliberate omissions

**No spaCy.** `en_core_web_sm` plus the spaCy runtime costs roughly 200 MB resident,
which is 40% of the Render Free budget. LLM structured output plus a regex/lexicon
pre-pass does the same job with no resident model.

**Groq rather than Gemini.** The project started on Gemini but its API project was
blocked (`403 PERMISSION_DENIED`) and could not be recovered. Groq serves Llama over
an HTTP API, so there are still no model weights on the instance — the deployment
footprint is identical. The LLM sits behind `app/services/llm.py`, so the provider is
an env var (`LLM_PROVIDER=groq|gemini`) and not a code change.

**No sentence-transformers.** Same reason — a local embedding model cannot fit
alongside the API on a free instance. Embeddings are an API call.

**Not `voyage-3.5-lite`.** Voyage moved its 200M free-token allowance to the
voyage-4 generation; the 3.x models are billed from the first token.

## Free-tier constraints this design is built around

| Constraint | Value | Consequence |
|---|---|---|
| Render Free RAM | 512 MB | one uvicorn worker, crawler runs as a transient subprocess |
| Render Free CPU | 0.1 | no CPU-heavy work in-process |
| Render background workers | not free | async jobs use `BackgroundTasks` + job state in Neo4j |
| Render idle spin-down | 15 min | ~60 s cold start; frontend shows a warmup state |
| Render free instance hours | 750/mo per workspace | shared across API and SearXNG |
| Aura Free capacity | 200k nodes / 400k rels | ample; chunks dominate the count |
| Aura Free idle | auto-pauses | ping before a demo |
| Voyage without a card | 3 RPM / 10k TPM | batch aggressively, or add a card for 2000 RPM |

## Build phases

- [x] **Phase 0** — scaffold, config, health checks, Render deploy
- [x] **Phase 1** — Neo4j schema, LLM extraction, text ingest, seed corpus
- [x] **Phase 2** — Voyage embeddings, vector index backfill, Mode A retrieval
- [x] **Phase 3** — constrained traversal, risk rules, graph API
- [x] **Phase 4** — search → filter → crawl → clean → embed → upsert (Mode B)
- [x] **Phase 5** — Next.js frontend, Cytoscape graph view, demo polish

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r backend/requirements.txt

cp backend/.env.example backend/.env   # then fill in the keys
```

You need three free accounts: [Neo4j AuraDB](https://console.neo4j.io) (create a Free
instance, save the generated password), [Groq](https://console.groq.com/keys) for the
LLM key, and [Voyage AI](https://dash.voyageai.com) for the embedding key (Phase 2
onward).

Run the API:

```bash
cd backend
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/docs.

Frontend (separate terminal):

```bash
cd frontend
copy .env.example .env.local   # NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
npm install
npm run dev
```

Then open http://localhost:3000.

Local SearXNG (Mode B search). Without it the API falls back to DuckDuckGo:

```bash
docker compose up -d searxng
```

`SEARXNG_URL` in `backend/.env` should be `http://127.0.0.1:8888`.

Load the demo graph:

```bash
cd backend
python -m scripts.seed --dry-run   # lexicon scores only, no API calls
python -m scripts.seed             # full ingest
python -m scripts.backfill_embeddings   # needs VOYAGE_API_KEY
```

Until a Voyage key is set, Mode A falls back to the Neo4j fulltext index. Answers still
use graph expansion; they just rank chunks by keyword instead of cosine similarity.

### Developing against a local Neo4j

### Developing against a local Neo4j

Aura Free allows one instance and pauses when idle. For day-to-day work, point
`NEO4J_URI` at a throwaway container instead:

```bash
docker run -d --name narcograph-neo4j -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/testpassword123 \
  -e NEO4J_server_memory_heap_max__size=512m \
  neo4j:5-community
```

Then set `NEO4J_URI=bolt://localhost:7687` and `NEO4J_PASSWORD=testpassword123`.

### Verifying the graph layer

`scripts/verify_graph.py` exercises every Cypher statement — schema creation, upsert,
idempotency, honorific merging, the fulltext lookup, the constrained walk and both
risk-rule shapes — using a hard-coded extraction, so it costs no Gemini quota:

```bash
cd backend
python -m scripts.verify_graph
```

Run it after any schema change, and once against Aura before a demo.

## Troubleshooting

Diagnostic scripts, all runnable as `python -m scripts.<name>` from `backend/`:

| Script | Use when |
|---|---|
| `verify_graph` | confirming Neo4j schema and all Cypher work |
| `diagnose_tls` | `SSLCertVerificationError` / `self-signed certificate in chain` |
| `list_models` | checking which models the provider's key can see |
| `test_extraction` | finding which model actually works for extraction |
| `probe_llm` | isolating an auth or permission error, and fingerprinting the key |

**`Relative module names not supported`** — `-m` takes a dotted module name. Use
`python -m scripts.seed`, not `python -m .\scripts\seed.py`.

**`self-signed certificate in certificate chain`** — a proxy, campus firewall or
antivirus is intercepting TLS. Run `diagnose_tls` to see who signed the certificate
you actually received. If interception is confirmed and you cannot install the
intercepting CA, `neo4j+ssc://` in `NEO4J_URI` keeps encryption but skips CA
verification. Use that **locally only**, never in Render's environment.

**`403 PERMISSION_DENIED: Your project has been denied access`** — the key
authenticates but its project cannot generate content. Usually a Workspace or
Education account with generative AI disabled by the admin. Create a new key from a
personal Google account.

**`404 ... no longer available to new users`** — the model was retired. Retired
models still appear in `models.list()`, so use `test_extraction` to find a working
one rather than trusting the listing.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness, no I/O — this is Render's health check |
| `GET /health/deep` | Neo4j, LLM, Voyage coverage, search config, graph counts |
| `POST /api/ingest/text` | ingest one article: extract → chunk → upsert → embed |
| `POST /api/ingest/analyze` | lexicon pre-pass only, no LLM, no writes |
| `POST /api/ingest/embed` | backfill Voyage vectors for pending chunks |
| `POST /api/ask` | Graph-RAG answer. Default `discover=true`: if fewer than 10 chunks score ≥ 0.80, SearXNG → Scrapy → chunk → Voyage → Neo4j, then Mode A runs again |
| `GET /api/graph/stats` | node and relationship counts |
| `GET /api/graph/cases` | recent cases, optional `city` / `drug` filters |
| `GET /api/graph/network` | Cytoscape elements for the current graph |
| `GET /api/graph/entity?name=` | constrained neighbourhood walk around an entity |
| `GET /api/risk/flags` | R1–R6 pattern matches over the live graph |
| `POST /api/osint/search` | search + relevance filter, no fetch |
| `POST /api/osint/crawl` | background crawl → ingest → embed |
| `GET /api/osint/jobs` | recent crawl jobs |
| `GET /api/osint/jobs/{id}` | poll one job |

## Deploying to Render

The repo has a `render.yaml` blueprint. In the Render dashboard choose **New →
Blueprint**, point it at this repo, and fill in the secrets marked `sync: false`
(`NEO4J_URI`, `NEO4J_PASSWORD`, `GEMINI_API_KEY`, `VOYAGE_API_KEY`, `CORS_ORIGINS`).

Set `CORS_ORIGINS` to your Vercel URL once the frontend is deployed.

## Deploying the frontend to Vercel

In Vercel: **Add New → Project**, root directory `frontend`, environment variable
`NEXT_PUBLIC_API_URL` = your Render API URL (no trailing slash). After the first
deploy, copy the Vercel origin into Render's `CORS_ORIGINS`.

## Data and ethics

`backend/scripts/seed_articles.json` is **synthetic demo data**. Every name, case and
quantity is fictional, written to exercise the schema and risk rules. It is not real
reporting and must not be presented as such.

The crawler operates only on public web pages, obeys `robots.txt`, and rate-limits
itself. Risk flags are prioritisation aids for human analysts, never conclusions
about any individual.
