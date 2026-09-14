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
NAS, Router oder einem beliebigen Linux-Rechner im selben Netz – oder
**als Docker-Container, vollständig über Umgebungsvariablen konfiguriert**.

---

## ⚠️ Vorher lesen

Die Steckdose darf **nicht die Box versorgen, auf der LCD4Linux läuft**.
Sonst schaltet das Skript beim ersten Aussetzer den Strom ab, das Dashboard
kommt nie wieder online, und die Steckdose bleibt für immer aus. Die Shelly
gehört an das Display, den Monitor, die Beleuchtung – an alles, was *vom*
Dashboard abhängt, nicht an das, was es *trägt*.

---

## Docker

Das Image braucht keine Zustandsdaten, kein Volume und keine
Konfigurationsdatei – **alles wird über Umgebungsvariablen gesetzt**. Es
genügt Bridge-Netzwerk und es werden keine Ports veröffentlicht; der
Container baut nur ausgehende Verbindungen ins LAN auf, zum Dashboard und
zur Steckdose.

### Image bauen lassen und auf der NAS importieren

Der empfohlene Weg für die UGREEN NAS: GitHub baut das Image, die
Docker-App importiert die fertige Datei. Keine Registry, kein Anmelden,
kein Compiler auf der NAS.

**1. Bauen lassen.** Im Repository auf *Actions* → Workflow
*Tests und Image* → **Run workflow**. Zwei Felder stehen zur Wahl:

| Feld | Für die DXP2800 |
|---|---|
| `platform` | `linux/amd64` – die DXP2800 hat einen Intel N100 |
| `tag` | `latest`, sofern nichts anderes gewünscht ist |

Erst laufen die Tests, dann wird gebaut; zusammen dauert das gut eine
Minute.

> Den Knopf *Run workflow* zeigt GitHub nur für Workflows an, die auf dem
> Standardbranch liegen. Solange die Datei nur in einem Feature-Branch
> steht, muss dieser erst nach `main` gebracht werden.

**2. Herunterladen.** Unten auf der Seite des Laufs hängt unter *Artifacts*
das Paket `lcd4linux-shelly-amd64`. GitHub packt Artefakte immer in ein
ZIP – dieses **entpacken**, herauskommt `lcd4linux-shelly-amd64.tar`
(rund 20 MB). Genau diese `.tar` will die Docker-App, nicht das ZIP.

**3. Auf die NAS legen.** Die Datei in eine Freigabe kopieren, zum Beispiel
über den Dateimanager von UGOS nach `/volume1/docker/`.

**4. Importieren.** Docker-App → **Image** → **`+`** → **Von NAS** → die
`.tar` auswählen. Danach steht `lcd4linux-shelly:latest` in der Liste.
(*Von Paketquelle* daneben lädt aus einer Registry – das wird hier nicht
gebraucht.)

**5. Container anlegen.** Aus dem importierten Image einen Container
erstellen und dabei setzen:

* Neustartverhalten: **immer neu starten**
* Netzwerk: **Bridge** (Standard), keine Portweiterleitung nötig
* Umgebungsvariablen – mindestens diese drei:

| Variable | Wert |
|---|---|
| `TZ` | `Europe/Berlin` |
| `DASHBOARD_URL` | `http://192.168.178.104:8050/api/state` |
| `SHELLY_HOST` | die IP der Shelly Plug M Gen3, z. B. `192.168.178.60` |

Alles Weitere ist optional und steht in der Tabelle unter
[Konfiguration](#konfiguration). Zum Ausprobieren lohnt ein erster Start
mit `COMMAND=test`: der Container schreibt dann ins Protokoll, ob er
Dashboard und Steckdose erreicht, und beendet sich wieder. Läuft das
durch, die Variable wieder entfernen (oder auf `watch` setzen) und den
Container dauerhaft starten.

Ein Update läuft genauso: Workflow starten, neue `.tar` importieren,
Container neu erstellen.

### Alternativ: über SSH

Wer lieber auf der Kommandozeile arbeitet, baut das Image direkt auf der
NAS – es gibt keine kompilierten Abhängigkeiten, das dauert auch auf dem
N100 nur Sekunden:

```bash
ssh <benutzer>@<nas-ip>
sudo mkdir -p /volume1/docker/lcd4linux-shelly
cd /volume1/docker/lcd4linux-shelly
sudo git clone https://github.com/CE-Repo/LCD4Linux_Shelly.git .
sudo nano docker-compose.yml     # DASHBOARD_URL und SHELLY_HOST eintragen
sudo docker compose up -d --build
sudo docker compose logs -f
```

Die mitgelieferte `docker-compose.yml` listet alle Variablen mit Kommentar
auf. Vor dem Dauerbetrieb lohnt ein Blick, ob der Container beide Seiten
erreicht:

```bash
sudo docker compose run --rm lcd4linux-shelly test
```

Ein Archiv aus dem Actions-Workflow lässt sich auf demselben Weg
einspielen, ohne zu bauen:

```bash
sudo docker load -i lcd4linux-shelly-amd64.tar
```

Und ohne Compose, wenn das Image schon da ist:

```bash
docker run -d --name lcd4linux-shelly --restart unless-stopped \
    -e TZ=Europe/Berlin \
    -e DASHBOARD_URL=http://192.168.178.104:8050/api/state \
    -e SHELLY_HOST=192.168.178.60 \
    lcd4linux-shelly:latest
```

Der Container läuft als Benutzer `shelly` (UID 1000), nicht als root, und
beendet sich auf `docker stop` innerhalb einer Sekunde sauber.

### Healthcheck

Der Watcher berührt nach jeder Abfrage eine Heartbeat-Datei; der eingebaute
Healthcheck prüft, ob diese frisch ist. **Gesund heißt: der Watcher arbeitet** –
nicht: das Dashboard ist online. Ein ausgeschaltetes Kodi ist ein gültiges
Ergebnis und kein Fehler, der Container bleibt dabei `healthy`.

```bash
docker inspect -f '{{.State.Health.Status}}' lcd4linux-shelly
```

### Passwörter

Wer das Shelly- oder Dashboard-Passwort nicht im Klartext in der
Compose-Datei stehen haben will, legt es in eine Datei und verweist mit
`…_FILE` darauf:

```yaml
    environment:
      SHELLY_PASSWORD_FILE: /run/secrets/shelly_password
    secrets:
      - shelly_password

secrets:
  shelly_password:
    file: ./shelly_password.txt
```

Das gilt für jede Variable aus der Tabelle weiter unten: zu jedem Namen
existiert eine `…_FILE`-Variante, die Vorrang hat.

---

## Installation ohne Docker

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
lcd4linux_shelly.py [Optionen] [watch|once|status|on|off|test|health]
```

| Befehl | Wirkung |
|---|---|
| `watch` | Dauerbetrieb, Standard |
| `once` | eine Abfrage, gegebenenfalls schalten, fertig – für `cron` |
| `status` | eine Zeile pro Seite; Rückgabewert 0 = online, 2 = offline |
| `on` / `off` | Steckdose von Hand schalten |
| `test` | Verbindungstest mit Gerätedaten |
| `health` | Healthcheck des Containers, siehe oben |

Im Container wird der Befehl über `COMMAND` gewählt, etwa `COMMAND=test`.

Für `cron` statt eines Dienstes:

```cron
* * * * * /usr/local/bin/lcd4linux_shelly.py once >> /var/log/lcd4linux-shelly.log 2>&1
```

Da `once` den vorherigen Zustand nicht kennt, schaltet es bei jedem Lauf
einmal – für den Dauerbetrieb ist `watch` die bessere Wahl.

---

## Konfiguration

Jede Einstellung lässt sich auf drei Wegen setzen. Wer gewinnt, steht weiter
unten; eingebaute Vorgaben < INI-Datei < **Umgebung** < Kommandozeile.

| Umgebungsvariable | INI | Kommandozeile | Standard | Bedeutung |
|---|---|---|---|---|
| `DASHBOARD_URL` | `dashboard.url` | `-d`, `--dashboard-url` | `http://192.168.178.104:8050/api/state` | Adresse des Dashboards |
| `DASHBOARD_INTERVAL` | `dashboard.interval` | `-i`, `--interval` | `10` | Sekunden zwischen zwei Abfragen |
| `DASHBOARD_TIMEOUT` | `dashboard.timeout` | `--timeout` | `4` | Zeitlimit einer Abfrage |
| `DASHBOARD_ONLINE_AFTER` | `dashboard.online_after` | `--online-after` | `1` | gute Abfragen bis „an“ |
| `DASHBOARD_OFFLINE_AFTER` | `dashboard.offline_after` | `--offline-after` | `3` | Fehlversuche bis „aus“ |
| `DASHBOARD_USERNAME` | `dashboard.username` | – | `lcd4linux` | Benutzername des Web-Editors (beliebig) |
| `DASHBOARD_PASSWORD` | `dashboard.password` | `--dashboard-password` | – | Passwort des Web-Editors |
| `DASHBOARD_ACCEPT_STATUS` | `dashboard.accept_status` | – | `200,401` | HTTP-Status, die als online gelten; `any` = jede Antwort |
| `SHELLY_HOST` | `shelly.host` | `-s`, `--shelly-host` | – | Adresse der Shelly |
| `SHELLY_CHANNEL` | `shelly.channel` | `--channel` | `0` | Schaltkanal |
| `SHELLY_USERNAME` | `shelly.username` | – | `admin` | bei Shelly immer `admin` |
| `SHELLY_PASSWORD` | `shelly.password` | `--shelly-password` | – | Shelly-Passwort |
| `SHELLY_TIMEOUT` | `shelly.timeout` | – | `5` | Zeitlimit eines Schaltbefehls |
| `SHELLY_RETRIES` | `shelly.retries` | – | `3` | Wiederholungen bei Fehlern |
| `RESYNC_INTERVAL` | `behaviour.resync_interval` | `--resync-interval` | `300` | Nachkorrektur, `0` = aus |
| `ON_EXIT` | `behaviour.on_exit` | `--on-exit` | `keep` | `keep`, `off` oder `on` beim Beenden |
| `DRY_RUN` | `behaviour.dry_run` | `-n`, `--dry-run` | `false` | nur protokollieren, nichts schalten |
| `VERBOSE` | `behaviour.verbose` | `-v`, `--verbose` | `false` | jede Abfrage protokollieren |
| `LOG_FILE` | `behaviour.log_file` | `--log-file` | – | in eine Datei statt nach stdout |
| `HEARTBEAT_FILE` | `behaviour.heartbeat_file` | `--heartbeat-file` | – | Datei für den Healthcheck (im Image gesetzt) |
| `COMMAND` | – | Positionsargument | `watch` | welcher Befehl ausgeführt wird |
| `CONFIG_FILE` | – | `-c`, `--config` | – | Pfad zur INI-Datei |

Zwei Dinge gelten für **jeden** dieser Namen:

* **`…_FILE`** – statt `SHELLY_PASSWORD` kann `SHELLY_PASSWORD_FILE` auf eine
  Datei zeigen, deren Inhalt der Wert ist. Praktisch für Docker-Secrets, und
  der Wert taucht nicht in `docker inspect` auf. Die `…_FILE`-Variante hat
  Vorrang vor dem direkten Wert.
* **`L4LS_`-Präfix** – `L4LS_SHELLY_HOST` wird genauso gelesen wie
  `SHELLY_HOST` und geht vor. Nur nötig, wenn ein schlichter Name wie
  `VERBOSE` außerhalb eines Containers mit etwas anderem kollidiert.

Eine INI-Datei ist nirgends Pflicht. Ohne `--config` und ohne `CONFIG_FILE`
sucht das Skript der Reihe nach `./lcd4linux-shelly.ini`,
`~/.config/lcd4linux-shelly.ini` und `/etc/lcd4linux-shelly.ini`; findet es
keine, gelten Vorgaben und Umgebung. `lcd4linux-shelly.example.ini` zeigt
alle Werte in Dateiform.

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
| Container erreicht das Dashboard nicht | Aus dem Container heraus prüfen: `docker compose run --rm lcd4linux-shelly test`. Adressen müssen die des LAN sein – `127.0.0.1` zeigt im Container auf den Container selbst. |
| Container ist `unhealthy` | Der Watcher hat länger als das Dreifache eines Abfragezyklus nichts mehr getan. Ins Log sehen; ein offline gemeldetes Dashboard allein löst das nicht aus. |
| Zeitstempel im Log gehen falsch | `TZ=Europe/Berlin` setzen. |

Mehr Details liefert `VERBOSE=true` bzw. `-v`, nachzulesen mit
`docker compose logs -f` oder `journalctl -u lcd4linux-shelly -f`.

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Die Testsuite startet kleine HTTP-Server auf `127.0.0.1`, die ein Dashboard
und eine Shelly nachstellen – inklusive Digest-Anmeldung. Geprüft werden
außerdem die Entprellung, die Nachkorrektur, die Environment-Schicht samt
`…_FILE` und der Healthcheck. Es wird also weder echte Hardware noch ein
Netzwerk gebraucht.

---

## Lizenz

MIT – siehe [LICENSE](LICENSE).
