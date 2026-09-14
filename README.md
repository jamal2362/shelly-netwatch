# LCD4Linux_Shelly

Schaltet eine **Shelly Plug M Gen3** (oder jede andere Shelly der Generation 2/3)
im Takt des **LCD4Linux-Webdashboards**:

* Dashboard auf `http://192.168.178.104:8050/` erreichbar → **Steckdose an**
* Dashboard weg → **Steckdose aus**

Gedacht als Begleiter zum Kodi-Add-on
[`script.lcd4linux`](https://github.com/CE-Repo/script.lcd4linux), damit das
angeschlossene Display nur dann 230 V bekommt, wenn das Dashboard auch läuft.

Ein einziges Python-Skript, **nur Standardbibliothek** – kein `pip`, kein
`requests`, kein Compiler. Läuft ab Python 3.7 auf CoreELEC, Raspberry Pi,
NAS, Router oder einem beliebigen Linux-Rechner im selben Netz.

---

## ⚠️ Vorher lesen

Die Steckdose darf **nicht die Box versorgen, auf der LCD4Linux läuft**.
Sonst schaltet das Skript beim ersten Aussetzer den Strom ab, das Dashboard
kommt nie wieder online, und die Steckdose bleibt für immer aus. Die Shelly
gehört an das Display, den Monitor, die Beleuchtung – an alles, was *vom*
Dashboard abhängt, nicht an das, was es *trägt*.

---

## Installation

```bash
git clone https://github.com/CE-Repo/LCD4Linux_Shelly.git
cd LCD4Linux_Shelly

# Konfiguration anlegen und die beiden Adressen eintragen
sudo cp lcd4linux-shelly.example.ini /etc/lcd4linux-shelly.ini
sudo nano /etc/lcd4linux-shelly.ini

# Skript ablegen
sudo install -m 755 lcd4linux_shelly.py /usr/local/bin/lcd4linux_shelly.py
```

Anzupassen sind genau zwei Zeilen:

```ini
[dashboard]
url  = http://192.168.178.104:8050/api/state

[shelly]
host = 192.168.178.60          # die IP der Shelly Plug M Gen3
```

Die IP der Shelly steht in der Shelly-App unter *Einstellungen → Gerätedaten*
oder im Router. Eine feste IP (DHCP-Reservierung) ist zu empfehlen.

### Erst einmal ausprobieren

```bash
lcd4linux_shelly.py test        # erreicht das Skript beide Seiten?
lcd4linux_shelly.py status      # was sagen Dashboard und Steckdose gerade?
lcd4linux_shelly.py once -v     # eine Abfrage, einmal schalten
lcd4linux_shelly.py watch -v -n # zuschauen, ohne wirklich zu schalten
```

`test` meldet Modell, MAC und Firmware der Shelly – wenn das durchläuft,
stimmen Adresse und Passwort.

### Als Dienst (systemd)

```bash
sudo cp systemd/lcd4linux-shelly.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lcd4linux-shelly
journalctl -u lcd4linux-shelly -f
```

### Auf CoreELEC / LibreELEC

Dort gibt es kein `/usr/local/bin` und kein normales systemd-Verzeichnis;
beides liegt unter `/storage`:

```bash
mkdir -p /storage/lcd4linux-shelly
cp lcd4linux_shelly.py lcd4linux-shelly.example.ini /storage/lcd4linux-shelly/
mv /storage/lcd4linux-shelly/lcd4linux-shelly.example.ini \
   /storage/lcd4linux-shelly/lcd4linux-shelly.ini
chmod +x /storage/lcd4linux-shelly/lcd4linux_shelly.py

cp systemd/lcd4linux-shelly.service /storage/.config/system.d/
nano /storage/.config/system.d/lcd4linux-shelly.service   # Pfade anpassen,
                                                          # DynamicUser entfernen
systemctl daemon-reload
systemctl enable --now lcd4linux-shelly
```

Läuft das Skript auf derselben Box wie Kodi, genügt als Dashboard-Adresse
`http://127.0.0.1:8050/api/state`.

---

## Wie es arbeitet

Das Skript fragt alle 10 Sekunden `http://<box>:8050/api/state` ab – den
kleinsten Endpunkt des Dashboards, ein paar hundert Byte JSON statt der
ganzen Seite. Aus der Antwort wird ein Zustand:

| Antwort | Bewertung |
|---|---|
| HTTP 200 | online |
| HTTP 401 (Web-Editor mit Passwort) | online – der Server antwortet ja |
| Verbindung abgelehnt, Timeout, DNS-Fehler | offline |
| HTTP 5xx | offline |

Geschaltet wird erst, wenn sich der Zustand **wirklich** geändert hat:

* **einschalten** nach `online_after` guten Abfragen (Standard 1 – sofort),
* **ausschalten** nach `offline_after` fehlgeschlagenen Abfragen
  (Standard 3, also nach ~30 s).

Damit übersteht die Steckdose einen Neustart des Add-ons, ein kurz
überlastetes WLAN oder einen Kodi-Skin-Reload, ohne zu klackern. Solange sich
nichts ändert, geht auch kein Befehl an die Shelly.

Alle 5 Minuten (`resync_interval`) wird zusätzlich geprüft, ob die Steckdose
noch so steht, wie sie soll – falls jemand sie über die App oder den Taster
umgelegt hat, wird sie zurückgestellt. Wer manuelles Schalten behalten
möchte, setzt `resync_interval = 0`.

Beim Start ist der Zustand unbekannt; die erste eindeutige Abfrage bringt die
Steckdose in die richtige Stellung. Beim Beenden bleibt sie unangetastet
(`on_exit = keep`), lässt sich aber auf `off` oder `on` stellen.

### Ansteuerung der Shelly

Gen2/Gen3-Geräte sprechen JSON-RPC über `POST /rpc`:

```
Switch.Set        {"id": 0, "on": true}    schaltet
Switch.GetStatus  {"id": 0}                liest den Zustand zurück
Shelly.GetDeviceInfo                       Modell, MAC, Firmware
```

Ist im Gerät unter *Einstellungen → Authentifizierung* ein Passwort gesetzt,
verwendet das Skript HTTP-Digest mit SHA-256. Der Benutzername ist bei
Shelly immer `admin`; einzutragen ist nur `shelly.password`. Shelly weicht
beim Digest-Verfahren vom RFC ab (HA2 ist die feste Zeichenkette
`dummy_method:dummy_uri`) – das Skript beherrscht beide Varianten und
probiert sie nacheinander.

Fehlgeschlagene Schaltbefehle werden bis zu `retries`-mal mit wachsender
Pause wiederholt; klappt es trotzdem nicht, landet eine Zeile im Log und die
nächste Abfrage versucht es erneut. Der Watcher bleibt in jedem Fall am Leben.

---

## Befehle

```
lcd4linux_shelly.py [Optionen] [watch|once|status|on|off|test]
```

| Befehl | Wirkung |
|---|---|
| `watch` | Dauerbetrieb, Standard |
| `once` | eine Abfrage, gegebenenfalls schalten, fertig – für `cron` |
| `status` | eine Zeile pro Seite; Rückgabewert 0 = online, 2 = offline |
| `on` / `off` | Steckdose von Hand schalten |
| `test` | Verbindungstest mit Gerätedaten |

Für `cron` statt eines Dienstes:

```cron
* * * * * /usr/local/bin/lcd4linux_shelly.py once >> /var/log/lcd4linux-shelly.log 2>&1
```

Da `once` den vorherigen Zustand nicht kennt, schaltet es bei jedem Lauf
einmal – für den Dauerbetrieb ist `watch` die bessere Wahl.

---

## Optionen

Jeder Wert aus der INI-Datei lässt sich auf der Kommandozeile überschreiben.
Ohne `--config` sucht das Skript der Reihe nach
`./lcd4linux-shelly.ini`, `~/.config/lcd4linux-shelly.ini`,
`/etc/lcd4linux-shelly.ini`.

| Option | INI | Standard | Bedeutung |
|---|---|---|---|
| `-c`, `--config` | – | – | Konfigurationsdatei |
| `-d`, `--dashboard-url` | `dashboard.url` | `http://192.168.178.104:8050/api/state` | Adresse des Dashboards |
| `--dashboard-password` | `dashboard.password` | – | Passwort des Web-Editors |
| `-s`, `--shelly-host` | `shelly.host` | – | Adresse der Shelly |
| `--shelly-password` | `shelly.password` | – | Shelly-Passwort |
| `--channel` | `shelly.channel` | `0` | Schaltkanal |
| `-i`, `--interval` | `dashboard.interval` | `10` | Sekunden zwischen zwei Abfragen |
| `--timeout` | `dashboard.timeout` | `4` | Zeitlimit einer Abfrage |
| `--online-after` | `dashboard.online_after` | `1` | gute Abfragen bis „an“ |
| `--offline-after` | `dashboard.offline_after` | `3` | Fehlversuche bis „aus“ |
| `--resync-interval` | `behaviour.resync_interval` | `300` | Nachkorrektur, `0` = aus |
| `--on-exit` | `behaviour.on_exit` | `keep` | `keep`, `off` oder `on` beim Beenden |
| `-n`, `--dry-run` | `behaviour.dry_run` | `false` | nur protokollieren |
| `-v`, `--verbose` | – | – | jede Abfrage protokollieren |
| `--log-file` | – | – | in eine Datei schreiben |

Weitere Werte nur in der INI-Datei: `dashboard.username`,
`dashboard.accept_status`, `shelly.username`, `shelly.timeout`,
`shelly.retries`.

---

## Fehlersuche

| Meldung | Ursache |
|---|---|
| `Connection refused` | Kodi läuft nicht, das Add-on ist aus oder der Web-Editor ist abgeschaltet. Im Add-on: *Einstellungen → Web → Web-Editor aktiv*. |
| Dashboard nur lokal erreichbar | Im Add-on steht `web_bind` auf `local` (127.0.0.1). Auf „alle Schnittstellen“ stellen oder das Skript auf derselben Box laufen lassen. |
| `HTTP 401` als offline gewertet | `accept_status` enthält keine 401 – entweder 401 ergänzen oder `dashboard.password` setzen. |
| `the Shelly requires a password` | In der Shelly ist Authentifizierung aktiv: `shelly.password` eintragen. |
| `the Shelly rejected the password` | Falsches Passwort. Benutzername ist immer `admin`. |
| `unknown method Switch.Set` | Ein Gen1-Gerät. Die Gen1-Geräte sprechen `/relay/0?turn=on` statt RPC und werden hier nicht unterstützt. |
| Steckdose klackert | `offline_after` erhöhen oder `interval` verlängern. |

Mehr Details liefert `-v`, im Dienst nachzulesen mit
`journalctl -u lcd4linux-shelly -f`.

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Die Testsuite startet kleine HTTP-Server auf `127.0.0.1`, die ein Dashboard
und eine Shelly nachstellen – inklusive Digest-Anmeldung. Es wird also weder
echte Hardware noch ein Netzwerk gebraucht.

---

## Lizenz

MIT – siehe [LICENSE](LICENSE).
