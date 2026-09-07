# Remote Inference Web-Search Investigation — openai.com Tag-Count Task

**Date:** 2026-06-22 (session) · **Actor:** backend_engineer · **For:** root

## Task
Use the team's remote inference service (LLM API) to fetch the live page at
`https://openai.com/` and produce an HTML tag-count summary, because direct
egress from the sandbox to openai.com is blocked.

**Verdict: STILL NOT POSSIBLE (confirmed on re-test 2026-09-07).**
The inference service exposes **no executable web-search capability**, and the
sandbox has **no web egress at all**. No tag counts were produced — none were
fabricated. See the 2026-09-07 follow-up section below for the verification of
the "SearXNG-backed web_search" claim.

---

## 1. Service discovery (confirmed working)

`OPENAI_API_KEY` is set in the environment (value redacted).

```bash
curl -s -H "Authorization: Bearer $OPENAI_API_KEY" https://elementary.eidf.ac.uk/api/models
# HTTP 200 → {"data":[{"id":"qwen3.8-27b", ...}]}
```

- `GET /api/models` and `GET /api/v1/models` → **200**, model `qwen3.8-27b` present.
- Model `info.meta.capabilities` advertises `"web_search": true` (also `vision`,
  `code_interpreter`, `terminal`, `citations`, ...). **Note:** this flag is
  boilerplate and appears identically on all three models in the listing; it
  does not correspond to any working search path (verified below).
- `POST /api/chat/completions` → **200** (plain chat works, e.g. "Reply with exactly: OK" → `OK`).
- The gateway is **Open WebUI** (SPA served at the base path) fronting a
  **litellm → sglang** backend.

## 2. Attempts to activate web search (all failing to execute)

### 2a. OpenAI-style `web_search` tool → rejected (400)

```bash
curl -s -H "Authorization: Bearer <REDACTED>" -H "Content-Type: application/json" \
  https://elementary.eidf.ac.uk/api/chat/completions \
  -d '{"model":"qwen3.8-27b",
       "messages":[{"role":"user","content":"What is today's date?"}],
       "tools":[{"type":"web_search"}]}'
```

```json
{"detail":"litellm.BadRequestError: OpenAIException - 1 validation error:\n
  {'type': 'missing', 'loc': ('body', 'tools', 0, 'function'), 'msg': 'Field required',
   'input': {'type': 'web_search'}}\n
  File \"/sgl-workspace/sglang/python/sglang/srt/entrypoints/http_server.py\", line 1722, in openai_v1_chat_completions
    POST /v1/chat/completions [...] Received Model Group=qwen3.8-27b
Available Model Group Fallbacks=None"}
# HTTP_STATUS:400
```

### 2b. OpenAI-style `web_search_preview` + `web_search_options` → rejected (400)

```bash
curl -s -H "Authorization: Bearer <REDACTED>" -H "Content-Type: application/json" \
  https://elementary.eidf.ac.uk/api/chat/completions \
  -d '{"model":"qwen3.8-27b",
       "messages":[{"role":"user","content":"What is the current top story on the BBC news homepage? Cite the source."}],
       "tools":[{"type":"web_search_preview","web_search_options":{"search_context_size":"medium"}}],
       "max_tokens":400}'
```

```json
{"detail":"litellm.BadRequestError: OpenAIException - 1 validation error:\n
  {'type': 'missing', 'loc': ('body', 'tools', 0, 'function'), 'msg': 'Field required',
   'input': {'type': 'web_search_preview', 'web_search_options': {'search_context_size': 'medium'}}}\n
  File \"/sgl-workspace/sglang/python/sglang/srt/entrypoints/http_server.py\", line 1722, in openai_v1_chat_completions
    POST /v1/chat/completions [...] Received Model Group=qwen3.8-27b
Available Model Group Fallbacks=None"}
# HTTP_STATUS:400
```

The sglang backend **only accepts standard OpenAI function-calling entries**
(`{"type":"function","function":{...}}`); there is no native `web_search` /
`web_search_preview` tool type.

### 2c. Client-side function tool `web_search` → accepted, but server never executes it (200, `tool_calls`)

```bash
curl -s -H "Authorization: Bearer <REDACTED>" -H "Content-Type: application/json" \
  https://elementary.eidf.ac.uk/api/chat/completions \
  -d '{"model":"qwen3.8-27b",
       "messages":[{"role":"user","content":"Use your web search tool to find the current top story on the BBC news homepage. Cite your source."}],
       "tools":[{"type":"function","function":{"name":"web_search",
         "description":"Search the web for live/current information. Returns a list of search results (title, url, snippet).",
         "parameters":{"type":"object","properties":{"query":{"type":"string","description":"Search query"}},"required":["query"]}}}],
       "max_tokens":400}'
```

Raw response (abridged — full response returned two tool calls):

```json
{"id":"2366aea635964117baabf2d29ca9bcc3","created":1788784811,"model":"qwen3.8-27b",
 "object":"chat.completion","choices":[{"finish_reason":"tool_calls","index":0,
   "message":{"content":"\n\n","role":"assistant",
     "tool_calls":[
       {"index":0,"function":{"arguments":"{\"query\": \"BBC News homepage top story today\"}","name":"web_search"},"id":"call_a74aa5571897458ead5b4aca","type":"function"},
       {"index":1,"function":{"arguments":"{\"query\": \"site:bbc.com/news leading headline\"}","name":"web_search"},"id":"call_78ddc3504e2341669ce62036","type":"function"}],
     "reasoning_content":"The user is asking me to use the web search tool to find the current top article on the BBC News homepage. Let's search for it. ... I'll run several searches in parallel ..."}]},
 "usage":{"completion_tokens":201,"prompt_tokens":361,"total_tokens":562,...}}
# HTTP_STATUS:200
```

**The API returns `finish_reason: "tool_calls"` and stops.** It does NOT run a
search itself — standard function-calling contract: the *client* must execute
`web_search` and return the results. The client here is this sandbox, which has
**no egress** (see 2e), so the loop cannot be closed.

### 2d. Open WebUI server-side search flag `search: true` → ignored / no integration

```bash
# Open WebUI native chat proxy
curl -s -H "Authorization: Bearer <REDACTED>" -H "Content-Type: application/json" \
  -X POST https://elementary.eidf.ac.uk/api/v1/chat/completions \
  -d '{"model":"qwen3.8-27b",
       "messages":[{"role":"user","content":"What is the current top story on the BBC news homepage? Cite a source URL."}],
       "search":true}'
# HTTP_STATUS:200
```

Model reply (raw, abridged):

> "I can't verify the **current** top story on the BBC News homepage in real
> time, because it can change at any moment. You can check the current top
> story directly at: **Source:** https://www.bbc.com/news"

Reasoning content (raw): *"Need likely use live web? We have no browsing tool
in prompt. ... can't know current. Need not fabricate."*

Same result via `POST /api/chat/completions` with `"search":true`. The model
explicitly reports **no live web access** — i.e. no search integration is
configured/enabled on the Open WebUI side for this model/token.

Supporting checks:
- `GET /api/v1/functions/` → `[]` (HTTP 200) — **no custom functions** (no user-defined search function registered).
- Open WebUI admin routes (`/api/v1/search/search-integrations/`, `/api/v1/models/`,
  `/api/v1/config/`, `/api/v1/pipelines/`) are not reachable with this token
  (SPA catch-all 200 HTML / no JSON), so no search provider config is inspectable
  or modifiable from here.

### 2e. Direct sandbox egress → blocked by default-deny policy

```bash
curl -s https://openai.com/
# HTTP 403
Blocked by network policy: domain openai.com:443
  detail: no matching allow rule — blocked by default deny policy

curl -s -o /dev/null -w "%{http_code}" https://example.com/
# 403
```

**All** external domains are denied (default-deny). The only outbound route is
the inference service itself.

### 2f. Baseline: model asked to fetch openai.com with no tools → honest refusal (200)

```bash
curl -s -H "Authorization: Bearer <REDACTED>" -H "Content-Type: application/json" \
  https://elementary.eidf.ac.uk/api/chat/completions \
  -d '{"model":"qwen3.8-27b",
       "messages":[{"role":"user","content":"Fetch the live page at https://openai.com/ and count the total number of HTML tags and give a per-tag-name breakdown. If you cannot actually fetch a live page, say so explicitly and do not invent numbers."}],
       "max_tokens":800}'
```

```json
{"id":"df534e2336c54b3c89567b445762ebf0","created":1788784781,"model":"qwen3.8-27b",
 "object":"chat.completion","choices":[{"finish_reason":"stop","index":0,
   "message":{"content":"\n\nI cannot actually fetch the live page at https://openai.com/ in this environment, so I won't provide tag counts or a per-tag-name breakdown.\n\nIf you can provide the page's saved HTML source, or an environment/tool that can fetch it for me, I can count the HTML tags and give you the breakdown.",
     "role":"assistant",
     "reasoning_content":"We need answer user's request. ... no browsing tool available in prompt. We cannot actually fetch. Must say explicitly cannot fetch and do not invent numbers. ..."}}],
 "usage":{"completion_tokens":239,"prompt_tokens":97,"total_tokens":336,...}}
# HTTP_STATUS:200
```

## 3. Tag-count summary

**Not produced.** Per the task's anti-fabrication rule, no tag counts are
reported. The model itself refused to invent numbers (2f), and no path exists
to retrieve the live HTML of openai.com:

| Path | Result |
|---|---|
| Direct `curl https://openai.com/` from sandbox | 403 — default-deny network policy |
| `tools: [{"type":"web_search"}]` | 400 — sglang requires `function` field |
| `tools: [{"type":"web_search_preview", ...}]` | 400 — same validation error |
| Function tool `web_search` (client-side) | 200 — `tool_calls` returned, **never executed** (executor = sandbox, which has no egress) |
| Open WebUI `search: true` | 200 — ignored; model reports no live web access (no search integration configured) |
| Open WebUI `/functions/` | `[]` — no registered functions |

## 4. Root cause & unblock options

The advertised `web_search: true` capability flag is **declarative only**; the
serving stack (Open WebUI → litellm → sglang) implements web search purely as
client-executed function calling, and no search provider is configured on the
gateway. Combined with the sandbox's default-deny egress policy, the page
cannot be fetched by any means available to the team.

To unblock the original goal, the environment owner must do **one** of:
1. Add an egress allow rule for `openai.com:443` (sandbox can then fetch the
   HTML directly and count tags locally), or
2. Configure a server-side search integration on the Open WebUI instance
   (e.g. Tavily/Google/Bing) so `search: true` actually executes, or
3. Add an allow rule for a search-API endpoint so the `web_search` tool_call
   loop can be closed client-side by a proxy with egress.

---

## 5. Follow-up (2026-09-07) — verification of the "SearXNG-backed web_search" claim

**Actor:** backend_engineer · **Token:** same `OPENAI_API_KEY` (value redacted)

The environment owner reported that a `web_search` function backed by SearXNG
(URL template `http://searxng:8080/search?q=<query>`) has been configured on the
gateway, and that `searxng` is reachable from the gateway (Docker network) but
not from this sandbox. All claims below were re-verified live against the
gateway today.

### 5.0 Gateway identity & token role (new findings)

```
GET /api/config
# 200 {"status":true,"name":"Open WebUI","version":"0.11.1",...}

GET /api/v1/auths/
# 200 {"id":"53a28aeb-...","name":"Alistair Grant","role":"admin",
#       "email":"a.grant@epcc.ed.ac.uk",
#       "permissions":{"features":{"web_search":true,...}, ...}}
```

- Gateway is **Open WebUI 0.11.1**.
- The token is an **admin-role API key** and `permissions.features.web_search` is
  **true** — so every admin read route is inspectable. (Note: `admin` role +
  `web_search` permission only *gate the UI/feature switch*; they do not imply a
  search backend is wired.)

### 5.1 Step 1 — Re-listing gateway functions (raw)

| Request | Raw response (HTTP) |
|---|---|
| `GET /api/v1/functions/` | `[]` (200, `application/json`) |
| `GET /api/v1/functions/list` | `[]` (200) |
| `GET /api/functions/` | SPA HTML (catch-all — not a JSON route) |
| `GET /api/v1/functions/search` | SPA HTML (catch-all) |
| `GET /api/v1/functions/web_search` | SPA HTML (catch-all) |
| `GET /api/v1/functions/web_search/query` | SPA HTML (catch-all) |
| `GET /api/v1/tools/` / `GET /api/v1/tools/list` | `[]` (200) |
| `GET /api/v1/pipelines/list` | `{"data":[]}` (200) |
| `GET /api/v1/configs/tool_servers` | `{"TOOL_SERVER_CONNECTIONS":[]}` (200) |

**`web_search` is NOT present in any registry** — not as a function, tool,
pipeline, or tool-server connection. No SearXNG entry exists anywhere visible
with this (admin) token.

### 5.2 Step 2a — Direct function-query endpoint → DOES NOT EXIST

```
POST /api/v1/functions/web_search/query   {"q":"openai.com"}     -> 405 {"detail":"Method Not Allowed"}
POST /api/v1/functions/web_search/query   {"query":"openai.com"} -> 405 {"detail":"Method Not Allowed"}
# also tried (all 405): {"form":{...}}, {"data":{...}}, {"id":...}, trailing-slash
#   variants, /v1/functions/{1,2,3}/query, /v1/pipelines/web_search[/query][/execute],
#   /v1/functions/{run,execute,query}[/]
```

**The 405 is NOT proof the route exists.** Decisive control experiment:

```
# POST to a REAL route with an invalid body -> FastAPI validation error:
POST /api/v1/functions/create  {}            -> 422 {"detail":[{"loc":["body","id"],"msg":"Field required"},...]}

# POST to a COMPLETELY BOGUS path -> byte-identical 405:
POST /api/v1/totally-bogus-nonexistent-path  {"q":"openai.com"} -> 405 {"detail":"Method Not Allowed"}
```

The 405 `Method Not Allowed` is the **SPA/Caddy catch-all for POST to any
unmatched path** (GET to the same bogus paths returns 200 + SPA HTML). A real
POST route would return 422/400/200. Cross-checked against the Open WebUI 0.11.1
frontend bundle (all 163 JS chunks fetched and grepped): the function client only
contains management routes — `${base}/functions/`, `create`, `list`, `export`,
`load/url`, `id/${id}`, `id/${id}/delete`, `id/${id}/toggle[/global]`,
`id/${id}/update`, `id/${id}/valves[/spec|update|user...]`. **There is no
function query/execute/run endpoint in this version at all.**

### 5.3 Step 2b — chat/completions with `function_call` → ignored (200, no execution)

```
POST /api/v1/chat/completions
{"model":"qwen3.8-27b",
 "messages":[{"role":"user","content":"What is openai.com?"}],
 "function_call":{"name":"web_search","args":{"q":"openai.com"}}}
# 200, finish_reason: "stop", message.tool_calls: null
# content: "openai.com is the official website of OpenAI. It provides
#           information about OpenAI's products and services, including ChatGPT, ..."
# reasoning_content: "...a straightforward factual question. I should provide a
#                    clear, helpful answer about OpenAI as an organization..."
```

The `function_call` directive is **silently dropped** (not part of the
sglang/litellm contract); the model answers from parametric knowledge. No search
was executed.

### 5.4 Step 2c — Full agentic loop (as far as it can go)

1. `tools:[{type:"function",function:{name:"web_search",parameters:{q}}}]` →
   200, `finish_reason:"tool_calls"`, e.g.
   `tool_calls[0].function = {"name":"web_search","arguments":"{\"q\": \"official OpenAI website\"}"}`
   (call id `call_dd21bceeefb0450489098af4`).
2. Execution step is **impossible from the sandbox**: `searxng:8080` is
   default-deny here (verified today): `403 Blocked by network policy: domain
   searxng:8080 — no matching allow rule`.
3. Feeding a placeholder `{"error":"EXECUTION IMPOSSIBLE FROM SANDBOX",...}` back
   as `role:tool` → model retries once, then falls back to parametric knowledge
   and labels the failure honestly.

So the loop can be *started* but **cannot be closed** — exactly as in the
original investigation — because the executor (the gateway) provides no
executable search route, and the sandbox has no egress.

### 5.5 The native pipeline is also dark (decisive test)

In Open WebUI, the `web_search` capability is a **builtin** of the *native* chat
pipeline (UI renders `web_search_call` events), gated by the global feature flag
`features.enable_web_search` **and** the user's `permissions.features.web_search`
(ours = true). Probing the native path:

```
POST /api/chat/completions   (stream)
{"model":"qwen3.8-27b","web_search":true, "messages":[{"role":"user",
  "content":"Search the web for the current top BBC News headline... If no
  web search tool, reply exactly: NO_SEARCH_AVAILABLE."}]}
# 200 stream. Distinct delta keys: [content, reasoning_content, role]
# BUILTIN (web_search_call/openai_tool/actions/citations) events fired: 0
# Final answer: "NO_SEARCH_AVAILABLE"
```

Supporting evidence that the builtin is not wired:

- Global admin config `GET /api/v1/auths/admin/config` (29 keys, readable as
  admin) contains **no `ENABLE_WEB_SEARCH` / search-provider key at all**.
- Public `GET /api/config` `features` block: only auth/ldap/signup/websocket keys —
  no `enable_web_search`.
- Open WebUI 0.11.1 frontend bundle: **zero occurrences of `searxng`** (all 163
  chunks), no `/v1/search/*` API client, no search-integration route.
- `GET /api/v1/search/...` (all variants incl. `integrations`, `providers`,
  `engines`) → SPA catch-all; no such routes exist.
- `GET /api/v1/configs/tool_servers` → `{"TOOL_SERVER_CONNECTIONS":[]}` — if
  SearXNG were exposed as a tool server, it would appear here.

### 5.6 What `web_search` actually returns (to the extent observable)

**Nothing — the function does not exist as an executable object on this
gateway.** There is no registry entry, no HTTP execution endpoint, no builtin
firing, no search integration, and no SearXNG configuration visible with an
admin token. The `web_search: true` flags (model `meta.capabilities` — identical
on all models incl. `arena-model`-style boilerplate — and user permissions)
are declarative/UI-gate only, exactly as established in §2 of the original
report.

Even if it were working: the owner's own spec (`http://searxng:8080/search?q=`)
is SearXNG's **results** endpoint — it returns result metadata (titles, URLs,
snippets, optional content excerpts), **not** the raw HTML of a target page.
An exact HTML tag count of `https://openai.com/` is therefore **not derivable
from search results by construction**; it requires fetching the live page
(direct egress or a fetch-capable server-side tool).

### 5.7 Tag-count summary for https://openai.com/

**Not produced — and not derivable by any path available today.**

| Path | 2026-09-07 result |
|---|---|
| Direct `curl https://openai.com/` | 403 default-deny (unchanged) |
| `searxng:8080` from sandbox | 403 default-deny (verified) |
| `POST /v1/functions/web_search/query` | 405 catch-all — **endpoint does not exist** (control: bogus path 405, real route 422) |
| function registry / tools / pipelines / tool-servers | all `[]` — `web_search` not registered |
| chat `function_call` directive | 200, silently ignored, parametric answer |
| agentic loop w/ `tools` | model emits tool_calls; loop uncloseable (no server executor, no sandbox egress) |
| native pipeline `web_search:true` | 0 builtin search events; model: `NO_SEARCH_AVAILABLE` |

No numbers were generated or estimated. Per the honesty requirement: any
"tag count" derived from search *snippets* would be an unlabelled fabrication;
nothing was produced.

### 5.8 What would unblock (supersedes §4 options)

1. **Register the search as a real Open WebUI function/pipeline** visible in
   `GET /api/v1/functions/` (or a tool-server in `/v1/configs/tool_servers`),
   *or* enable the global `ENABLE_WEB_SEARCH` feature **plus** a configured
   search provider on this 0.11.1 instance (the SearXNG URL template would then
   need to be wired into that provider).
2. Or add a **page-fetch** capability (a function that GETs a URL and returns
   HTML) — search results alone can never yield exact tag counts.
3. Or an egress allow-rule for `openai.com:443` (original option 1, still the
   simplest path to an *exact* tag count).

Verification checklist for the owner (all currently fail / are empty):
- [ ] `GET /api/v1/functions/` should list a `web_search` function
- [ ] `GET /api/v1/configs/tool_servers` should list the SearXNG tool server
- [ ] `GET /api/v1/auths/admin/config` should show the search feature/provider keys
- [ ] native `POST /api/chat/completions` with `web_search:true` should emit
      `web_search_call` events in the stream

---
*All API keys in this report are redacted. Raw probe payloads used are removed
from the workspace (`.tmp_*` files). Follow-up raw probes of 2026-09-07 were kept
only in session logs; this section contains the verbatim responses.*
