# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.control import build_control_api_openapi_spec
from venus_evcharger.control import openapi


class TestVenusEvchargerControlOpenApi(unittest.TestCase):
    def test_string_schema_supports_optional_enum_and_default(self) -> None:
        self.assertEqual(openapi._string_schema(), {"type": "string"})
        self.assertEqual(
            openapi._string_schema(enum=("b", "a"), default="a"),
            {"type": "string", "enum": ["a", "b"], "default": "a"},
        )

    def test_boolean_schema_supports_optional_default(self) -> None:
        self.assertEqual(openapi._boolean_schema(), {"type": "boolean"})
        self.assertEqual(
            openapi._boolean_schema(default=True),
            {"type": "boolean", "default": True},
        )

    def test_integer_schema_supports_optional_minimum(self) -> None:
        self.assertEqual(openapi._integer_schema(), {"type": "integer"})
        self.assertEqual(
            openapi._integer_schema(minimum=0),
            {"type": "integer", "minimum": 0},
        )

    def test_number_schema_supports_optional_minimum(self) -> None:
        self.assertEqual(openapi._number_schema(), {"type": "number"})
        self.assertEqual(
            openapi._number_schema(minimum=0.0),
            {"type": "number", "minimum": 0.0},
        )

    def test_openapi_spec_exposes_expected_paths_security_and_schemas(self) -> None:
        spec = build_control_api_openapi_spec()

        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertEqual(spec["info"]["version"], "v1")
        self.assertEqual(spec["servers"][0]["url"], "http://127.0.0.1:8765")

        paths = spec["paths"]
        self.assertIn("/v1/openapi.json", paths)
        self.assertIn("/v1/capabilities", paths)
        self.assertIn("/v1/control/command", paths)
        self.assertIn("/v1/events", paths)
        self.assertIn("/v1/state/config-effective", paths)
        self.assertIn("/v1/state/dbus-diagnostics", paths)
        self.assertEqual(
            paths["/v1/capabilities"]["get"]["security"],
            [{"BearerAuth": []}],
        )
        self.assertIn("requestBody", paths["/v1/control/command"]["post"])
        self.assertEqual(
            paths["/v1/events"]["get"]["responses"]["200"]["content"]["application/x-ndjson"]["schema"]["$ref"],
            "#/components/schemas/ControlEvent",
        )

        schemas = spec["components"]["schemas"]
        self.assertIn("ControlCapabilities", schemas)
        self.assertIn("ControlEvent", schemas)
        self.assertIn("ControlCommandResponse", schemas)
        self.assertIn("ControlVersioning", schemas)
        self.assertIn("StateConfigEffective", schemas)
        self.assertIn("StateHealth", schemas)
        self.assertIn("StateDbusDiagnostics", schemas)
        self.assertIn("/v1/openapi.json", schemas["ControlCapabilities"]["properties"]["endpoints"]["items"]["enum"])
        self.assertEqual(
            schemas["ControlCapabilities"]["properties"]["versioning"]["$ref"],
            "#/components/schemas/ControlVersioning",
        )
        self.assertIn(
            "/v1/events",
            schemas["ControlVersioning"]["properties"]["experimental_endpoints"]["items"]["enum"],
        )

        error_schema = schemas["ControlError"]
        self.assertIn("unauthorized", error_schema["properties"]["code"]["enum"])
        self.assertIn("idempotency_conflict", error_schema["properties"]["code"]["enum"])
        self.assertEqual(
            spec["components"]["securitySchemes"]["BearerAuth"]["scheme"],
            "bearer",
        )
