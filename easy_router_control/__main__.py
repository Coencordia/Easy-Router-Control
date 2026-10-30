"""Start de app: ``python -m easy_router_control``."""

from __future__ import annotations

import argparse
import socket
import threading
import webbrowser

from .server import make_server


def lan_ip() -> str | None:
    # Geen echte verbinding; dit vraagt alleen welk lokaal adres het OS zou kiezen.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
        except OSError:
            return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Websites blokkeren op je ASUS-router.")
    parser.add_argument("--port", type=int, default=8080, help="poort voor de webpagina (standaard 8080)")
    parser.add_argument("--lan", action="store_true",
                        help="ook bereikbaar maken voor andere apparaten in je thuisnetwerk (bijv. je telefoon)")
    parser.add_argument("--no-browser", action="store_true", help="de browser niet automatisch openen")
    args = parser.parse_args()

    extra: tuple[str, ...] = ()
    host = "127.0.0.1"
    ip = None
    if args.lan:
        host = "0.0.0.0"
        ip = lan_ip()
        extra = (ip, socket.gethostname()) if ip else (socket.gethostname(),)

    server = make_server(host, args.port, extra_hosts=extra)
    url = f"http://localhost:{args.port}/"
    print(f"Easy Router Control draait op {url}")
    if ip:
        print(f"Vanaf je telefoon of andere apparaten: http://{ip}:{args.port}/")
    print("Stoppen met Ctrl+C.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGestopt.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
