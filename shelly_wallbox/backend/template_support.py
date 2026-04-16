# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared HTTP/JSON helpers for template-backed meter and switch backends."""

from __future__ import annotations

import configparser
import json
from string import Template
from typing import Any, cast
from urllib.parse import urljoin

import requests


def load_template_config(config_path: str) -> configparser.ConfigParser:
    """Load one template backend config file."""
    parser = configparser.ConfigParser()
    read_files = parser.read(config_path)
    if not read_files:
        raise FileNotFoundError(config_path)
    return parser


def config_section(parser: configparser.ConfigParser, name: str) -> configparser.SectionProxy:
    """Return one named section or DEFAULT when absent."""
    return parser[name] if parser.has_section(name) else parser["DEFAULT"]


def normalize_http_method(value: object, default: str) -> str:
    """Return one supported HTTP method."""
    method = str(value).strip().upper() if value is not None else ""
    return method if method in {"GET", "POST", "PUT", "PATCH"} else default


def resolved_url(base_url: str, raw_url: object) -> str:
    """Return one absolute URL using the optional adapter base URL."""
    url = str(raw_url).strip() if raw_url is not None else ""
    if not url:
        return ""
    if "://" in url:
        return url
    if not base_url:
        raise ValueError(f"Relative URL '{url}' requires Adapter.BaseUrl")
    return urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))


def json_path_value(payload: dict[str, object], path: str) -> object:
    """Return one nested JSON value addressed by a dotted path."""
    current: object = payload
    for part in str(path).split("."):
        token = part.strip()
        if not token:
            continue
        if not isinstance(current, dict) or token not in current:
            raise ValueError(f"Missing response path '{path}'")
        current = current[token]
    return current


def render_json_payload(template_text: str | None, context: dict[str, str]) -> object | None:
    """Render one optional JSON body template."""
    if not template_text:
        return None
    rendered = Template(template_text).safe_substitute(context).strip()
    if not rendered:
        return None
    return cast(object, json.loads(rendered))


def _request_kwargs(url: str, timeout_seconds: float, payload: object | None) -> dict[str, object]:
    """Return requests kwargs for one template backend HTTP call."""
    kwargs: dict[str, object] = {
        "url": str(url),
        "timeout": float(timeout_seconds),
    }
    if payload is not None:
        kwargs["json"] = payload
    return kwargs


def _request_method_callable(session: Any, method: str) -> Any:
    """Return the bound requests-session method for one normalized HTTP verb."""
    normalized_method = str(method).strip().upper()
    if normalized_method == "GET":
        return session.get
    if normalized_method == "POST":
        return session.post
    if normalized_method == "PUT":
        return session.put
    if normalized_method == "PATCH":
        return session.patch
    raise ValueError(f"Unsupported template backend HTTP method '{method}'")


def _response_payload_dict(response: Any) -> dict[str, object]:
    """Return a dict payload from one HTTP response, or an empty dict otherwise."""
    response_payload = response.json()
    return cast(dict[str, object], response_payload) if isinstance(response_payload, dict) else {}


class TemplateHttpBackendBase:
    """Small shared HTTP client helper for template backends."""

    def __init__(self, service: object, timeout_seconds: float) -> None:
        self.service = service
        self.timeout_seconds = float(timeout_seconds)
        session = getattr(service, "session", None)
        self._session = cast(Any, session if session is not None else requests.Session())

    def _perform_request(
        self,
        method: str,
        url: str,
        *,
        context: dict[str, str] | None = None,
        json_template: str | None = None,
    ) -> dict[str, object]:
        """Perform one backend HTTP request and return a dict payload when available."""
        template_context = context or {}
        rendered_url = str(Template(url).safe_substitute(template_context))
        payload = render_json_payload(json_template, template_context)
        kwargs = _request_kwargs(rendered_url, self.timeout_seconds, payload)
        response = _request_method_callable(self._session, method)(**kwargs)
        response.raise_for_status()
        return _response_payload_dict(response)
