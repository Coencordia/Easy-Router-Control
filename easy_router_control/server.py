"""Lokale webserver: serveert de bedieningspagina en praat namens de browser met de router.

De browser kan de router niet zelf aanspreken (andere origin), dus deze server
houdt de routersessie bij en biedt een kleine JSON-API aan:

    POST /api/login    {host, username, password, https}
    POST /api/logout
    GET  /api/filter
    POST /api/filter   {enabled, rules: [{url, enabled}]}
"""

from __future__ import annotations

import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .asus import AsusRouter, AuthError, RouterError, UrlRule, normalize_domain

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.router: AsusRouter | None = None
        self.info: dict[str, str] = {}
        # Wordt in de pagina gezet en moet bij elke API-aanroep meekomen, zodat
        # andere websites in je browser deze server niet kunnen aansturen.
        self.csrf_token = secrets.token_urlsafe(32)
        self.router_factory = AsusRouter


def filter_payload(state: AppState, flt) -> dict:
    return {
        "enabled": flt.enabled,
        "rules": [r.to_dict() for r in flt.rules],
        "legacy": flt.legacy_format,
        "whitelist": flt.whitelist_mode,
        "router": state.info,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "EasyRouterControl"
    state: AppState
    allowed_hosts: set[str]

    def log_message(self, fmt, *args):  # alleen fouten loggen
        pass

    # -- Hulpfuncties -------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json; charset=utf-8")

    def _error(self, message: str, status: int) -> None:
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        # Beschermt tegen DNS-rebinding: alleen bekende hostnamen accepteren.
        return self.headers.get("Host", "").lower() in self.allowed_hosts

    def _authorized_api(self) -> bool:
        if not self._host_ok():
            self._error("Ongeldige host.", HTTPStatus.FORBIDDEN)
            return False
        if not secrets.compare_digest(self.headers.get("X-App-Token", ""), self.state.csrf_token):
            self._error("Ongeldige aanvraag. Herlaad de pagina.", HTTPStatus.FORBIDDEN)
            return False
        return True

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Aanvraag te groot.")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Ongeldige aanvraag.")
        return data

    def _with_router(self, fn) -> None:
        state = self.state
        with state.lock:
            if state.router is None:
                self._error("Niet ingelogd.", HTTPStatus.UNAUTHORIZED)
                return
            try:
                self._json(fn(state.router))
            except AuthError as exc:
                state.router = None
                self._error(str(exc), HTTPStatus.UNAUTHORIZED)
            except RouterError as exc:
                self._error(str(exc), HTTPStatus.BAD_GATEWAY)

    # -- Routes -------------------------------------------------------------

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            if not self._host_ok():
                self._send(HTTPStatus.FORBIDDEN, b"Ongeldige host", "text/plain")
                return
            page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            page = page.replace("__CSRF_TOKEN__", self.state.csrf_token)
            self._send(200, page.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/filter":
            if self._authorized_api():
                self._with_router(lambda r: filter_payload(self.state, r.get_url_filter()))
        elif self.path == "/api/session":
            if self._authorized_api():
                logged_in = self.state.router is not None
                self._json({"loggedIn": logged_in, "router": self.state.info if logged_in else None})
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Niet gevonden", "text/plain")

    def do_POST(self) -> None:
        if not self._authorized_api():
            return
        try:
            body = self._read_body()
        except ValueError as exc:
            self._error(str(exc) or "Ongeldige aanvraag.", HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/api/login":
            self._login(body)
        elif self.path == "/api/logout":
            with self.state.lock:
                if self.state.router:
                    self.state.router.logout()
                self.state.router = None
                self.state.info = {}
            self._json({"ok": True})
        elif self.path == "/api/filter":
            self._save_filter(body)
        else:
            self._error("Niet gevonden.", HTTPStatus.NOT_FOUND)

    def _login(self, body: dict) -> None:
        host = str(body.get("host") or "").strip()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not host or not username or not password:
            self._error("Vul routeradres, gebruikersnaam en wachtwoord in.", HTTPStatus.BAD_REQUEST)
            return
        router = self.state.router_factory(host, username, password, use_https=bool(body.get("https")))
        try:
            router.login()
            info = router.get_info()
        except AuthError as exc:
            self._error(str(exc), HTTPStatus.UNAUTHORIZED)
            return
        except RouterError as exc:
            self._error(str(exc), HTTPStatus.BAD_GATEWAY)
            return
        with self.state.lock:
            if self.state.router:
                self.state.router.logout()
            self.state.router = router
            self.state.info = info
        self._json({"ok": True, "router": info})

    def _save_filter(self, body: dict) -> None:
        try:
            rules: list[UrlRule] = []
            seen: set[str] = set()
            for item in body.get("rules") or []:
                url = normalize_domain(str(item.get("url", "")))
                if url not in seen:
                    seen.add(url)
                    rules.append(UrlRule(url=url, enabled=bool(item.get("enabled", True))))
        except (ValueError, AttributeError) as exc:
            self._error(str(exc), HTTPStatus.BAD_REQUEST)
            return
        enabled = bool(body.get("enabled"))

        def save(router: AsusRouter) -> dict:
            current = router.get_url_filter()
            # MAC-adressen van bestaande regels behouden (ingesteld via de routerpagina).
            macs = {r.url: r.mac for r in current.rules}
            for rule in rules:
                rule.mac = macs.get(rule.url, "ALL")
            return filter_payload(self.state, router.set_url_filter(enabled, rules, current.legacy_format))

        self._with_router(save)


def make_server(host: str = "127.0.0.1", port: int = 8080, state: AppState | None = None,
                extra_hosts: tuple[str, ...] = ()) -> ThreadingHTTPServer:
    state = state or AppState()
    names = ("localhost", "127.0.0.1", "[::1]", *extra_hosts)
    allowed = {f"{name.lower()}:{port}" for name in names}
    handler = type("BoundHandler", (Handler,), {"state": state, "allowed_hosts": allowed})
    return ThreadingHTTPServer((host, port), handler)
