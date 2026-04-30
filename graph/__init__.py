"""Compatibility package for graph.* imports.

This repo was extracted without a traditional package layout. Most modules import
via `graph.*` (e.g., `graph.utils.logger`). To keep those imports working under
`uv run` without moving files yet, we provide a small wrapper package.
"""

