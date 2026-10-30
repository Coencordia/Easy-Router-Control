// Test de routerlogica van ios/EasyRouterControl.js buiten Scriptable.
// Gebruik: node tests/test_ios_script.js <host:poort van tests.mock_router>
const fs = require("fs");
const http = require("http");
const assert = require("assert");
const path = require("path");

class Request {
  constructor(url) { this.url = url; this.method = "GET"; this.headers = {}; this.body = null; }
  loadString() {
    return new Promise((resolve, reject) => {
      const headers = { ...this.headers };
      if (this.body) headers["Content-Length"] = Buffer.byteLength(this.body);
      const req = http.request(this.url, { method: this.method, headers }, res => {
        let data = "";
        res.on("data", c => (data += c));
        res.on("end", () => { this.response = { statusCode: res.statusCode }; resolve(data); });
      });
      req.on("error", reject);
      if (this.body) req.write(this.body);
      req.end();
    });
  }
}
const Data = { fromString: s => ({ toBase64String: () => Buffer.from(s).toString("base64") }) };

const src = fs.readFileSync(path.join(__dirname, "../ios/EasyRouterControl.js"), "utf8")
  .replace(/await main\(\);\s*Script\.complete\(\);\s*$/, "")
  + "\nreturn { AsusRouter, parseRulelist, serializeRulelist, normalizeDomain };";
const lib = new Function("Request", "Data", src)(Request, Data);

(async () => {
  const host = process.argv[2];
  assert.strictEqual(lib.normalizeDomain(" https://www.TikTok.com/@x?y=1 "), "tiktok.com");
  assert.throws(() => lib.normalizeDomain("evil<1>ALL>x.com"));
  assert.throws(() => lib.normalizeDomain("tiktok"));
  assert.deepStrictEqual(lib.parseRulelist("&#601&#62ALL&#62a.com&#600&#62AA&#62b.com").rules,
    [{ url: "a.com", enabled: true, mac: "ALL" }, { url: "b.com", enabled: false, mac: "AA" }]);
  assert.strictEqual(lib.parseRulelist("<a.com<b.com").legacy, true);

  await assert.rejects(new lib.AsusRouter(host, "admin", "fout").login(), /Onjuiste/);
  await assert.rejects(new lib.AsusRouter("127.0.0.1:1", "admin", "admin").login(), /niet bereiken/);

  const r = new lib.AsusRouter(host, "admin", "admin");
  await r.login();
  assert.deepStrictEqual(await r.getInfo(), { model: "RT-AX86U", firmware: "3.0.0.4.388_24243" });
  let f = await r.getFilter();
  assert.strictEqual(f.enabled, false);
  f = await r.setFilter(true, [{ url: "tiktok.com", enabled: true, mac: "ALL" },
                               { url: "x.com", enabled: false, mac: "ALL" }], f.legacy);
  assert.strictEqual(f.enabled, true);
  assert.deepStrictEqual(f.rules.map(x => [x.url, x.enabled]), [["tiktok.com", true], ["x.com", false]]);

  r.token = "verlopen";  // sessie verlopen -> automatisch opnieuw inloggen
  assert.strictEqual((await r.getFilter()).rules.length, 2);
  console.log("Alle iOS-scripttests geslaagd");
})().catch(e => { console.error(e); process.exit(1); });
