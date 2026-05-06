# Vulture artifacts (`docs/unused-code/backend/artifacts`)

These files are **verbatim** output from [Vulture](https://github.com/jendrikseipp/vulture) over `services/ai-backend/src`, split by cluster path prefix.

| File                                                         | Contents                                                                                                                          |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| [`vulture-min60-src-only.txt`](./vulture-min60-src-only.txt) | Full run: `vulture src --min-confidence 60` from `services/ai-backend` (~634 lines, exit code 3 when dead-code candidates exist). |
| `cluster-*-vulture.txt`                                      | Same lines filtered to one cluster (see parent [README.md](../README.md)).                                                        |

**Regenerate** (from repo root):

```bash
cd services/ai-backend
export PYTHONPATH=src:../../packages/service-contracts/src
.venv/bin/vulture src --min-confidence 60 \
  | tee ../../docs/unused-code/backend/artifacts/vulture-min60-src-only.txt
```

Then re-split (from repo root, bash):

```bash
ART=docs/unused-code/backend/artifacts/vulture-min60-src-only.txt
BASE=docs/unused-code/backend/artifacts
grep '^src/runtime_api/' "$ART" > "$BASE/cluster-runtime-api-vulture.txt"
grep '^src/runtime_worker/' "$ART" > "$BASE/cluster-runtime-worker-vulture.txt"
grep '^src/runtime_adapters/' "$ART" > "$BASE/cluster-runtime-adapters-vulture.txt"
grep '^src/agent_runtime/execution/\|^src/agent_runtime/prompts/' "$ART" > "$BASE/cluster-agent-runtime-execution-vulture.txt"
grep '^src/agent_runtime/capabilities/' "$ART" > "$BASE/cluster-agent-runtime-capabilities-vulture.txt"
grep '^src/agent_runtime/persistence/\|^src/agent_runtime/retention/' "$ART" > "$BASE/cluster-agent-runtime-persistence-vulture.txt"
grep '^src/agent_runtime/context/' "$ART" > "$BASE/cluster-agent-runtime-context-memory-vulture.txt"
grep '^src/agent_runtime/delegation/' "$ART" > "$BASE/cluster-agent-runtime-delegation-vulture.txt"
grep '^src/agent_runtime/api/' "$ART" > "$BASE/cluster-agent-runtime-domain-services-vulture.txt"
grep '^src/agent_runtime/budgets/\|^src/agent_runtime/pricing/\|^src/agent_runtime/deployment/' "$ART" > "$BASE/cluster-agent-runtime-ops-economics-vulture.txt"
grep '^src/agent_runtime/observability/' "$ART" > "$BASE/cluster-agent-runtime-observability-vulture.txt"
grep '^src/agent_runtime/settings.py:\|^src/agent_runtime/validation.py:\|^src/agent_runtime/__init__.py:' "$ART" > "$BASE/cluster-agent-runtime-cross-cutting-vulture.txt"
```

Optional suppressions: pass [`../../ai-backend/vulture_whitelist.py`](../../ai-backend/vulture_whitelist.py) as a second path to vulture _after_ regenerating splits (whitelist is not included in these committed artifacts to avoid analyzing the whitelist file itself).
