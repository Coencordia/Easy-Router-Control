# Easy Router Control

Een eenvoudige app om in te loggen op je **ASUS-router** en websites te blokkeren,
zonder door de ingewikkelde routerinstellingen te hoeven klikken.

- Inloggen met je gewone routergegevens
- Websitefilter met één schakelaar aan- of uitzetten
- Website toevoegen door gewoon `tiktok.com` (of een hele link) in te typen
- Snelknoppen voor TikTok, Instagram, YouTube, Facebook, Snapchat, X, Roblox en Netflix
- Websites tijdelijk pauzeren of verwijderen
- Werkt ook vanaf je telefoon (zie hieronder)

De app gebruikt het ingebouwde **URL-filter** van de router (in de routerpagina te vinden
onder *Firewall → URL-filter* of *Ouderlijk toezicht*). Alles wat je hier instelt, zie je
daar ook terug, en andersom.

## Wat heb je nodig

- Een ASUS-router met Asuswrt- of Asuswrt-Merlin-firmware (vrijwel alle RT-, GT-, TUF- en ZenWiFi-modellen)
- Een computer in je thuisnetwerk met [Python 3.9 of nieuwer](https://www.python.org/downloads/)
  (op Windows: vink bij installatie *Add Python to PATH* aan)

Er zijn geen extra pakketten nodig.

## Starten

- **Windows:** dubbelklik op `start.bat`
- **macOS / Linux:** `./start.sh` of `python3 -m easy_router_control`

Je browser opent automatisch op <http://localhost:8080>. Vul in:

| Veld           | Meestal                                                      |
|----------------|--------------------------------------------------------------|
| Routeradres    | `192.168.50.1`, `192.168.1.1` of `router.asus.com`           |
| Gebruikersnaam | `admin` (of wat je zelf hebt ingesteld)                      |
| Wachtwoord     | het wachtwoord van de routerpagina (niet je wifi-wachtwoord) |

Vink *Router gebruikt HTTPS* aan als je de routerpagina normaal via `https://` opent.
Gebruikt je router een andere poort, typ die dan achter het adres, bijv. `192.168.50.1:8443`.

### Vanaf je telefoon

Start de app met `--lan`:

```sh
python3 -m easy_router_control --lan
```

De app toont dan een adres zoals `http://192.168.50.23:8080/` dat je op je telefoon
kunt openen (zolang die op dezelfde wifi zit en de computer aan staat).
Let op: iedereen op je wifi kan die pagina dan openen. Zolang je ingelogd bent, kunnen
zij dus ook het filter aanpassen. Log uit als je klaar bent.

Andere opties: `--port 9000` (andere poort), `--no-browser` (browser niet openen).

## Goed om te weten

- **Elke wijziging herstart de firewall van de router.** Dat duurt een paar seconden;
  verbindingen kunnen heel even haperen.
- **Het filter werkt op domeinnaam.** `tiktok.com` blokkeert ook `www.tiktok.com` en
  `m.tiktok.com`. Grote apps gebruiken vaak meerdere domeinen; de snelknoppen voegen
  er daarom meerdere tegelijk toe.
- **Het is geen waterdichte blokkade.** Apps en browsers met een eigen VPN of
  versleutelde DNS (DNS-over-HTTPS) kunnen het filter soms omzeilen. Ook duurt het
  soms even voordat een al geopende website echt geblokkeerd wordt.
- **Je wachtwoord** wordt niet opgeslagen op schijf. Het blijft alleen in het geheugen
  zolang de app draait, zodat de app automatisch opnieuw kan inloggen als de
  routersessie verloopt.
- Regels die je in de routerpagina aan een **specifiek apparaat** hebt gekoppeld, blijven
  aan dat apparaat gekoppeld. Nieuwe regels gelden voor alle apparaten.
- Staat je router in **whitelist-modus**, dan waarschuwt de app: dan zijn juist alléén de
  websites in de lijst toegestaan.

## Problemen oplossen

| Melding | Oplossing |
|---|---|
| *Kan de router niet bereiken* | Controleer het adres. Zit je op het thuisnetwerk (niet op mobiele data of een gastnetwerk)? |
| *Onjuiste gebruikersnaam of wachtwoord* | Gebruik de inloggegevens van de routerpagina, niet die van de wifi. |
| *Te veel mislukte pogingen* | De router blokkeert inloggen even. Wacht een paar minuten. |
| *De router vraagt om een captcha* | Log één keer in via de gewone routerpagina in je browser en probeer het daarna opnieuw. |

## Ontwikkelen

```sh
python3 -m unittest          # tests draaien (tegen een nagebootste router)
python3 -m tests.mock_router # nep-router op 127.0.0.1:8081, inloggen met admin/admin
```

Opbouw:

- `easy_router_control/asus.py`: praat met de router (`login.cgi`, `appGet.cgi`, `applyapp.cgi`)
- `easy_router_control/server.py`: lokale webserver + JSON-API voor de pagina
- `easy_router_control/static/index.html`: de bedieningspagina
