"""Home Assistant Core API client (works through the Supervisor proxy)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

_LOGGER = logging.getLogger(__name__)


class HassError(RuntimeError):
    pass


class HassClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
        if not self.configured:
            raise HassError("Home Assistant connection is not configured.")

        url = f"{self.base_url}/api/{path.lstrip('/')}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode() or "null"
        except urllib.error.HTTPError as exc:
            raise HassError(
                f"Home Assistant returned {exc.code} for {method} {path}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise HassError(f"Could not reach Home Assistant: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    # ------------------------------------------------------------------ API
    def ping(self) -> bool:
        try:
            self._request("GET", "/")
            return True
        except HassError:
            return False

    def state(self, entity_id: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/states/{entity_id}")
        except HassError:
            return None

    def call_service(self, domain: str, service: str, **data: Any) -> Any:
        return self._request("POST", f"/services/{domain}/{service}", data)

    def fire_event(self, event_type: str, **data: Any) -> Any:
        return self._request("POST", f"/events/{event_type}", data)

    def entities(self, prefix: str = "") -> list[dict[str, Any]]:
        try:
            states = self._request("GET", "/states") or []
        except HassError:
            return []
        return [
            {
                "entity_id": s["entity_id"],
                "state": s.get("state"),
                "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
            }
            for s in states
            if not prefix or s["entity_id"].startswith(prefix)
        ]
