"""DoorBot add-on HTTP server.

Pure standard library on purpose: the add-on has no third-party dependencies,
so it starts fast, has a tiny attack surface, and the identical code can be run
straight from a checkout on a laptop for development.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import posixpath
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .codes import CodeError, CodeStore, suggest_code
from .config import Config
from .controller import BackendError, LockController, build_backend
from .db import Database
from .hass import HassClient
from .keypad import RESULT_ACCEPTED, KeypadWatcher

_LOGGER = logging.getLogger("doorbot")

Handler = Callable[["Request"], Any]


class Request:
    def __init__(self, method: str, path: str, query: dict[str, list[str]], body: Any) -> None:
        self.method = method
        self.path = path
        self.query = query
        self.body = body if isinstance(body, dict) else {}
        self.raw_body = body

    def param(self, key: str, default: str = "") -> str:
        return self.query.get(key, [default])[0]


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        self.routes.append((method, pattern, handler))

    def match(self, method: str, path: str) -> tuple[Handler, dict[str, str]] | None:
        for route_method, pattern, handler in self.routes:
            if route_method != method:
                continue
            params = _match_pattern(pattern, path)
            if params is not None:
                return handler, params
        return None


def _match_pattern(pattern: str, path: str) -> dict[str, str] | None:
    p_parts = [p for p in pattern.strip("/").split("/") if p != ""]
    a_parts = [p for p in path.strip("/").split("/") if p != ""]
    if len(p_parts) != len(a_parts):
        return None
    params: dict[str, str] = {}
    for expected, actual in zip(p_parts, a_parts):
        if expected.startswith("{") and expected.endswith("}"):
            params[expected[1:-1]] = unquote(actual)
        elif expected != actual:
            return None
    return params


class DoorBotApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = Database(config.db_path)
        self.hass = HassClient(config.get("hass_url"), config.get("hass_token"))
        self.codes = CodeStore(self.db)
        self.keypad = KeypadWatcher(self.db)
        self.controller = LockController(
            self.db, build_backend(self.db, config.options, self.hass), config.options
        )
        self.controller.on_event(self._forward_to_hass)
        self._attempts: dict[str, list[float]] = {}
        self._attempt_lock = threading.Lock()
        self.router = Router()
        self._register_routes()

    # ------------------------------------------------------------ HA bridge
    def _forward_to_hass(self, kind: str, data: dict[str, Any]) -> None:
        if not self.hass.configured:
            return
        try:
            self.hass.fire_event("doorbot_event", kind=kind, **data)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not forward %s to Home Assistant", kind)

    # -------------------------------------------------------------- routing
    def _register_routes(self) -> None:
        r = self.router
        r.add("GET", "/api/status", self.api_status)
        r.add("GET", "/api/info", self.api_info)
        r.add("GET", "/api/events", self.api_events)

        r.add("POST", "/api/lock", lambda rq: self.controller.lock(actor="ui"))
        r.add("POST", "/api/unlock", lambda rq: self.controller.unlock(actor="ui"))
        r.add("POST", "/api/toggle", lambda rq: self.controller.toggle(actor="ui"))
        r.add("POST", "/api/stop", self.api_stop)

        r.add("GET", "/api/calibration", lambda rq: self.db.get_calibration())
        r.add("POST", "/api/calibration", self.api_save_calibration)
        r.add("POST", "/api/calibration/capture", self.api_capture)
        r.add("POST", "/api/calibration/jog", self.api_jog)
        r.add("POST", "/api/calibration/goto", self.api_goto)
        r.add("POST", "/api/calibration/torque", self.api_torque)
        r.add("POST", "/api/calibration/reset", lambda rq: self.controller.reset_calibration())

        r.add("GET", "/api/codes", lambda rq: {"codes": self.codes.list_codes()})
        r.add("POST", "/api/codes", self.api_create_code)
        r.add("PUT", "/api/codes/{code_id}", self.api_update_code)
        r.add("DELETE", "/api/codes/{code_id}", self.api_delete_code)
        r.add("POST", "/api/codes/{code_id}/reset", self.api_reset_code)
        r.add("GET", "/api/codes/suggest", lambda rq: {"code": suggest_code(int(rq.param("length", "6")))})
        r.add("POST", "/api/verify", self.api_verify)

        r.add("GET", "/api/keypad", lambda rq: self.keypad.snapshot())
        r.add("POST", "/api/keypad/settings", self.api_keypad_settings)
        r.add("POST", "/api/keypad/credentials", self.api_keypad_credentials)
        r.add("DELETE", "/api/keypad/credentials/{key}", self.api_keypad_credential_delete)
        r.add("POST", "/api/keypad/event", self.api_keypad_event)

        r.add("POST", "/api/dev/jam", self.api_dev_jam)

    # ---------------------------------------------------------- API methods
    def api_status(self, rq: Request) -> dict[str, Any]:
        status = self.controller.status()
        status["keypad"] = self.keypad.snapshot()
        return status

    def api_info(self, rq: Request) -> dict[str, Any]:
        return {
            "version": VERSION,
            "backend": self.controller.backend.name,
            "hass_configured": self.hass.configured,
            "hass_reachable": self.hass.ping() if self.hass.configured else False,
            "options": {
                k: v
                for k, v in self.config.options.items()
                if k != "hass_token"
            },
            "code_count": len(self.codes.list_codes()),
        }

    def api_events(self, rq: Request) -> dict[str, Any]:
        return {"events": self.db.events(int(rq.param("limit", "80")))}

    def api_stop(self, rq: Request) -> dict[str, Any]:
        self.controller.backend.stop()
        return self.controller.status()

    def api_save_calibration(self, rq: Request) -> dict[str, Any]:
        return self.controller.save_calibration(rq.body)

    def api_capture(self, rq: Request) -> dict[str, Any]:
        return self.controller.capture(str(rq.body.get("which", "")))

    def api_jog(self, rq: Request) -> dict[str, Any]:
        try:
            delta = int(rq.body.get("delta", 0))
        except (TypeError, ValueError) as exc:
            raise ApiError("Jog delta must be a whole number.") from exc
        return self.controller.jog(max(-2048, min(2048, delta)))

    def api_goto(self, rq: Request) -> dict[str, Any]:
        try:
            position = int(rq.body.get("position"))
        except (TypeError, ValueError) as exc:
            raise ApiError("Position must be a whole number.") from exc
        return self.controller.goto(position)

    def api_torque(self, rq: Request) -> dict[str, Any]:
        return self.controller.set_torque(bool(rq.body.get("enabled", True)))

    def api_create_code(self, rq: Request) -> dict[str, Any]:
        return self.codes.create(rq.body)

    def api_update_code(self, rq: Request, code_id: str = "") -> dict[str, Any]:
        return self.codes.update(int(code_id), rq.body)

    def api_delete_code(self, rq: Request, code_id: str = "") -> dict[str, Any]:
        self.codes.delete(int(code_id))
        return {"deleted": True}

    def api_reset_code(self, rq: Request, code_id: str = "") -> dict[str, Any]:
        return {"code": self.codes.reset_usage(int(code_id))}

    def api_verify(self, rq: Request) -> dict[str, Any]:
        """Validate a PIN and, if it passes, actuate the lock.

        This is the endpoint a keypad, an NFC reader, a dashboard keypad card or
        an automation calls. It is rate limited per source.
        """
        source = str(rq.body.get("source", "api"))[:32]
        code = str(rq.body.get("code", ""))

        if self._rate_limited(source):
            self.db.log("blocked", "Too many failed attempts - locked out", actor=source)
            raise ApiError("Too many failed attempts. Try again later.", 429)

        result = self.codes.check(code)
        if not result["allowed"]:
            self._record_failure(source)
            self.db.log(
                "denied",
                f"Rejected PIN ({result['reason']})",
                actor=source,
                reason=result["reason"],
                code_id=result.get("code_id"),
            )
            self._forward_to_hass("denied", {"reason": result["reason"], "source": source})
            return {"allowed": False, "reason": result["reason"]}

        self._clear_failures(source)
        self.codes.register_use(int(result["code_id"]))
        self.db.log(
            "granted",
            f"Accepted PIN for '{result['name']}'",
            actor=source,
            code_id=result["code_id"],
            duress=result.get("duress", False),
        )

        action = str(rq.body.get("action", "unlock"))
        actor = f"code:{result['name']}"
        try:
            if action == "toggle":
                self.controller.toggle(actor=actor)
            elif action != "none":
                self.controller.unlock(actor=actor)
        except BackendError as exc:
            raise ApiError(str(exc), 409) from exc

        self._forward_to_hass(
            "granted",
            {
                "name": result["name"],
                "code_id": result["code_id"],
                "source": source,
                "duress": result.get("duress", False),
            },
        )
        return {
            "allowed": True,
            "name": result["name"],
            "duress": result.get("duress", False),
            "status": self.controller.status(),
        }

    def api_keypad_settings(self, rq: Request) -> dict[str, Any]:
        return self.keypad.save_settings(rq.body)

    def api_keypad_credentials(self, rq: Request) -> dict[str, Any]:
        try:
            saved = self.keypad.save_credential(rq.body)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        return {"credential": saved, "keypad": self.keypad.snapshot()}

    def api_keypad_credential_delete(self, rq: Request, key: str = "") -> dict[str, Any]:
        try:
            self.keypad.delete_credential(key)
        except KeyError as exc:
            raise ApiError("No such credential.", 404) from exc
        return {"keypad": self.keypad.snapshot()}

    def api_keypad_event(self, rq: Request) -> dict[str, Any]:
        """Called by the ESP32 bridge with a decrypted keypad unlock frame.

        The keypad has already authenticated the credential over its encrypted
        channel, so the payload identifies *which* credential was used rather
        than carrying any secret:

            {"method": "fingerprint", "slot": 0, "keypad": "Front door"}
        """
        body = rq.body or {}
        if "method" not in body:
            raise ApiError("method (pin|nfc|fingerprint|face) is required.")
        try:
            slot = int(body.get("slot", body.get("index")))
        except (TypeError, ValueError) as exc:
            raise ApiError("slot (the credential index) is required.") from exc

        battery = body.get("battery")
        outcome = self.keypad.ingest(
            body["method"],
            slot,
            keypad_name=str(body.get("keypad", "")),
            battery=int(battery) if battery is not None else None,
            address=str(body.get("address", "")),
        )

        settings = outcome["settings"]
        actor = outcome["name"] or f"keypad:{outcome['method']}:{outcome['slot']}"
        acted = False
        if outcome["result"] == RESULT_ACCEPTED and settings["action"] != "notify":
            try:
                if settings["action"] == "toggle":
                    self.controller.toggle(actor=actor)
                else:
                    self.controller.unlock(actor=actor)
                acted = True
            except BackendError as exc:
                raise ApiError(str(exc), 409) from exc

        self._forward_to_hass(
            "keypad",
            {
                "result": outcome["result"],
                "method": outcome["method"],
                "slot": outcome["slot"],
                "name": outcome["name"],
                "known": outcome["known"],
                "duress": outcome["duress"],
                "acted": acted,
            },
        )
        outcome["acted"] = acted
        outcome["status"] = self.controller.status()
        return outcome

    def api_dev_jam(self, rq: Request) -> dict[str, Any]:
        backend = self.controller.backend
        if not hasattr(backend, "jam_next_move"):
            raise ApiError("Jam simulation is only available with the mock backend.")
        backend.jam_next_move = bool(rq.body.get("enabled", True))  # type: ignore[attr-defined]
        return {"jam_next_move": backend.jam_next_move}  # type: ignore[attr-defined]

    # -------------------------------------------------------- rate limiting
    def _rate_limited(self, source: str) -> bool:
        limit = int(self.config.get("max_failed_attempts") or 0)
        window = int(self.config.get("lockout_seconds") or 0)
        if limit <= 0:
            return False
        with self._attempt_lock:
            now = time.time()
            hits = [t for t in self._attempts.get(source, []) if now - t < window]
            self._attempts[source] = hits
            return len(hits) >= limit

    def _record_failure(self, source: str) -> None:
        with self._attempt_lock:
            self._attempts.setdefault(source, []).append(time.time())

    def _clear_failures(self, source: str) -> None:
        with self._attempt_lock:
            self._attempts.pop(source, None)


VERSION = "0.1.0"


class DoorBotHandler(BaseHTTPRequestHandler):
    server_version = f"DoorBot/{VERSION}"
    app: DoorBotApp

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        _LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------- dispatch
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        # The Supervisor strips the /api/hassio_ingress/<token> prefix before
        # proxying, so the path arrives here already relative to the add-on.
        path = parsed.path or "/"
        query = parse_qs(parsed.query)

        if not path.startswith("/api/"):
            self._serve_static(path)
            return

        body: Any = {}
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode() or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json({"error": "Request body must be valid JSON."}, 400)
                return

        match = self.app.router.match(method, path)
        if match is None:
            self._send_json({"error": f"No route for {method} {path}"}, 404)
            return

        handler, params = match
        request = Request(method, path, query, body)
        try:
            result = handler(request, **params) if params else handler(request)
            self._send_json(result if result is not None else {"ok": True})
        except ApiError as exc:
            self._send_json({"error": exc.message}, exc.status)
        except CodeError as exc:
            self._send_json({"error": str(exc)}, 400)
        except BackendError as exc:
            self._send_json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Unhandled error on %s %s", method, path)
            self._send_json({"error": f"Something went wrong: {exc}"}, 500)

    # --------------------------------------------------------------- output
    def _send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        web_dir: Path = self.app.config.web_dir
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        rel = posixpath.normpath(rel).lstrip("./")
        target = (web_dir / rel).resolve()

        if not str(target).startswith(str(web_dir.resolve())) or not target.is_file():
            target = web_dir / "index.html"
            if not target.is_file():
                self._send_json({"error": "UI not found"}, 404)
                return

        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def serve(config: Config | None = None) -> None:
    config = config or Config.load()
    logging.basicConfig(
        level=getattr(logging, str(config.get("log_level", "info")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = DoorBotApp(config)
    DoorBotHandler.app = app

    httpd = ThreadingHTTPServer(("0.0.0.0", config.port), DoorBotHandler)
    _LOGGER.info(
        "DoorBot %s listening on port %s (backend=%s, data=%s)",
        VERSION,
        config.port,
        app.controller.backend.name,
        config.data_dir,
    )
    app.db.log("startup", f"DoorBot {VERSION} started ({app.controller.backend.name} backend)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _LOGGER.info("Shutting down")
    finally:
        httpd.server_close()
