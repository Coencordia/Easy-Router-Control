"""Client voor de webinterface van ASUS-routers (Asuswrt en Asuswrt-Merlin).

Gebruikt dezelfde HTTP-endpoints als de officiële ASUS Router-app:

* ``/login.cgi``    inloggen, levert een ``asus_token`` op
* ``/appGet.cgi``   NVRAM-instellingen uitlezen
* ``/applyapp.cgi`` instellingen opslaan en een service herstarten
* ``/Logout.asp``   uitloggen

Het URL-filter staat in de NVRAM-variabelen ``url_enable_x`` (aan/uit) en
``url_rulelist``. Sinds firmware 3.0.0.4.384 heeft elke regel de vorm
``<ingeschakeld>MAC>url`` (bijv. ``<1>ALL>tiktok.com``); oudere firmware
gebruikt simpelweg ``<url``.
"""

from __future__ import annotations

import base64
import html
import json
import re
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# De router geeft een token terug aan clients die zich als de ASUS-app melden.
USER_AGENT = "asusrouter-Android-DUTUtil-1.0.0.245"

LOGIN_ERRORS = {
    "1": "Inloggen geweigerd door de router.",
    "2": "Er is al iemand anders ingelogd op de router. Log daar eerst uit.",
    "3": "Onjuiste gebruikersnaam of wachtwoord.",
    "7": "Te veel mislukte pogingen. Wacht een paar minuten en probeer opnieuw.",
    "8": "Te veel mislukte pogingen. Wacht een paar minuten en probeer opnieuw.",
    "10": "De router vraagt om een captcha. Log één keer in via de gewone "
          "webinterface van de router en probeer het daarna opnieuw.",
}

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)*[a-z0-9-]{1,63}$")


class RouterError(Exception):
    """Algemene fout bij communicatie met de router."""


class AuthError(RouterError):
    """Inloggen is mislukt of de sessie is verlopen."""


@dataclass
class UrlRule:
    url: str
    enabled: bool = True
    mac: str = "ALL"

    def to_dict(self) -> dict:
        return {"url": self.url, "enabled": self.enabled}


@dataclass
class UrlFilter:
    enabled: bool
    rules: list[UrlRule] = field(default_factory=list)
    legacy_format: bool = False
    whitelist_mode: bool = False


def decode_nvram(value: str) -> str:
    """De router codeert ``<`` en ``>`` soms als ``&#60`` / ``&#62`` (zonder ';')."""
    value = value.replace("&#60", "<").replace("&#62", ">")
    return html.unescape(value)


def parse_rulelist(raw: str) -> tuple[list[UrlRule], bool | None]:
    """Geeft de regels terug plus of het oude formaat gebruikt wordt (None = onbekend)."""
    rules: list[UrlRule] = []
    legacy: bool | None = None
    for entry in decode_nvram(raw).split("<"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(">")
        if len(parts) >= 3:
            legacy = False
            rules.append(UrlRule(url=parts[2], enabled=parts[0] != "0", mac=parts[1] or "ALL"))
        else:
            if legacy is None:
                legacy = True
            rules.append(UrlRule(url=parts[-1]))
    return rules, legacy


def serialize_rulelist(rules: list[UrlRule], legacy: bool) -> str:
    if legacy:
        # Het oude formaat kent geen uitgeschakelde regels.
        return "".join(f"<{r.url}" for r in rules if r.enabled)
    return "".join(f"<{1 if r.enabled else 0}>{r.mac}>{r.url}" for r in rules)


def normalize_domain(value: str) -> str:
    """Maakt van invoer als ``https://www.TikTok.com/foo`` gewoon ``tiktok.com``."""
    value = value.strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = re.split(r"[/?#]", value, maxsplit=1)[0]
    value = value.rsplit("@", 1)[-1].split(":", 1)[0].strip(".")
    if value.startswith("www."):
        value = value[4:]
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"'{value}' is geen geldig domein.") from None
    if not _DOMAIN_RE.match(value) or "." not in value:
        raise ValueError(f"'{value}' is geen geldig domein (bijv. tiktok.com).")
    return value


class AsusRouter:
    def __init__(self, host: str, username: str, password: str,
                 use_https: bool = False, verify_tls: bool = False, timeout: float = 10):
        host = host.strip().rstrip("/")
        if "://" in host:
            scheme, host = host.split("://", 1)
            use_https = scheme.lower() == "https"
        self.base_url = f"{'https' if use_https else 'http'}://{host}"
        self.username = username
        self._password = password
        self.timeout = timeout
        self.token: str | None = None
        self._lock = threading.Lock()

        handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler({})]
        if use_https:
            ctx = ssl.create_default_context()
            if not verify_tls:
                # Routers gebruiken standaard een zelfondertekend certificaat.
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)

    # -- HTTP ---------------------------------------------------------------

    def _post(self, path: str, data: dict[str, str]) -> str:
        req = urllib.request.Request(
            self.base_url + path,
            data=urllib.parse.urlencode(data).encode(),
            headers={
                "User-Agent": USER_AGENT,
                "Referer": self.base_url + "/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if self.token:
            req.add_header("Cookie", f"asus_token={self.token}")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthError("Sessie verlopen.") from exc
            raise RouterError(f"De router gaf een foutmelding (HTTP {exc.code}).") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RouterError(
                f"Kan de router op {self.base_url} niet bereiken. "
                "Controleer het adres en of je met het thuisnetwerk verbonden bent."
            ) from exc

    @staticmethod
    def _json(text: str) -> dict:
        try:
            data = json.loads(text)
        except ValueError:
            # Bij een verlopen sessie stuurt de router een HTML-pagina terug.
            raise AuthError("Sessie verlopen.") from None
        if not isinstance(data, dict):
            raise RouterError("Onverwacht antwoord van de router.")
        return data

    # -- Sessie -------------------------------------------------------------

    def login(self) -> None:
        with self._lock:
            self.token = None
            auth = base64.b64encode(f"{self.username}:{self._password}".encode()).decode()
            text = self._post("/login.cgi", {"login_authorization": auth})
            try:
                data = json.loads(text)
            except ValueError:
                raise RouterError("Dit lijkt geen ASUS-router te zijn (onverwacht antwoord).") from None
            token = data.get("asus_token") if isinstance(data, dict) else None
            if not token:
                code = str(data.get("error_status", "")) if isinstance(data, dict) else ""
                raise AuthError(LOGIN_ERRORS.get(code, f"Inloggen mislukt (foutcode {code or '?'})."))
            self.token = token

    def logout(self) -> None:
        if not self.token:
            return
        try:
            req = urllib.request.Request(
                self.base_url + "/Logout.asp",
                headers={"User-Agent": USER_AGENT, "Cookie": f"asus_token={self.token}"},
            )
            self._opener.open(req, timeout=self.timeout).close()
        except (urllib.error.URLError, OSError):
            pass
        finally:
            self.token = None

    def _call(self, fn):
        """Voert ``fn`` uit en logt één keer opnieuw in als de sessie verlopen is."""
        if not self.token:
            self.login()
        try:
            return fn()
        except AuthError:
            self.login()
            return fn()

    # -- NVRAM --------------------------------------------------------------

    def get_nvram(self, *keys: str) -> dict[str, str]:
        hook = ";".join(f"nvram_get({k})" for k in keys)
        return self._call(lambda: self._json(self._post("/appGet.cgi", {"hook": hook})))

    def apply(self, values: dict[str, str], service: str) -> None:
        payload = {"action_mode": "apply", "rc_service": service, **values}
        self._call(lambda: self._json(self._post("/applyapp.cgi", payload)))

    def get_info(self) -> dict[str, str]:
        data = self.get_nvram("productid", "firmver", "buildno", "extendno")
        version = ".".join(v for v in (data.get("firmver"), data.get("buildno")) if v)
        if data.get("extendno"):
            version += f"_{data['extendno']}"
        return {"model": data.get("productid", "ASUS-router"), "firmware": version}

    # -- URL-filter ---------------------------------------------------------

    def get_url_filter(self) -> UrlFilter:
        data = self.get_nvram("url_enable_x", "url_rulelist", "url_mode_x", "buildno")
        rules, legacy = parse_rulelist(data.get("url_rulelist", ""))
        if legacy is None:
            build = re.match(r"\d+", data.get("buildno", ""))
            legacy = bool(build) and int(build.group()) < 384
        return UrlFilter(
            enabled=data.get("url_enable_x") == "1",
            rules=rules,
            legacy_format=legacy,
            whitelist_mode=data.get("url_mode_x") == "1",
        )

    def set_url_filter(self, enabled: bool, rules: list[UrlRule], legacy: bool) -> UrlFilter:
        rulelist = serialize_rulelist(rules, legacy)
        self.apply(
            {"url_enable_x": "1" if enabled else "0", "url_rulelist": rulelist},
            service="restart_firewall",
        )
        result = self.get_url_filter()
        if result.enabled != enabled or serialize_rulelist(result.rules, legacy) != rulelist:
            raise RouterError("De router heeft de wijziging niet overgenomen. Probeer het opnieuw.")
        return result
