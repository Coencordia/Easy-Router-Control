// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: deep-blue; icon-glyph: shield-alt;

// Easy Router Control: websites blokkeren op je ASUS-router vanaf je iPhone of iPad.
// Draait in de gratis app Scriptable. Praat rechtstreeks met de router via dezelfde
// endpoints als de officiële ASUS Router-app (login.cgi, appGet.cgi, applyapp.cgi).

const USER_AGENT = "asusrouter-Android-DUTUtil-1.0.0.245";
const STORE_KEY = "easy-router-control";

const PRESETS = {
  "TikTok": ["tiktok.com", "tiktokv.com", "tiktokcdn.com"],
  "Instagram": ["instagram.com", "cdninstagram.com"],
  "YouTube": ["youtube.com", "youtu.be"],
  "Facebook": ["facebook.com", "fb.com"],
  "Snapchat": ["snapchat.com"],
  "X / Twitter": ["x.com", "twitter.com"],
  "Roblox": ["roblox.com", "rbxcdn.com"],
  "Netflix": ["netflix.com"],
};

const LOGIN_ERRORS = {
  "1": "Inloggen geweigerd door de router.",
  "2": "Er is al iemand anders ingelogd op de router. Log daar eerst uit.",
  "3": "Onjuiste gebruikersnaam of wachtwoord.",
  "7": "Te veel mislukte pogingen. Wacht een paar minuten en probeer opnieuw.",
  "8": "Te veel mislukte pogingen. Wacht een paar minuten en probeer opnieuw.",
  "10": "De router vraagt om een captcha. Log één keer in via de routerpagina in Safari en probeer het daarna opnieuw.",
};

class SessionExpired extends Error {}
class LoginError extends Error {}

// -- Regels -----------------------------------------------------------------

function decodeNvram(value) {
  return (value || "").replace(/&#60;?/g, "<").replace(/&#62;?/g, ">").replace(/&amp;/g, "&");
}

// Nieuwe firmware: "<1>ALL>tiktok.com" (aan>MAC>url). Oude firmware: "<tiktok.com".
function parseRulelist(raw) {
  const rules = [];
  let legacy = null;
  for (const entry of decodeNvram(raw).split("<")) {
    if (!entry.trim()) continue;
    const parts = entry.split(">");
    if (parts.length >= 3) {
      legacy = false;
      rules.push({ url: parts[2], enabled: parts[0] !== "0", mac: parts[1] || "ALL" });
    } else {
      if (legacy === null) legacy = true;
      rules.push({ url: parts[parts.length - 1], enabled: true, mac: "ALL" });
    }
  }
  return { rules, legacy };
}

function serializeRulelist(rules, legacy) {
  if (legacy) return rules.filter(r => r.enabled).map(r => `<${r.url}`).join("");
  return rules.map(r => `<${r.enabled ? 1 : 0}>${r.mac || "ALL"}>${r.url}`).join("");
}

// Maakt van "https://www.TikTok.com/@user" gewoon "tiktok.com".
function normalizeDomain(value) {
  let v = String(value || "").trim().toLowerCase();
  if (v.includes("://")) v = v.split("://")[1];
  v = v.split(/[/?#]/)[0];
  v = v.split("@").pop().split(":")[0].replace(/^\.+|\.+$/g, "");
  if (v.startsWith("www.")) v = v.slice(4);
  const ok = /^([a-z0-9-]{1,63}\.)+[a-z0-9-]{1,63}$/.test(v) && v.length <= 253;
  if (!ok) throw new Error(`'${value}' is geen geldig domein (bijv. tiktok.com).`);
  return v;
}

// -- Router -----------------------------------------------------------------

class AsusRouter {
  constructor(host, username, password) {
    host = host.trim().replace(/\/+$/, "");
    this.base = host.includes("://") ? host : `http://${host}`;
    this.username = username;
    this.password = password;
    this.token = null;
  }

  async post(path, form) {
    const req = new Request(this.base + path);
    req.method = "POST";
    req.timeoutInterval = 15;
    // Routers gebruiken bij HTTPS een zelfondertekend certificaat.
    req.allowInsecureRequest = true;
    req.headers = {
      "User-Agent": USER_AGENT,
      "Referer": this.base + "/",
      "Content-Type": "application/x-www-form-urlencoded",
    };
    if (this.token) req.headers["Cookie"] = `asus_token=${this.token}`;
    req.body = Object.entries(form)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join("&");
    let text;
    try {
      text = await req.loadString();
    } catch (e) {
      throw new Error(`Kan de router op ${this.base} niet bereiken. Zit je op je eigen wifi, en heeft Scriptable toegang tot het lokale netwerk (Instellingen → Scriptable → Lokaal netwerk)?`);
    }
    const status = req.response && req.response.statusCode;
    if (status === 401 || status === 403) throw new SessionExpired("Sessie verlopen.");
    if (status && status >= 400) throw new Error(`De router gaf een foutmelding (HTTP ${status}).`);
    return text;
  }

  // Bij een verlopen sessie stuurt de router een HTML-pagina in plaats van JSON.
  static json(text) {
    try {
      const data = JSON.parse(text);
      if (data && typeof data === "object") return data;
    } catch (e) {}
    throw new SessionExpired("Sessie verlopen.");
  }

  async login() {
    this.token = null;
    const auth = Data.fromString(`${this.username}:${this.password}`).toBase64String();
    const text = await this.post("/login.cgi", { login_authorization: auth });
    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      throw new Error("Dit lijkt geen ASUS-router te zijn (onverwacht antwoord).");
    }
    if (!data || !data.asus_token) {
      const code = data && data.error_status != null ? String(data.error_status) : "";
      throw new LoginError(LOGIN_ERRORS[code] || `Inloggen mislukt (foutcode ${code || "?"}).`);
    }
    this.token = data.asus_token;
  }

  async call(fn) {
    if (!this.token) await this.login();
    try {
      return await fn();
    } catch (e) {
      if (!(e instanceof SessionExpired)) throw e;
      await this.login();
      return await fn();
    }
  }

  nvram(...keys) {
    const hook = keys.map(k => `nvram_get(${k})`).join(";");
    return this.call(async () => AsusRouter.json(await this.post("/appGet.cgi", { hook })));
  }

  apply(values) {
    const form = { action_mode: "apply", rc_service: "restart_firewall", ...values };
    return this.call(async () => AsusRouter.json(await this.post("/applyapp.cgi", form)));
  }

  async getInfo() {
    const d = await this.nvram("productid", "firmver", "buildno", "extendno");
    let fw = [d.firmver, d.buildno].filter(Boolean).join(".");
    if (d.extendno) fw += `_${d.extendno}`;
    return { model: d.productid || "ASUS-router", firmware: fw };
  }

  async getFilter() {
    const d = await this.nvram("url_enable_x", "url_rulelist", "url_mode_x", "buildno");
    let { rules, legacy } = parseRulelist(d.url_rulelist);
    if (legacy === null) {
      const build = parseInt(d.buildno, 10);
      legacy = !isNaN(build) && build < 384;
    }
    return { enabled: d.url_enable_x === "1", rules, legacy, whitelist: d.url_mode_x === "1" };
  }

  async setFilter(enabled, rules, legacy) {
    const rulelist = serializeRulelist(rules, legacy);
    await this.apply({ url_enable_x: enabled ? "1" : "0", url_rulelist: rulelist });
    const result = await this.getFilter();
    if (result.enabled !== enabled || serializeRulelist(result.rules, legacy) !== rulelist) {
      throw new Error("De router heeft de wijziging niet overgenomen. Probeer het opnieuw.");
    }
    return result;
  }
}

// -- Opslag (iOS-sleutelhanger) -----------------------------------------------

function loadSettings() {
  try {
    return Keychain.contains(STORE_KEY) ? JSON.parse(Keychain.get(STORE_KEY)) : {};
  } catch (e) {
    return {};
  }
}

function saveSettings(settings) {
  Keychain.set(STORE_KEY, JSON.stringify(settings));
}

// -- Schermen -----------------------------------------------------------------

async function message(title, text) {
  const a = new Alert();
  a.title = title;
  a.message = text || "";
  a.addAction("OK");
  await a.present();
}

async function loginFlow(settings, error) {
  for (;;) {
    const a = new Alert();
    a.title = "Inloggen op je ASUS-router";
    a.message = error || "Gebruik de gegevens van de routerpagina (niet je wifi-wachtwoord).";
    a.addTextField("Routeradres", settings.host || "192.168.50.1");
    a.addTextField("Gebruikersnaam", settings.username || "admin");
    a.addSecureTextField("Wachtwoord", "");
    a.addAction("Inloggen en onthouden");
    a.addAction("Inloggen");
    a.addCancelAction("Annuleren");
    const choice = await a.present();
    if (choice === -1) return null;

    const host = a.textFieldValue(0).trim();
    const username = a.textFieldValue(1).trim();
    const password = a.textFieldValue(2);
    if (!host || !username || !password) {
      error = "Vul routeradres, gebruikersnaam en wachtwoord in.";
      continue;
    }
    const router = new AsusRouter(host, username, password);
    try {
      await router.login();
    } catch (e) {
      error = e.message;
      continue;
    }
    saveSettings(choice === 0 ? { host, username, password } : { host, username });
    return router;
  }
}

async function connect() {
  const settings = loadSettings();
  if (settings.host && settings.username && settings.password) {
    const router = new AsusRouter(settings.host, settings.username, settings.password);
    try {
      await router.login();
      return router;
    } catch (e) {
      return loginFlow(settings, e.message);
    }
  }
  return loginFlow(settings);
}

async function askDomain() {
  const a = new Alert();
  a.title = "Website blokkeren";
  a.message = "Typ een website of plak een link.";
  a.addTextField("bijv. tiktok.com", "");
  a.addAction("Blokkeer");
  a.addCancelAction("Annuleren");
  if ((await a.present()) === -1) return null;
  return a.textFieldValue(0);
}

async function askPreset() {
  const names = Object.keys(PRESETS);
  const a = new Alert();
  a.title = "Snel blokkeren";
  for (const name of names) a.addAction(name);
  a.addCancelAction("Annuleren");
  const i = await a.present();
  return i === -1 ? null : PRESETS[names[i]];
}

async function app(router, sharedUrls) {
  let info = { model: "ASUS-router", firmware: "" };
  let filter;
  try {
    info = await router.getInfo();
    filter = await router.getFilter();
  } catch (e) {
    await message("Fout", e.message);
    return;
  }

  // Wijziging doorvoeren op de router en het scherm verversen.
  async function save(enabled, rules) {
    try {
      filter = await router.setFilter(enabled, rules, filter.legacy);
    } catch (e) {
      await message("Niet gelukt", e.message);
      try { filter = await router.getFilter(); } catch (_) {}
    }
    render();
  }

  async function addSites(inputs) {
    const known = new Set(filter.rules.map(r => r.url));
    const extra = [];
    for (const input of inputs) {
      let url;
      try {
        url = normalizeDomain(input);
      } catch (e) {
        await message("Ongeldige website", e.message);
        return;
      }
      if (!known.has(url)) {
        known.add(url);
        extra.push({ url, enabled: true, mac: "ALL" });
      }
    }
    if (!extra.length) {
      await message("Staat er al in", "Deze website wordt al geblokkeerd.");
      return;
    }
    // Iets toevoegen terwijl het filter uit staat heeft geen effect, dus zet het aan.
    await save(true, [...filter.rules, ...extra]);
  }

  const table = new UITable();
  table.showSeparators = true;

  function actionRow(title, subtitle, onSelect) {
    const row = new UITableRow();
    row.height = 60;
    row.dismissOnSelect = false;
    const cell = row.addText(title, subtitle);
    cell.titleFont = Font.semiboldSystemFont(17);
    cell.subtitleColor = Color.gray();
    row.onSelect = onSelect;
    table.addRow(row);
  }

  function render() {
    table.removeAllRows();

    const header = new UITableRow();
    header.isHeader = true;
    header.height = 70;
    const h = header.addText("Easy Router Control", `${info.model}${info.firmware ? " · firmware " + info.firmware : ""}`);
    h.titleFont = Font.boldSystemFont(22);
    h.subtitleColor = Color.gray();
    table.addRow(header);

    const active = filter.rules.filter(r => r.enabled).length;
    actionRow(
      filter.enabled ? "🟢 Websitefilter staat AAN" : "⚪️ Websitefilter staat UIT",
      filter.enabled
        ? `${active} website${active === 1 ? "" : "s"} geblokkeerd · tik om uit te zetten`
        : "Er wordt niets geblokkeerd · tik om aan te zetten",
      () => save(!filter.enabled, filter.rules),
    );
    if (filter.whitelist) {
      actionRow("⚠️ Whitelist-modus",
        "Alléén de websites in de lijst zijn toegestaan. Wijzig dit in de routerinstellingen.", null);
    }
    actionRow("➕ Website blokkeren", "Typ een website of plak een link", async () => {
      const value = await askDomain();
      if (value) await addSites([value]);
    });
    actionRow("⚡️ Snel blokkeren", "TikTok, Instagram, YouTube, Roblox…", async () => {
      const urls = await askPreset();
      if (urls) await addSites(urls);
    });

    const listHeader = new UITableRow();
    listHeader.isHeader = true;
    listHeader.addText(`Geblokkeerde websites (${filter.rules.length})`);
    table.addRow(listHeader);

    if (!filter.rules.length) {
      const empty = new UITableRow();
      const c = empty.addText("Nog geen geblokkeerde websites.");
      c.titleColor = Color.gray();
      table.addRow(empty);
    }

    filter.rules.forEach((rule, i) => {
      const row = new UITableRow();
      row.height = 54;
      row.dismissOnSelect = false;
      const label = row.addText(rule.enabled ? `🚫 ${rule.url}` : `⏸️ ${rule.url}`,
        rule.enabled ? "Geblokkeerd" : "Gepauzeerd");
      label.subtitleColor = Color.gray();
      label.widthWeight = 80;
      const del = row.addButton("Verwijder");
      del.widthWeight = 20;
      del.rightAligned();
      del.onTap = () => save(filter.enabled, filter.rules.filter((_, j) => j !== i));
      row.onSelect = async () => {
        if (filter.legacy) return;
        const rules = filter.rules.map((r, j) => (j === i ? { ...r, enabled: !r.enabled } : r));
        await save(filter.enabled, rules);
      };
      table.addRow(row);
    });

    actionRow("Uitloggen en gegevens vergeten", "Wist het opgeslagen wachtwoord van dit apparaat", async () => {
      const settings = loadSettings();
      saveSettings({ host: settings.host, username: settings.username });
      await message("Uitgelogd", "Je wachtwoord is gewist. Sluit dit scherm.");
    });

    table.reload();
  }

  render();

  let host = null;
  try { host = sharedUrls && sharedUrls.length ? normalizeDomain(sharedUrls[0]) : null; } catch (e) {}
  if (host) {
    const a = new Alert();
    a.title = `${host} blokkeren?`;
    a.addAction("Blokkeer");
    a.addCancelAction("Annuleren");
    // Tabel en vraag tegelijk tonen kan niet; eerst de vraag.
    if ((await a.present()) === 0) await addSites([host]);
  }

  await table.present(true);
}

async function main() {
  const router = await connect();
  if (!router) return;
  const shared = (args.urls || []).concat(args.plainTexts || []).filter(Boolean);
  await app(router, shared);
}

await main();
Script.complete();
