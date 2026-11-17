# SRE Patrol Agent

[繁體中文](README.md)

An AI agent that, given a Kubernetes namespace, automatically inspects every Deployment in it and writes an inspection report.

- Built on [deepagents](https://github.com/langchain-ai/deepagents) 0.7.39: one orchestrator plus specialist sub-agents
- Connects to existing observability tools over MCP: **Kubernetes and Prometheus** (always on), **Elasticsearch and Dynatrace** (optional, off by default)
- The model decides each Deployment's health (OK / WARN / CRIT / UNKNOWN); every finding must cite evidence
- Strictly read-only
- Run once or on a schedule; reports are written as Markdown files
- Prompts and reports are in Traditional Chinese

Diagrams: [docs/architecture.md](docs/architecture.md).

## Quick start

```bash
cp patrol_agent/.env.example patrol_agent/.env
```

Fill in at least `PATROL_MODEL` (`provider:model`, e.g. `openai:gpt-5.2`), the matching API key, `PROMETHEUS_URL`, and optionally `K8S_MCP_KUBECONFIG` (a read-only kubeconfig is recommended).

```bash
cd patrol_agent && uv sync
```

```bash
scripts/mcp.sh start
```

```bash
cd patrol_agent && uv run langgraph dev --no-browser
```

```bash
cd patrol_agent && uv run python patrol.py --namespace payments --once
```

Use `--interval 30m` to run on a schedule, or `--local` to run the graph in-process without `langgraph dev`. Reports go to `reports/<namespace>/<YYYYmmdd-HHMMSS>.md`.

Optional chat UI: `cd deep-agents-ui && yarn install && yarn dev`, then open http://localhost:3300 with Deployment URL `http://localhost:2024` and Assistant ID `patrol_agent`.

## Optional data sources

Set `ENABLE_ELASTICSEARCH=true` / `ENABLE_DYNATRACE=true` to enable them. When disabled (the default), the sub-agent is not registered, its tools are not loaded, the orchestrator prompt does not mention it, `scripts/mcp.sh` does not start its MCP server, and the report marks it as disabled. Restart `langgraph dev` after changing these flags.

- Elasticsearch uses Elastic's official MCP server, which is **deprecated** (security fixes only; Elastic recommends the Agent Builder MCP endpoint on 9.2+). It supports Elasticsearch 8.x / 9.x.
- Dynatrace uses a read-only MCP server bundled in this repo (`patrol_agent/dynatrace_mcp/`) for Dynatrace SaaS (classic) / Managed via Environment API v2.

## Tests

```bash
cd patrol_agent && uv run pytest
```

## License

MIT. Derived from [SRE-Agent](https://github.com/vapering/SRE-Agent); the original copyright notice is kept in [LICENSE](LICENSE). `deep-agents-ui/` comes from LangChain's deep-agents-ui (MIT, see [deep-agents-ui/LICENSE](deep-agents-ui/LICENSE)).
