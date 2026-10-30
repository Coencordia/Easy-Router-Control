import json
import threading
import unittest
import urllib.error
import urllib.request

from easy_router_control.asus import (
    AsusRouter, AuthError, UrlRule, normalize_domain, parse_rulelist, serialize_rulelist,
)
from easy_router_control.server import AppState, make_server
from tests.mock_router import MockRouter


class RulelistTests(unittest.TestCase):
    def test_parse_new_format(self):
        rules, legacy = parse_rulelist("&#601&#62ALL&#62tiktok.com&#600&#62AA:BB:CC:DD:EE:FF&#62x.com")
        self.assertFalse(legacy)
        self.assertEqual([(r.url, r.enabled, r.mac) for r in rules],
                         [("tiktok.com", True, "ALL"), ("x.com", False, "AA:BB:CC:DD:EE:FF")])

    def test_parse_legacy_format(self):
        rules, legacy = parse_rulelist("<tiktok.com<x.com")
        self.assertTrue(legacy)
        self.assertEqual([r.url for r in rules], ["tiktok.com", "x.com"])

    def test_parse_empty(self):
        self.assertEqual(parse_rulelist(""), ([], None))

    def test_roundtrip(self):
        rules = [UrlRule("a.com"), UrlRule("b.com", enabled=False, mac="AA:BB:CC:DD:EE:FF")]
        self.assertEqual(serialize_rulelist(rules, False), "<1>ALL>a.com<0>AA:BB:CC:DD:EE:FF>b.com")
        self.assertEqual(serialize_rulelist(rules, True), "<a.com")

    def test_normalize_domain(self):
        self.assertEqual(normalize_domain(" https://www.TikTok.com/@user?x=1 "), "tiktok.com")
        self.assertEqual(normalize_domain("m.youtube.com:443"), "m.youtube.com")
        self.assertEqual(normalize_domain("bücher.de"), "xn--bcher-kva.de")
        for bad in ("", "tiktok", "evil<1>ALL>x.com", "a b.com"):
            with self.assertRaises(ValueError):
                normalize_domain(bad)


class RouterClientTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockRouter().start()

    def tearDown(self):
        self.mock.stop()

    def test_wrong_password(self):
        with self.assertRaisesRegex(AuthError, "Onjuiste"):
            AsusRouter(self.mock.host, "admin", "fout").login()

    def test_set_and_get_filter(self):
        router = AsusRouter(self.mock.host, "admin", "admin")
        router.login()
        self.assertEqual(router.get_info(), {"model": "RT-AX86U", "firmware": "3.0.0.4.388_24243"})
        result = router.set_url_filter(True, [UrlRule("tiktok.com")], legacy=False)
        self.assertTrue(result.enabled)
        self.assertEqual(self.mock.nvram["url_rulelist"], "<1>ALL>tiktok.com")
        self.assertEqual(self.mock.applied_services, ["restart_firewall"])

    def test_relogin_after_session_expiry(self):
        router = AsusRouter(self.mock.host, "admin", "admin")
        router.login()
        self.mock.tokens.clear()
        self.assertFalse(router.get_url_filter().enabled)

    def test_unreachable(self):
        from easy_router_control.asus import RouterError
        with self.assertRaisesRegex(RouterError, "niet bereiken"):
            AsusRouter("127.0.0.1:1", "admin", "admin", timeout=2).login()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockRouter().start()
        self.state = AppState()
        self.server = make_server("127.0.0.1", 0, self.state)
        # make_server kent de poort pas na binden; opnieuw opbouwen met de echte poort.
        port = self.server.server_address[1]
        self.server.server_close()
        self.server = make_server("127.0.0.1", port, self.state)
        self.base = f"http://127.0.0.1:{port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.mock.stop()

    def call(self, path, body=None, token=None):
        req = urllib.request.Request(self.base + path, headers={
            "X-App-Token": token if token is not None else self.state.csrf_token,
            "Content-Type": "application/json",
        })
        if body is not None:
            req.data = json.dumps(body).encode()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_page_contains_token(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        page = opener.open(self.base + "/").read().decode()
        self.assertIn(self.state.csrf_token, page)

    def test_rejects_missing_token(self):
        status, _ = self.call("/api/session", token="")
        self.assertEqual(status, 403)

    def test_full_flow(self):
        self.assertEqual(self.call("/api/filter")[0], 401)
        status, data = self.call("/api/login", {"host": self.mock.host, "username": "admin", "password": "fout"})
        self.assertEqual((status, data["error"]), (401, "Onjuiste gebruikersnaam of wachtwoord."))

        status, data = self.call("/api/login", {"host": self.mock.host, "username": "admin", "password": "admin"})
        self.assertEqual(status, 200)
        self.assertEqual(data["router"]["model"], "RT-AX86U")

        status, data = self.call("/api/filter", {"enabled": True, "rules": [
            {"url": "https://www.TikTok.com/", "enabled": True},
            {"url": "tiktok.com", "enabled": True},
            {"url": "youtube.com", "enabled": False},
        ]})
        self.assertEqual(status, 200)
        self.assertEqual(data["rules"], [{"url": "tiktok.com", "enabled": True},
                                         {"url": "youtube.com", "enabled": False}])
        self.assertEqual(self.mock.nvram["url_rulelist"], "<1>ALL>tiktok.com<0>ALL>youtube.com")
        self.assertEqual(self.mock.nvram["url_enable_x"], "1")

        status, data = self.call("/api/filter", {"enabled": True, "rules": [{"url": "evil<x"}]})
        self.assertEqual(status, 400)

        self.assertEqual(self.call("/api/logout", {})[0], 200)
        self.assertEqual(self.call("/api/filter")[0], 401)

    def test_keeps_mac_of_existing_rules(self):
        self.mock.nvram["url_rulelist"] = "<1>AA:BB:CC:DD:EE:FF>tiktok.com"
        self.call("/api/login", {"host": self.mock.host, "username": "admin", "password": "admin"})
        self.call("/api/filter", {"enabled": True, "rules": [{"url": "tiktok.com"}, {"url": "x.com"}]})
        self.assertEqual(self.mock.nvram["url_rulelist"], "<1>AA:BB:CC:DD:EE:FF>tiktok.com<1>ALL>x.com")


if __name__ == "__main__":
    unittest.main()
