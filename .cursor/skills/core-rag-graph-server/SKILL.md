---
name: core-rag-graph-server
description: Maintains and extends the graph-enhanced RAG FastAPI server from arXiv 2603.25152 (HTML https://arxiv.org/html/2603.25152v1). Covers KTBuilder graph construction, graph merge and LLM entity resolution JSON protocol, community reports (Leiden + prompts), YAML/config prompts, and uv-based Python workflows. Use when editing this repository, graph_server APIs, kt_gen, resolution, graph_processor, community_reports, or config/prompt_templates.
---

# Core RAG graph server

## When this applies

Use this skill for work in **core_rag_graph**: FastAPI surface, graph construction, merging, entity resolution, community reporting, or prompt/config changes tied to the paper’s graph-RAG pipeline.

## Paper and intent

- **Paper**: https://arxiv.org/html/2603.25152v1  
- **Intent**: RAG enhanced with an explicit **knowledge graph**—extract entities/relations from chunks, store/merge graphs per KB, optional community summaries, retrieval agents driven by YAML prompts.

## Package management

- Use **uv** as the package manager.  
- If `pyproject.toml` exists: `uv sync`, `uv run <command>`.  
- If only `requirements.txt`: `uv pip install -r requirements.txt`, then `uv run python …` or `uv run uvicorn …`.

## Request flow (mental model)

1. **Ingest / construct**: Chunks + schema → `KTBuilder` → triples → `graph_processor.update_graph` (merge + **LLMEntityResolver** when an existing graph file is present).  
2. **Entity resolution**: Candidates from name similarity; LLM returns **only** JSON `{"same": [bool, ...]}` in **pair order**; parser in `utils/resolution.py` must stay in sync with the prompt.  
3. **Community reports**: `graph_processor.extract_community` → `CommunityReportsExtractor` (Leiden, attribute communities, LLM).  
4. **API**: `graph_server.py`—REST + WebSocket progress for long tasks.

## Files to touch (by task)

| Task | Primary files |
|------|----------------|
| API / CORS / WS | `graph_server.py` |
| Extraction / triples | `utils/kt_gen.py`, `config/base_config.yaml`, `config/prompt_templates.py` |
| Merge / persist graph | `utils/graph_processor.py` |
| Duplicate entity merge | `utils/resolution.py` |
| Community reports | `utils/community_reports.py`, `config/prompt_templates.py` |
| LLM client | `utils/call_llm_api.py` |
| Config load | `config/config_loader.py` |

## Checklist before finishing a change

- [ ] Prompt output shape matches parser (especially resolution JSON).  
- [ ] New dependencies recorded for **uv** (`pyproject.toml` or `requirements.txt`).  
- [ ] English-first copy for new prompts/strings unless product requires otherwise.

## Examples

**Run API with uv:**

```bash
uv run uvicorn graph_server:app --host 0.0.0.0 --port 20050
```

**Resolution contract (do not drift):**

- Prompt lists pairs 1..N; response: `{"same": [<N booleans>]}`.
