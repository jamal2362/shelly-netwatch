# shelly-netwatch

Schaltet eine **Shelly (Gen2/Gen3)** danach, ob ein Dienst im Netz antwortet:

* Ziel erreichbar → **Steckdose an**
* Ziel weg → **Steckdose aus**

Das Ziel ist alles, was auf **IP und Port** antwortet – ein Webdashboard, ein
Media-Player, ein Drucker, ein Access Point, ein Spieleserver, ein NAS.
Entweder als HTTP-Abfrage (`http://192.168.178.104:8050/api/state`) oder als
reiner TCP-Test (`192.168.178.104:8050` – „macht da überhaupt jemand auf?“).

Ein einziges Python-Skript, **nur Standardbibliothek** – kein `pip`, kein
`requests`, kein Compiler. Läuft ab Python 3.7 auf CoreELEC, Raspberry Pi,
NAS, Router oder einem beliebigen Linux-Rechner im selben Netz – oder
**als Docker-Container, vollständig über Umgebungsvariablen konfiguriert**.

---

## ⚠️ Vorher lesen

Die Steckdose darf **nicht die Box versorgen, auf der das beobachtete Ziel
läuft**. Sonst schaltet das Skript beim ersten Aussetzer den Strom ab, das
Ziel kommt nie wieder online, und die Steckdose bleibt für immer aus. Die
Shelly gehört an das, was *vom* Ziel abhängt – Display, Monitor, Beleuchtung,
Lautsprecher –, nicht an das, was es *trägt*.

---

## Wofür das gut ist

| Ziel | Adresse | Was die Shelly schaltet |
|---|---|---|
| LCD4Linux-Webdashboard (Kodi-Add-on [`script.lcd4linux`](https://github.com/CE-Repo/script.lcd4linux)) | `http://192.168.178.104:8050/api/state` | das angeschlossene Display |
| Kodi selbst (JSON-RPC) | `192.168.178.104:8080` | Verstärker, Soundbar |
| Ein PC / eine Workstation | `192.168.178.20:3389` | Monitor, Schreibtischlampe |
| Ein 3D-Drucker (OctoPrint) | `http://octopi:80` | Absaugung, Gehäuselicht |
| Ein Access Point | `192.168.178.1:443` | Repeater, Signalleuchte |

Der ursprüngliche Anlass war LCD4Linux – daher der alte Name. Im Skript ist
davon nichts übrig: es fragt eine Adresse ab, sonst nichts.

---

## Kommt von `lcd4linux-shelly`? Das ändert sich

Der Umstieg geht ohne Umkonfigurieren: **alle alten Namen werden weiter
gelesen**, die neuen haben Vorrang.

| Alt | Neu |
|---|---|
| `lcd4linux_shelly.py` | `shelly_netwatch.py` |
| `/etc/lcd4linux-shelly.ini` | `/etc/shelly-netwatch.ini` (die alte wird noch gefunden) |
| Abschnitt `[dashboard]` | `[target]` (der alte wird noch gelesen) |
| `DASHBOARD_URL`, `DASHBOARD_TIMEOUT`, … | `TARGET_URL`, `TARGET_TIMEOUT`, … |
| Präfix `L4LS_` | `SNW_` |
| `-d`, `--dashboard-url` | `-t`, `--target-url` (die alten Schalter bleiben) |
| Image/Container `lcd4linux-shelly` | `shelly-netwatch` |

Eine Sache ist nicht abwärtskompatibel: **es gibt keine eingebaute
Standardadresse mehr.** Früher stand `http://192.168.178.104:8050/api/state`
als Vorgabe im Skript – für ein allgemeines Werkzeug ist das Unsinn. Wer bisher
ohne gesetzte Adresse gestartet hat, trägt sie jetzt ein; ohne Ziel bricht der
Start mit einer Meldung ab, statt fremde IPs anzupingen.

---

## Docker

Das Image braucht keine Zustandsdaten, kein Volume und keine
Konfigurationsdatei – **alles wird über Umgebungsvariablen gesetzt**. Es
genügt Bridge-Netzwerk und es werden keine Ports veröffentlicht; der
Container baut nur ausgehende Verbindungen ins LAN auf, zum Ziel und
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
das Paket `shelly-netwatch-amd64`. GitHub packt Artefakte immer in ein
ZIP – dieses **entpacken**, herauskommt `shelly-netwatch-amd64.tar`
(rund 20 MB). Genau diese `.tar` will die Docker-App, nicht das ZIP.

**3. Auf die NAS legen.** Die Datei in eine Freigabe kopieren, zum Beispiel
über den Dateimanager von UGOS nach `/volume1/docker/`.

**4. Importieren.** Docker-App → **Image** → **`+`** → **Von NAS** → die
`.tar` auswählen. Danach steht `shelly-netwatch:latest` in der Liste.
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
| `TARGET_URL` | die Adresse des Ziels, z. B. `http://192.168.178.104:8050/api/state` oder `192.168.178.104:8050` |
| `SHELLY_HOST` | die IP der Shelly, z. B. `192.168.178.60` |

Alles Weitere ist optional und steht in der Tabelle unter
[Konfiguration](#konfiguration). Zum Ausprobieren lohnt ein erster Start
mit `COMMAND=test`: der Container schreibt dann ins Protokoll, ob er
Ziel und Steckdose erreicht, und beendet sich wieder. Läuft das
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
sudo mkdir -p /volume1/docker/shelly-netwatch
cd /volume1/docker/shelly-netwatch
sudo git clone https://github.com/CE-Repo/shelly-netwatch.git .
sudo nano docker-compose.yml     # TARGET_URL und SHELLY_HOST eintragen
sudo docker compose up -d --build
sudo docker compose logs -f
```

Die mitgelieferte `docker-compose.yml` listet alle Variablen mit Kommentar
auf. Vor dem Dauerbetrieb lohnt ein Blick, ob der Container beide Seiten
erreicht:

```bash
sudo docker compose run --rm shelly-netwatch test
```

Ein Archiv aus dem Actions-Workflow lässt sich auf demselben Weg
einspielen, ohne zu bauen:

```bash
sudo docker load -i shelly-netwatch-amd64.tar
```

Und ohne Compose, wenn das Image schon da ist:

```bash
docker run -d --name shelly-netwatch --restart unless-stopped \
    -e TZ=Europe/Berlin \
    -e TARGET_URL=http://192.168.178.104:8050/api/state \
    -e SHELLY_HOST=192.168.178.60 \
    shelly-netwatch:latest
```

Der Container läuft als Benutzer `shelly` (UID 1000), nicht als root, und
beendet sich auf `docker stop` innerhalb einer Sekunde sauber.

### Healthcheck

Der Watcher berührt nach jeder Abfrage eine Heartbeat-Datei; der eingebaute
Healthcheck prüft, ob diese frisch ist. **Gesund heißt: der Watcher arbeitet** –
nicht: das Ziel ist online. Ein ausgeschaltetes Ziel ist ein gültiges
Ergebnis und kein Fehler, der Container bleibt dabei `healthy`.

```bash
docker inspect -f '{{.State.Health.Status}}' shelly-netwatch
```

### Passwörter

Wer das Shelly- oder Ziel-Passwort nicht im Klartext in der
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
git clone https://github.com/CE-Repo/shelly-netwatch.git
cd shelly-netwatch

# Konfiguration anlegen und die beiden Adressen eintragen
sudo cp shelly-netwatch.example.ini /etc/shelly-netwatch.ini
sudo nano /etc/shelly-netwatch.ini

# Skript ablegen
sudo install -m 755 shelly_netwatch.py /usr/local/bin/shelly_netwatch.py
```

Anzupassen sind genau zwei Zeilen:

```ini
[target]
url  = http://192.168.178.104:8050/api/state

[shelly]
host = 192.168.178.60          # die IP der Shelly
```

Die IP der Shelly steht in der Shelly-App unter *Einstellungen → Gerätedaten*
oder im Router. Eine feste IP (DHCP-Reservierung) ist zu empfehlen.

### Erst einmal ausprobieren

```bash
shelly_netwatch.py test        # erreicht das Skript beide Seiten?
shelly_netwatch.py status      # was sagen Ziel und Steckdose gerade?
shelly_netwatch.py once -v     # eine Abfrage, einmal schalten
shelly_netwatch.py watch -v -n # zuschauen, ohne wirklich zu schalten
```

`test` meldet Modell, MAC und Firmware der Shelly – wenn das durchläuft,
stimmen Adresse und Passwort.

### Als Dienst (systemd)

```bash
sudo cp systemd/shelly-netwatch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shelly-netwatch
journalctl -u shelly-netwatch -f
```

### Auf CoreELEC / LibreELEC

Dort gibt es kein `/usr/local/bin` und kein normales systemd-Verzeichnis;
beides liegt unter `/storage`:

```bash
mkdir -p /storage/shelly-netwatch
cp shelly_netwatch.py shelly-netwatch.example.ini /storage/shelly-netwatch/
mv /storage/shelly-netwatch/shelly-netwatch.example.ini \
   /storage/shelly-netwatch/shelly-netwatch.ini
chmod +x /storage/shelly-netwatch/shelly_netwatch.py

cp systemd/shelly-netwatch.service /storage/.config/system.d/
nano /storage/.config/system.d/shelly-netwatch.service   # Pfade anpassen,
                                                         # DynamicUser entfernen
systemctl daemon-reload
systemctl enable --now shelly-netwatch
```

Läuft das Skript auf derselben Box wie das Ziel, genügt als Adresse
`http://127.0.0.1:8050/api/state`.

---

## Wie es arbeitet

Das Skript fragt das Ziel alle 10 Sekunden ab. Wie, entscheidet die Adresse:

| `url` | Prüfung |
|---|---|
| `http://box:8050/api/state` | HTTP GET, der Status zählt |
| `https://box/` | dasselbe über TLS |
| `box:8050` (ohne Schema, ohne Pfad) | **TCP**: Verbindung auf den Port, sonst nichts |
| `tcp://box:8050` | dasselbe, ausgeschrieben |
| `box:8050/api/state` | Pfad vorhanden → HTTP |

Der TCP-Test ist die kleinste mögliche Prüfung und funktioniert bei allem,
was kein HTTP spricht – SSH, SMB, Drucker, Spieleserver. Bei HTTP lohnt
ein sparsamer Endpunkt; bei LCD4Linux ist das `/api/state`, ein paar hundert
Byte JSON statt der ganzen Seite.

Aus der Antwort wird ein Zustand:

| Antwort | Bewertung |
|---|---|
| HTTP 200 | online |
| HTTP 401 (passwortgeschützt) | online – der Server antwortet ja |
| TCP-Verbindung kommt zustande | online |
| Verbindung abgelehnt, Timeout, DNS-Fehler | offline |
| HTTP 5xx | offline |

Geschaltet wird erst, wenn sich der Zustand **wirklich** geändert hat:

* **einschalten** nach `online_after` guten Abfragen (Standard 1 – sofort),
* **ausschalten** nach `offline_after` fehlgeschlagenen Abfragen
  (Standard 3, also nach ~30 s).

Damit übersteht die Steckdose einen Neustart des Dienstes, ein kurz
überlastetes WLAN oder einen Reload, ohne zu klackern. Solange sich
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
shelly_netwatch.py [Optionen] [watch|once|status|on|off|test|health]
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
* * * * * /usr/local/bin/shelly_netwatch.py once >> /var/log/shelly-netwatch.log 2>&1
```

Da `once` den vorherigen Zustand nicht kennt, schaltet es bei jedem Lauf
einmal – für den Dauerbetrieb ist `watch` die bessere Wahl.

---

## Konfiguration

Jede Einstellung lässt sich auf drei Wegen setzen. Wer gewinnt, steht weiter
unten; eingebaute Vorgaben < INI-Datei < **Umgebung** < Kommandozeile.

| Umgebungsvariable | INI | Kommandozeile | Standard | Bedeutung |
|---|---|---|---|---|
| `TARGET_URL` | `target.url` | `-t`, `--target-url` | – | Adresse des Ziels: URL oder `HOST:PORT` |
| `TARGET_INTERVAL` | `target.interval` | `-i`, `--interval` | `10` | Sekunden zwischen zwei Abfragen |
| `TARGET_TIMEOUT` | `target.timeout` | `--timeout` | `4` | Zeitlimit einer Abfrage |
| `TARGET_ONLINE_AFTER` | `target.online_after` | `--online-after` | `1` | gute Abfragen bis „an“ |
| `TARGET_OFFLINE_AFTER` | `target.offline_after` | `--offline-after` | `3` | Fehlversuche bis „aus“ |
| `TARGET_USERNAME` | `target.username` | – | `shelly-netwatch` | Benutzername für HTTP Basic (bei LCD4Linux beliebig) |
| `TARGET_PASSWORD` | `target.password` | `--target-password` | – | Passwort des Ziels |
| `TARGET_ACCEPT_STATUS` | `target.accept_status` | – | `200,401` | HTTP-Status, die als online gelten; `any` = jede Antwort |
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

Drei Dinge gelten für **jeden** dieser Namen:

* **`…_FILE`** – statt `SHELLY_PASSWORD` kann `SHELLY_PASSWORD_FILE` auf eine
  Datei zeigen, deren Inhalt der Wert ist. Praktisch für Docker-Secrets, und
  der Wert taucht nicht in `docker inspect` auf. Die `…_FILE`-Variante hat
  Vorrang vor dem direkten Wert.
* **`SNW_`-Präfix** – `SNW_SHELLY_HOST` wird genauso gelesen wie
  `SHELLY_HOST` und geht vor. Nur nötig, wenn ein schlichter Name wie
  `VERBOSE` außerhalb eines Containers mit etwas anderem kollidiert.
* **Alte Namen** – `DASHBOARD_*` und `L4LS_*` werden weiterhin gelesen,
  verlieren aber gegen die neuen.

Eine INI-Datei ist nirgends Pflicht. Ohne `--config` und ohne `CONFIG_FILE`
sucht das Skript der Reihe nach `./shelly-netwatch.ini`,
`~/.config/shelly-netwatch.ini` und `/etc/shelly-netwatch.ini` (und danach
dieselben Pfade mit dem alten Namen `lcd4linux-shelly.ini`); findet es keine,
gelten Vorgaben und Umgebung. `shelly-netwatch.example.ini` zeigt alle Werte
in Dateiform.

---

## Fehlersuche

| Meldung | Ursache |
|---|---|
| `no target configured` | `TARGET_URL` bzw. `target.url` ist leer – eine Standardadresse gibt es nicht mehr. |
| `a target without a scheme needs a port` | `192.168.178.104` allein reicht nicht, der Port gehört dazu: `192.168.178.104:8050`. |
| `Connection refused` | Am Ziel lauscht nichts: Dienst aus, falscher Port, oder er hört nur auf `127.0.0.1`. Bei LCD4Linux: *Einstellungen → Web → Web-Editor aktiv*. |
| Ziel nur lokal erreichbar | Der Dienst bindet auf `127.0.0.1`. Auf „alle Schnittstellen“ stellen oder das Skript auf derselben Box laufen lassen. |
| `HTTP 401` als offline gewertet | `accept_status` enthält keine 401 – entweder 401 ergänzen oder `target.password` setzen. |
| `the Shelly requires a password` | In der Shelly ist Authentifizierung aktiv: `shelly.password` eintragen. |
| `the Shelly rejected the password` | Falsches Passwort. Benutzername ist immer `admin`. |
| `unknown method Switch.Set` | Ein Gen1-Gerät. Die Gen1-Geräte sprechen `/relay/0?turn=on` statt RPC und werden hier nicht unterstützt. |
| Steckdose klackert | `offline_after` erhöhen oder `interval` verlängern. |
| Container erreicht das Ziel nicht | Aus dem Container heraus prüfen: `docker compose run --rm shelly-netwatch test`. Adressen müssen die des LAN sein – `127.0.0.1` zeigt im Container auf den Container selbst. |
| Container ist `unhealthy` | Der Watcher hat länger als das Dreifache eines Abfragezyklus nichts mehr getan. Ins Log sehen; ein offline gemeldetes Ziel allein löst das nicht aus. |
| Zeitstempel im Log gehen falsch | `TZ=Europe/Berlin` setzen. |

Mehr Details liefert `VERBOSE=true` bzw. `-v`, nachzulesen mit
`docker compose logs -f` oder `journalctl -u shelly-netwatch -f`.

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```
