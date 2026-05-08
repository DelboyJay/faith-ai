# FAITH-031 - Web Search MCP Server

**Phase:** 6 - External Integrations
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-003, FAITH-035
**FRS Reference:** Section 4.15

---

## Objective

Implement FAITH web search using the Tavily API for both search and page
retrieval. The Web Search MCP server must call Tavily's hosted APIs and return
compact structured results to agents:
`title`, `url`, `snippet`, relevance/source metadata, and retrieved page content
when explicitly requested.

The goal is not to scrape Google directly, not to launch browser automation for
search results pages, and not to maintain a FAITH-hosted metasearch container.
Tavily is the selected v1 provider because it provides purpose-built search and
extract APIs for LLM/agent workflows.

---

## Architecture

```text
FAITH PA / agents
    |
    | MCP tool call: web_search.search / web_search.retrieve
    v
Web Search MCP server
    |
    | HTTPS POST /search or /extract
    v
Tavily API
    |
    v
Search results or retrieved page content normalised back to agents
```

### Runtime Components

- Add a `faith_mcp.web_search` package that calls Tavily's HTTP API and
  normalises responses.
- Read the Tavily API key from FAITH secrets or environment configuration, for
  example `TAVILY_API_KEY`; never commit or log the key.
- Use Tavily Search for result discovery.
- Use Tavily Extract for page retrieval from selected URLs.
- Do not add a FAITH-managed SearXNG container for this v1 path.
- Keep provider access behind the Web Search MCP interface so a future provider
  can be swapped without changing agent-facing tool names.

---

## Commands

| Command | Parameters | Returns |
|---|---|---|
| `search` | `query`, `max_results?`, `search_depth?`, `topic?`, `time_range?`, `include_domains?`, `exclude_domains?` | Structured result list |
| `search_docs` | `query`, `site?`, `max_results?` | Structured result list biased toward documentation |
| `retrieve` | `url` or `urls`, `extract_depth?`, `format?`, `max_chars?` | Retrieved page content and metadata |
| `status` | none | Tavily configuration and connectivity summary |

Search result items must contain at least:

- `title`
- `url`
- `snippet`
- `score` or source metadata where Tavily provides it

Retrieved page items must contain at least:

- `url`
- `content` or `raw_content`
- `status` or a structured extraction error

The tool must not return unbounded full-page content. Retrieval responses must
apply a configured maximum size and clearly report truncation.

---

## Privacy Behaviour

| Profile | Behaviour |
|---|---|
| `public` | Web search and retrieval enabled through Tavily. |
| `internal` | Web search and retrieval enabled, but no external result caching by FAITH. |
| `confidential` | Web search and retrieval disabled. The MCP server returns an error before calling Tavily. |

The privacy check must happen at the start of every command so hot-reloaded
profile changes take effect without restarting the tool.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Tavily API key missing | Return a structured configuration error explaining how to configure `TAVILY_API_KEY`. |
| Tavily returns 401/403 | Return a structured authentication error; do not expose the API key. |
| Tavily returns 429/quota errors | Return a clear rate-limit/quota error and preserve any retry guidance when available. |
| Tavily unavailable or request times out | Return a structured provider-unavailable error; do not raise raw exceptions to agents. |
| Tavily returns malformed JSON | Return a provider-response error with safe diagnostic metadata. |
| Privacy profile is `confidential` | Return an error immediately; no outbound request to Tavily. |
| Empty query or URL | Return a validation error. |
| Excessive `max_results` or `max_chars` | Clamp to configured maximums. |

---

## Acceptance Criteria

1. `faith_mcp.web_search` calls Tavily Search for web result discovery rather
   than scraping Google/Bing directly or calling a local SearXNG container.
2. `faith_mcp.web_search` calls Tavily Extract for explicit page retrieval.
3. The Tavily API key is loaded from FAITH secrets/environment configuration and
   is never committed, logged, or returned to agents.
4. `search()` returns structured results with `title`, `url`, `snippet`, and
   score/source metadata where available.
5. `search_docs()` supports an optional `site` parameter and uses Tavily domain
   filtering when possible.
6. `retrieve()` returns bounded page content with URL metadata and truncation
   reporting when content exceeds the configured size.
7. `status()` reports whether Tavily is configured and optionally verifies
   provider reachability without exposing secrets.
8. The tool returns structured errors instead of raw exceptions when Tavily is
   unavailable, unauthorised, rate-limited, or returns malformed data.
9. The tool is disabled for `confidential` privacy projects before any network
   call is made.
10. Tests are written first and use a fake HTTP transport/client. They must not
    make real Tavily network calls.
11. Tests cover successful search, docs search, page retrieval, privacy
    blocking, missing API key, authentication failure, rate limiting, timeout,
    malformed response, max-result clamping, max-content clamping, and status
    reporting.
12. The implementation is compared against FRS Section 4.15 before the task is
    marked `DONE`.

---

## Out of Scope

- Direct Google Search scraping with `requests`, `curl`, or browser-like headers.
- Browser automation for search results pages.
- Running a FAITH-managed SearXNG container for the v1 web-search path.
- Running a full independent crawler/index.
- Returning unbounded full page HTML/content.

---

## Notes

- Tavily is an external hosted provider and requires an API key, so FAITH must
  surface missing-key, quota, and network failures clearly.
- Search and page retrieval are intentionally separate commands. Agents should
  search first, inspect result metadata, then retrieve selected pages only when
  needed.
- The Web Search MCP interface should remain provider-neutral even though Tavily
  is the selected v1 backend.
