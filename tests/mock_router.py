"""Nagebootste ASUS-router voor tests en om de app zonder echte router te proberen.

Start los met ``python -m tests.mock_router`` (adres 127.0.0.1:8081, admin/admin).
"""

import base64
import json
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockRouter:
    def __init__(self, username="admin", password="admin"):
        self.credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.nvram = {
            "productid": "RT-AX86U", "firmver": "3.0.0.4", "buildno": "388", "extendno": "24243",
            "url_enable_x": "0", "url_rulelist": "", "url_mode_x": "0",
        }
        self.tokens: set[str] = set()
        self.applied_services: list[str] = []
        router = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body, ctype="application/json"):
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authed(self):
                cookie = self.headers.get("Cookie", "")
                return any(c.strip().removeprefix("asus_token=") in router.tokens
                           for c in cookie.split(";") if c.strip().startswith("asus_token="))

            def do_GET(self):
                if self.path == "/Logout.asp":
                    router.tokens.clear()
                self._send("<html></html>", "text/html")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode()))
                if self.path == "/login.cgi":
                    if form.get("login_authorization") == router.credentials:
                        token = secrets.token_hex(16)
                        router.tokens.add(token)
                        self._send(json.dumps({"asus_token": token}))
                    else:
                        self._send(json.dumps({"error_status": "3"}))
                    return
                if not self._authed():
                    # Echte routers sturen bij een verlopen sessie een HTML-pagina.
                    self._send("<html><script>top.location.href='/Main_Login.asp';</script></html>",
                               "text/html")
                    return
                if self.path == "/appGet.cgi":
                    keys = [h[len("nvram_get("):-1] for h in form.get("hook", "").split(";")]
                    # De router codeert < en > als &#60 en &#62.
                    out = {k: router.nvram.get(k, "").replace("<", "&#60").replace(">", "&#62")
                           for k in keys}
                    self._send(json.dumps(out))
                elif self.path == "/applyapp.cgi":
                    for k, v in form.items():
                        if k not in ("action_mode", "rc_service"):
                            router.nvram[k] = v
                    router.applied_services.append(form.get("rc_service", ""))
                    self._send(json.dumps({"modify": "1", "run_service": form.get("rc_service", "")}))
                else:
                    self._send("{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    mock = MockRouter()
    mock.server.server_close()
    mock.server = ThreadingHTTPServer(("127.0.0.1", 8081), mock.server.RequestHandlerClass)
    print("Nep-router draait op 127.0.0.1:8081 (gebruiker admin, wachtwoord admin)")
    mock.server.serve_forever()
