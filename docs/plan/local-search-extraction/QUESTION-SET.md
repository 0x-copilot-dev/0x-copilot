# AC3 Question Set — answers that live below the fold

Status: MEASURED 2026-08-03 · Gates: `docs/plan/local-search-extraction/PRD.md` §6 AC3
Machine-readable companion: [`question_set.json`](./question_set.json)

---

## 1. Why this exists before the code

The PRD is explicit that this file comes first:

> **AC3 — answer quality improves on a fixed question set.** A small held-out set of questions
> whose answers exist below the fold of a page (i.e. _not_ in the snippet) is answered correctly
> more often than the snippet-only baseline. Without this AC the feature is unfalsifiable.

And §7 names the decision this set actually settles:

> this makes `web_search` meaningfully slower for a modest quality gain on _easy_ questions, and a
> large gain on _hard_ ones. If most real usage is easy lookups, the latency cost may not be worth
> it. AC3's question set is what settles that — build it first.

So the set is not a test fixture. It is the falsifier for the whole feature: if fetch-and-extract
cannot beat the snippet baseline **here**, on questions selected to be maximally favourable to it,
it will not beat it anywhere, and S1 should not ship.

## 2. The gate a question had to pass

Every question was admitted by measurement, never by assumption. Three conditions, all checked
against live results:

1. the exact answer string appears in the fetched page text;
2. the exact answer string appears in **none of the four returned snippets**;
3. the source page is itself one of those four results — otherwise fetch-and-extract could never
   reach it either, and the question would measure nothing.

Condition 2 is the whole point. Condition 3 is what keeps the set honest: it is easy to write a
question whose answer is buried in a document no search would ever surface, and such a question
makes the feature look bad for reasons the feature cannot control.

The search call mirrors the runtime's own call site — `WebSearchToolRegistry._search` in
`services/ai-backend/src/runtime_worker/dependencies.py` — parameter for parameter:

| Parameter     | Value          |
| ------------- | -------------- |
| library       | `ddgs==9.14.4` |
| `region`      | `wt-wt`        |
| `safesearch`  | `moderate`     |
| `timelimit`   | `y`            |
| `max_results` | `4`            |
| `backend`     | `auto`         |

String comparison is case-folded and whitespace-collapsed, with typographic quotes, dashes and
non-breaking spaces mapped to ASCII, so a line wrap inside an RFC is not scored as a mismatch.

`question_set.json` stores **all four snippets verbatim** for every question, so the claim
"the answer was not in the snippet" is auditable without re-running anything.

### One limitation, stated plainly

Condition 1 was checked against `lxml` full-document text, **not** against `trafilatura`, which
§3 of the PRD names as the extractor S1 will add. `trafilatura` is not installed in the service
venv yet, so it could not be used here.

That gap matters in one direction: main-content extraction deliberately discards boilerplate, and
several answers in this set live in structures that a main-content extractor may classify as
boilerplate rather than prose — the nginx Syntax/Default/Context directive tables, MDN's fenced
code block, jq's example tables, the git-gc CONFIGURATION list. If S1 extracts with `trafilatura`
and a question suddenly cannot be answered from the extracted text, **suspect the extractor before
suspecting the question**.

The first task after adding `trafilatura` should therefore be to re-run condition 1 through it and
record which of the 17 survive. A question whose answer `trafilatura` drops is not a bad question —
it is a measurement of the extractor, and one worth having before the token-budget numbers land.

## 3. The set — 17 questions, 12 domains

Depth is the answer's character offset divided by the extracted page length: how far past the top
of the document a reader must get before the answer appears.

| id                                   | question                                                                   | source                | depth | answer (abbrev.)                                            |
| ------------------------------------ | -------------------------------------------------------------------------- | --------------------- | ----- | ----------------------------------------------------------- |
| `nginx-client-max-body-size`         | What is nginx's default `client_max_body_size`?                            | nginx.org             | 14%   | `client_max_body_size 1m`                                   |
| `go-time-reference-unix`             | Go's reference time, as a Unix timestamp, is what number?                  | pkg.go.dev            | 18%   | `As a Unix time, this is 1136239445`                        |
| `sqlite-max-column`                  | Largest value `SQLITE_MAX_COLUMN` can be raised to at compile time?        | sqlite.org            | 28%   | `as large as 32767`                                         |
| `mdn-429-retry-after-example`        | In MDN's worked 429 example, what `Retry-After` value is sent?             | developer.mozilla.org | 29%   | `Retry-After: 3600`                                         |
| `rfc3339-unknown-offset`             | Which offset means "UTC known, local offset unknown"?                      | datatracker.ietf.org  | 31%   | `an offset of "-00:00"`                                     |
| `sqlite-charint-affinity`            | Which declared type shows that affinity-rule _order_ matters?              | sqlite.org            | 32%   | `A column whose declared type is "CHARINT"`                 |
| `pg-transaction-timeout-interaction` | What happens when `transaction_timeout` <= `statement_timeout`?            | postgresql.org        | 36%   | `then the longer timeout is ignored`                        |
| `semver-prerelease-precedence`       | The spec's worked example of pre-release precedence ordering?              | semver.org            | 43%   | `1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < …`        |
| `curl-max-redirs-default`            | How many redirects does curl follow by default with `--location`?          | curl.se               | 43%   | `By default the limit is set to 50 redirects`               |
| `jq-ltrimstr-example`                | Output of `jq '[.[]\|ltrimstr("foo")]'` in the manual's example?           | jqlang.org            | 44%   | `["fo","","barfoo","bar","afoo"]`                           |
| `rfc8259-duplicate-names`            | On duplicate object names, what does RFC 8259 say many implementations do? | datatracker.ietf.org  | 45%   | `Many implementations report the last name/value pair only` |
| `pg-max-wal-size-default`            | Default `max_wal_size`, and the assumed unit when none is given?           | postgresql.org        | 52%   | `taken as megabytes. The default is 1 GB.`                  |
| `redis-maxmemory-samples`            | How many keys does approximated-LRU sample per eviction by default?        | redis.io              | 58%   | `maxmemory-samples 5`                                       |
| `git-gc-log-expiry`                  | How old must `gc.log` be before `git gc --auto` runs again?                | git-scm.com           | 62%   | `more than gc.logExpiry old. Default is "1.day"`            |
| `git-worktree-prune-expire`          | What grace period does `git gc` use for `git worktree prune`?              | git-scm.com           | 70%   | `git worktree prune --expire 3.months.ago`                  |
| `rfc9110-418-unused`                 | Why does RFC 9110 mark 418 as Unused?                                      | ietf.org              | 76%   | `was an April 1 RFC that lampooned the various ways HTTP…`  |
| `jq-skip-function`                   | What does jq's `skip(n; expr)` do?                                         | jqlang.org            | 79%   | `The skip function skips the first n outputs from expr`     |

Sources are documentation, standards and reference manuals — chosen over news precisely so the set
does not rot (§6 covers the residual risk and its detector). Every question was measured with its
source page at **rank 1**.

### Three worked examples, in full

These are the shape of the whole set. Each pairs the measured rank-1 snippet against the answer.

**`mdn-429-retry-after-example`** — the snippet names the header and withholds the value:

> _snippet:_ "A Retry-After header may be included to this response to indicate how long a client
> should wait before making the request again."
>
> _answer, 29% down, inside a fenced HTTP response block:_ `Retry-After: 3600`

**`git-worktree-prune-expire`** — the snippet is the man page's one-line NAME entry:

> _snippet, in its entirety:_ "git-gc - Cleanup unnecessary files and optimize the local repository."
>
> _answer, 70% down, in the CONFIGURATION section:_ `git worktree prune --expire 3.months.ago`

The whole CONFIGURATION section — where every default on that page lives — is invisible to a
snippet-only reader. That is why the same page supports a second question (`git-gc-log-expiry`)
that also passes the gate: the snippet is not a lossy summary of the page, it is one line of it.

**`pg-transaction-timeout-interaction`** — the snippet shows the wrong parameter from the right
section:

> _snippet:_ "19.11.1. Statement Behavior #. client_min_messages (enum) #. Controls which message
> levels are sent to the client…"
>
> _answer, 36% down, in the `transaction_timeout` entry:_ `then the longer timeout is ignored`

Note what the snippet does and does not carry: it is the top of the section, and the answer sits
36% further down in a different entry. Nothing about it is wrong — it is simply not where the fact
lives, which is the whole shape this set is built to capture.

## 4. What building the set measured, beyond the set itself

These are findings, not asides — each one changes how AC3 must be run or how S1 must behave.

### 4.1 DuckDuckGo's top-4 is not deterministic

Two searches issued **six seconds apart with a byte-identical query** returned **disjoint** result
sets:

| run | rank 1                                             | canonical nginx docs present? |
| --- | -------------------------------------------------- | ----------------------------- |
| A   | `nginx.org/en/docs/http/ngx_http_core_module.html` | yes, rank 1                   |
| B   | `bobcares.com/blog/module-ngx_http_core_module/`   | no                            |

Run A is the one preserved in `question_set.json` under `nginx-client-max-body-size`; run B's four
URLs are reproduced above because they will not otherwise survive anywhere. Across the whole
exercise, 6 of the 17 accepted questions — `nginx-client-max-body-size`, `rfc3339-unknown-offset`,
`pg-max-wal-size-default`, `sqlite-charint-affinity`, `rfc9110-418-unused` and
`pg-transaction-timeout-interaction` — failed condition 3 on one run and passed it on a later run
of the **identical** query, with nothing changed but the clock.

**Consequence for AC3:** a single pass over the set is not a measurement. Both the baseline and the
treatment arm must run the set N times and report a rate, because a chunk of the variance is
DuckDuckGo's, not the feature's. Reporting one pass would let ranking noise masquerade as a
quality delta in whichever direction happened to be lucky.

**Consequence for S1:** AC1's snippet fallback carries more weight than it first appears. Discovery
itself is unreliable, so "the page we wanted was not among the four" is a routine outcome, not an
edge case.

### 4.2 Query phrasing, not page structure, decides whether the snippet has the answer

The single most instructive result. The same page and the same fact flip verdicts on query shape:

| query                                              | rank-1 snippet                                                           | verdict                  |
| -------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------ |
| `git gc worktree prune expire grace period config` | "When git gc is run, it calls git worktree prune --expire 3.months.ago…" | answer IS in snippet     |
| `git-scm.com git-gc documentation`                 | "git-gc - Cleanup unnecessary files and optimize the local repository."  | answer is NOT in snippet |

A fact-shaped query makes DuckDuckGo extract the snippet from the matching region, and the answer
comes back for free. A topic-shaped query returns the page's lead instead.

This is direct evidence **for** the PRD's §8.2 decision — snippet-anchored windowing works because
the snippet already localises the match — and simultaneously a limit on it: when the snippet is the
document lead rather than a match region, there is nothing to anchor to and the fallback ("return
the article's lead, capped") is what actually runs. On this set that fallback path is the common
case, not the rare one. **S1 should measure how often the snippet is locatable in the extracted
article, and treat a low rate as a signal to reconsider the window strategy, not as a bug.**

Three candidates were dropped outright because the answer sat in the rank-1 snippet once the query
named the fact: `python-lru-cache-default` (`lru_cache(maxsize=128, typed=False)` came back in the
docs.python.org snippet), `sqlite-blob-affinity-no-type`, and `k8s-prestop-grace-extension` (whose
snippet quoted the answer word for word). They are listed in §7.

Two others — `nginx-client-max-body-size` and `git-worktree-prune-expire` — failed under a
fact-shaped query and passed under a topic-shaped one, which is why they appear in the set. Their
recorded queries in `question_set.json` are the topic-shaped versions, and **the query text is part
of the question**: re-running these with fact-shaped phrasing will legitimately show the answer in
the snippet and prove nothing.

### 4.3 `timelimit="y"` demotes canonical documentation

The runtime pins `timelimit="y"` (past year). Reference documentation frequently loses to recently
published SEO content under that filter: queries for RFC 3339, RFC 6749, `functools`,
`Array.prototype.sort` and the Kubernetes pod lifecycle all returned tutorial blogs and
listicle-shaped pages while the canonical document was absent from the top four.

This is a pre-existing property of the current tool, unchanged by this PRD, and it is worth stating
plainly: **the discovery stage is already biased away from the primary sources that fetch-and-extract
is best at.** Whether `timelimit` should stay `"y"` is out of scope here, but it belongs in the
hyperparameters conversation, and AC4's cost numbers will be read differently depending on it.

### 4.4 Some canonical documentation refuses a plain HTTP client

Encountered while building the set, with a stock browser `User-Agent`:

| host                                  | response to a plain HTTP GET                                               |
| ------------------------------------- | -------------------------------------------------------------------------- |
| `w3.org` (WCAG 2.1)                   | `403 Forbidden`                                                            |
| `freedesktop.org` (systemd man pages) | `418 I'm a teapot`                                                         |
| `gnu.org` (GNU make manual)           | connect timeout, intermittent — succeeded once, then failed on two retries |

`gnumake-delete-on-error` was dropped from the set for this reason: its answer is genuinely below
the fold of a 525k-character manual, but the page could not be fetched reliably enough to belong in
a gating set.

This is AC1 and AC6 in miniature. S1's fetch layer will meet these hosts, and the required behaviour
is the snippet fallback with a hard timeout — never an error, never a stall. The intermittent
gnu.org case is the nastiest of the three: a host that succeeds on the first attempt and times out
on the next is what turns a per-fetch timeout from a nicety into a correctness requirement.

## 5. How to run AC3 against this set

1. **Baseline arm** — today's `web_search`: four snippets, no fetch. Answer each question from
   snippets alone.
2. **Treatment arm** — S1: fetch, extract, snippet-anchored window.
3. **Scoring** — the answer string, normalised as in §2, appears in the model's response. Exact
   strings were chosen to be distinctive precisely so scoring needs no judge model. Two are short
   enough to warrant care (`maxmemory-samples 5`, `Retry-After: 3600`) — score those against the
   full string, not the bare number.
4. **Repeats** — N passes per arm, report a rate with its spread (§4.1). Both arms must run in the
   same session so they see comparable DuckDuckGo behaviour.
5. **Report alongside AC4** — a quality gain is only meaningful next to its measured token and
   latency cost.

A question where **both** arms fail because the source page was not in the top four is a discovery
miss, not a quality signal. Count those separately; they measure §4.1, not the feature.

## 6. Staleness — the real risk, and how it surfaces

Every source here is documentation, so the failure mode is not link rot but silent edits: a default
changes, a section is rewritten, a page is restructured. `max_wal_size`, `maxmemory-samples`,
`SQLITE_MAX_COLUMN` and `--max-redirs` are all values that could be changed by their upstream
projects without ceremony. **A stale entry is worse than a missing one**, because it scores a
correct answer as wrong and makes the feature look broken.

The detector is the gate itself, re-run. Because `question_set.json` records the answer string, the
source URL, the character offset and all four snippets, a re-run separates four distinct failures:

| signature                                               | meaning                                                        | action                                                                                                                           |
| ------------------------------------------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| answer string no longer found on the page               | upstream edited the fact — **the entry is stale**              | re-read the page, update the answer or retire the question                                                                       |
| answer found, but `char_offset`/`depth_pct` moved a lot | page restructured; fact intact                                 | refresh the offsets; no scoring change                                                                                           |
| answer now appears in a snippet                         | the ranker changed, or the question drifted fact-shaped (§4.2) | **re-run several times first** — condition 2 flips too (see below), so one observation is not a verdict; retire only if it holds |
| source page missing from the top four                   | usually §4.1 volatility                                        | re-run before concluding anything                                                                                                |

Re-run the gate before every AC3 measurement, not on a schedule. The set is small enough that this
is cheap, and running it is the only thing that makes the numbers trustworthy.

**Condition 2 is nondeterministic as well, and that correction matters.** §4.1 documents
flip-flopping for condition 3, but it is not confined to it: on one verbatim run of §8,
`mdn-429-retry-after-example` reported `in_snippet=True` and reported `False` on twelve subsequent
probes of the identical query — DuckDuckGo alternates between two result clusters for it. Since the
table above sends "answer now appears in a snippet" to _retirement_, a single unlucky first run
would permanently delete a good question. Require the observation to repeat before acting on it.

## 7. Rejected candidates, kept on the record

Recorded so nobody re-proposes them, and because the rejections are themselves the evidence for §4.

| candidate                                                                                                       | rejected because                                                                                        |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `python-lru-cache-default`                                                                                      | `lru_cache(maxsize=128, typed=False)` was in the rank-1 docs.python.org snippet — fails condition 2     |
| `sqlite-blob-affinity-no-type`                                                                                  | the rank-1 sqlite.org snippet quoted the whole affinity rule                                            |
| `k8s-prestop-grace-extension`                                                                                   | the rank-1 kubernetes.io snippet quoted "a small, one-off grace period extension of 2 seconds" verbatim |
| `nginx-413-status`                                                                                              | source page never reached the top four across three attempts                                            |
| `rust-hashmap-siphash`                                                                                          | passed once, then failed discovery twice; too unstable to gate on                                       |
| `gnumake-delete-on-error`                                                                                       | gnu.org fetch unreliable from a plain HTTP client (§4.4)                                                |
| WCAG 2.1 contrast ratios (`w3.org`)                                                                             | `403 Forbidden` to a non-browser client                                                                 |
| systemd `RestartSec` (`freedesktop.org`)                                                                        | `418 I'm a teapot` to a non-browser client                                                              |
| `man7-open-o-tmpfile`, `mdn-samesite-lax-post-window`, `mdn-sort-empty-slots`, `rfc6749-unsupported-grant-type` | canonical page never discovered under `timelimit="y"` (§4.3)                                            |

## 8. Reproducing the measurement

The gate is a short script against the same library the runtime uses. Run it from the ai-backend
service so `ddgs==9.14.4` is the pinned version:

```bash
cd services/ai-backend && PYTHONPATH="$PWD/src:../../packages/service-contracts/src:../../packages/audit-chain/src" \
  .venv/bin/python - <<'PY'
import json, re, httpx
from ddgs import DDGS
from lxml import html as lxml_html

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def norm(t):
    t = t.replace(" ", " ").replace("’", "'").replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", t).strip().lower()

def page_text(url):
    with httpx.Client(follow_redirects=True, timeout=40.0, headers={"User-Agent": UA}) as c:
        resp = c.get(url)
        resp.raise_for_status()
        doc = lxml_html.fromstring(resp.text)
    for b in doc.xpath("//script|//style|//noscript"):
        b.getparent().remove(b)
    return norm(doc.text_content())

def canon(u):
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", u.strip().lower())).rstrip("/")

for q in json.load(open("../../docs/plan/local-search-extraction/question_set.json"))["questions"]:
    with DDGS() as d:
        res = list(d.text(q["query"], region="wt-wt", safesearch="moderate",
                          timelimit="y", max_results=4, backend="auto") or ())
    ans = norm(q["answer"])
    in_snip = ans in norm(" ".join(r.get("body", "") for r in res))
    rank = next((i for i, r in enumerate(res, 1) if canon(r.get("href", "")) == canon(q["source_url"])), None)
    on_page = ans in page_text(q["source_url"])
    print(f'{q["id"]}: on_page={on_page} in_snippet={in_snip} rank={rank}')
PY
```

A healthy entry prints `on_page=True in_snippet=False rank=1`. Map any other line through the §6
table before changing anything — `rank=None` on its own is usually noise, and `on_page=False` is
the one that always means a real edit upstream.

That distinction is not theoretical. Running the block above over the first three questions
immediately after this document was written produced:

```
redis-maxmemory-samples: on_page=True in_snippet=False rank=1
semver-prerelease-precedence: on_page=True in_snippet=False rank=None
mdn-429-retry-after-example: on_page=True in_snippet=False rank=None
```

All three were recorded at rank 1 minutes earlier. Nothing upstream changed; the below-the-fold
property held in every case. Two `rank=None` results in a three-question sample is §4.1 doing
exactly what §4.1 says it does — and is the reason AC3 must average over repeats.
