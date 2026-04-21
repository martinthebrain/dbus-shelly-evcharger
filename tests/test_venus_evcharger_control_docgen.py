# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest

from venus_evcharger.control import (
    GENERATED_MARKDOWN_BLOCK_RENDERERS,
    replace_generated_markdown_block,
)


class TestVenusEvchargerControlDocgen(unittest.TestCase):
    def test_generated_markdown_blocks_match_documents(self) -> None:
        document_blocks = {
            "CONTROL_API.md": ("CONTROL_API_COMMAND_MATRIX", "CONTROL_API_GETTING_STARTED"),
            "API_OVERVIEW.md": ("API_OVERVIEW_CLIENT_STARTING_POINTS",),
            "README.md": ("README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED",),
        }

        for relative_path, block_names in document_blocks.items():
            with self.subTest(path=relative_path):
                document = pathlib.Path(relative_path).read_text(encoding="utf-8")
                for block_name in block_names:
                    begin_marker = f"<!-- BEGIN:{block_name} -->"
                    end_marker = f"<!-- END:{block_name} -->"
                    begin = document.index(begin_marker) + len(begin_marker)
                    end = document.index(end_marker)
                    self.assertEqual(
                        document[begin:end].strip(),
                        GENERATED_MARKDOWN_BLOCK_RENDERERS[block_name]().strip(),
                    )

    def test_replace_generated_markdown_block_is_no_op_for_rendered_content(self) -> None:
        original = "<!-- BEGIN:BLOCK -->\nold\n<!-- END:BLOCK -->"
        updated = replace_generated_markdown_block(original, "BLOCK", "new")
        self.assertEqual(updated, "<!-- BEGIN:BLOCK -->\nnew\n<!-- END:BLOCK -->")


if __name__ == "__main__":
    unittest.main()
