# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest

from venus_evcharger.control import (
    CONTROL_API_COMMAND_REFERENCE,
    CONTROL_API_COMMAND_SCOPE_REQUIREMENTS,
    build_control_api_openapi_spec,
    render_control_api_command_matrix_markdown,
)


class TestVenusEvchargerControlReference(unittest.TestCase):
    def test_rendered_command_matrix_matches_documented_block(self) -> None:
        document = pathlib.Path("CONTROL_API.md").read_text(encoding="utf-8")
        begin_marker = "<!-- BEGIN:CONTROL_API_COMMAND_MATRIX -->"
        end_marker = "<!-- END:CONTROL_API_COMMAND_MATRIX -->"

        begin = document.index(begin_marker) + len(begin_marker)
        end = document.index(end_marker)
        documented_block = document[begin:end].strip()

        self.assertEqual(documented_block, render_control_api_command_matrix_markdown())

    def test_reference_scopes_match_shared_scope_contract(self) -> None:
        self.assertEqual(
            {item.name: item.required_scope for item in CONTROL_API_COMMAND_REFERENCE},
            CONTROL_API_COMMAND_SCOPE_REQUIREMENTS,
        )

    def test_reference_required_fields_match_named_openapi_request_schemas(self) -> None:
        spec = build_control_api_openapi_spec()
        schemas = spec["components"]["schemas"]

        for item in CONTROL_API_COMMAND_REFERENCE:
            matching_named_schemas = []
            for schema_name, schema in schemas.items():
                properties = schema.get("properties", {})
                name_property = properties.get("name", {})
                if name_property.get("const") != item.name:
                    continue
                matching_named_schemas.append(schema)

            self.assertTrue(matching_named_schemas, msg=f"Missing named request schema for {item.name}")

            for schema in matching_named_schemas:
                self.assertTrue(
                    set(item.required_fields).issubset(set(schema.get("required", ()))),
                    msg=f"Schema for {item.name} does not include documented required fields.",
                )

    def test_reference_command_names_match_openapi_capabilities_enum(self) -> None:
        spec = build_control_api_openapi_spec()
        capability_names = (
            spec["components"]["schemas"]["ControlCapabilities"]["properties"]["command_names"]["items"]["enum"]
        )

        self.assertEqual(
            sorted(item.name for item in CONTROL_API_COMMAND_REFERENCE),
            sorted(capability_names),
        )


if __name__ == "__main__":
    unittest.main()
