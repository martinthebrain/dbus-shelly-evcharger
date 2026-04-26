# SPDX-License-Identifier: GPL-3.0-or-later
"""Refresh generated Markdown blocks for the local Control API docs."""

from __future__ import annotations

from pathlib import Path
import sys


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from venus_evcharger.control.docgen import (
    GENERATED_MARKDOWN_BLOCK_RENDERERS,
    replace_generated_markdown_block,
)


_DOC_BLOCKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CONTROL_API.md", ("CONTROL_API_COMMAND_MATRIX", "CONTROL_API_GETTING_STARTED")),
    ("API_OVERVIEW.md", ("API_OVERVIEW_CLIENT_STARTING_POINTS",)),
    ("README.md", ("README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED",)),
)


def _render_document(path: Path, block_names: tuple[str, ...]) -> None:
    document = path.read_text(encoding="utf-8")
    for block_name in block_names:
        document = replace_generated_markdown_block(
            document,
            block_name,
            GENERATED_MARKDOWN_BLOCK_RENDERERS[block_name](),
        )
    path.write_text(document, encoding="utf-8")


def main() -> int:
    for relative_path, block_names in _DOC_BLOCKS:
        _render_document(_REPO_ROOT / relative_path, block_names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
