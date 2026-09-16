# shelly-netwatch

Switches a **Shelly (Gen2/Gen3)** by whether a service on the network answers:

* target reachable → **socket on**
* target gone → **socket off**

The target is anything that answers on an **IP and a port** – a web dashboard,
a media player, a printer, an access point, a game server, a NAS. Either as an
HTTP poll (`http://192.168.178.104:8050/api/state`) or as a plain TCP check
(`192.168.178.104:8050` – "is anything listening there at all?").

A single Python script, **standard library only** – no `pip`, no `requests`,
no compiler. Runs on Python 3.7 and up on CoreELEC, a Raspberry Pi, a NAS, a
router or any other Linux box on the same network – or **as a Docker
container, configured entirely through environment variables**.

---

## ⚠️ Read this first

The socket must **not power the box the watched target runs on**. Otherwise
the script cuts the power at the first hiccup, the target never comes back
online, and the socket stays off forever. The Shelly belongs on whatever
depends *on* the target – display, monitor, lighting, speakers – not on what
*carries* it.

---

## What this is good for

| Target | Address | What the Shelly switches |
|---|---|---|
| LCD4Linux web dashboard (Kodi add-on [`script.lcd4linux`](https://github.com/CE-Repo/script.lcd4linux)) | `http://192.168.178.104:8050/api/state` | the attached display |
| Kodi itself (JSON-RPC) | `192.168.178.104:8080` | amplifier, soundbar |
| A PC or workstation | `192.168.178.20:3389` | monitor, desk lamp |
| A 3D printer (OctoPrint) | `http://octopi:80` | fume extraction, enclosure light |
| An access point | `192.168.178.1:443` | repeater, status lamp |

LCD4Linux was the original reason this exists – hence the old name. Nothing
of it is left in the script: it polls one address, and that is all.

---

## Coming from `lcd4linux-shelly`? What changes

Upgrading needs no reconfiguration: **every old name is still read**, the new
ones simply win.

| Old | New |
|---|---|
| `lcd4linux_shelly.py` | `shelly_netwatch.py` |
| `/etc/lcd4linux-shelly.ini` | `/etc/shelly-netwatch.ini` (the old one is still found) |
| section `[dashboard]` | `[target]` (the old one is still read) |
| `DASHBOARD_URL`, `DASHBOARD_TIMEOUT`, … | `TARGET_URL`, `TARGET_TIMEOUT`, … |
| prefix `L4LS_` | `SNW_` |
| `-d`, `--dashboard-url` | `-t`, `--target-url` (the old switches remain) |
| image/container `lcd4linux-shelly` | `shelly-netwatch` |

One thing is not backwards compatible: **there is no built-in default address
any more.** `http://192.168.178.104:8050/api/state` used to be the default in
the script, which is nonsense for a general-purpose tool. If you used to start
it without setting an address, set one now; without a target the script exits
with a message instead of poking at somebody else's IP.

---

## Docker

The image keeps no state, needs no volume and no configuration file –
**everything is set through environment variables**. Bridge networking is
enough and no ports are published; the container only makes outgoing
connections into the LAN, to the target and to the socket.

### Have the image built and import it on the NAS

The recommended route for a UGREEN NAS: GitHub builds the image, the Docker
app imports the finished file. No registry, no login, no compiler on the NAS.

**1. Build it.** In the repository go to *Actions* → workflow
*Tests und Image* → **Run workflow**. Two fields to choose:

| Field | For the DXP2800 |
|---|---|
| `platform` | `linux/amd64` – the DXP2800 has an Intel N100 |
| `tag` | `latest`, unless you want something else |

Tests run first, then the build; together that takes a good minute.

> GitHub only shows the *Run workflow* button for workflows that live on the
> default branch. As long as the file only exists in a feature branch, that
> branch has to reach `main` first.

**2. Download it.** At the bottom of the run's page, under *Artifacts*, sits
the package `shelly-netwatch-amd64`. GitHub always wraps artifacts in a ZIP –
**unpack it**, and out comes `shelly-netwatch-amd64.tar` (about 20 MB). That
`.tar` is what the Docker app wants, not the ZIP.

**3. Put it on the NAS.** Copy the file into a share, for example via the UGOS
file manager to `/volume1/docker/`.

**4. Import it.** Docker app → **Image** → **`+`** → **From NAS** → pick the
`.tar`. Afterwards `shelly-netwatch:latest` is in the list. (*From package
source* next to it pulls from a registry – not needed here.)

**5. Create the container.** Build a container from the imported image and set:

* restart behaviour: **always restart**
* network: **bridge** (the default), no port forwarding needed
* environment variables – at least these three:

| Variable | Value |
|---|---|
| `TZ` | `Europe/Berlin` |
| `TARGET_URL` | the target's address, e.g. `http://192.168.178.104:8050/api/state` or `192.168.178.104:8050` |
| `SHELLY_HOST` | the Shelly's IP, e.g. `192.168.178.60` |

Everything else is optional and listed under
[Configuration](#configuration). For a first try, start with
`COMMAND=test`: the container then writes to the log whether it reaches the
target and the socket, and exits again. Once that works, remove the variable
(or set it to `watch`) and start the container for good.

Updating works the same way: run the workflow, import the new `.tar`, recreate
the container.

### Alternatively: over SSH

If you prefer the command line, build the image directly on the NAS – there
are no compiled dependencies, so even the N100 takes only seconds:

```bash
ssh <user>@<nas-ip>
sudo mkdir -p /volume1/docker/shelly-netwatch
cd /volume1/docker/shelly-netwatch
sudo git clone https://github.com/jamal2362/shelly-netwatch.git .
sudo nano docker-compose.yml     # fill in TARGET_URL and SHELLY_HOST
sudo docker compose up -d --build
sudo docker compose logs -f
```

The bundled `docker-compose.yml` lists every variable with a comment. Before
running it permanently, check that the container reaches both sides:

```bash
sudo docker compose run --rm shelly-netwatch test
```

An archive from the Actions workflow can be loaded the same way, without
building:

```bash
sudo docker load -i shelly-netwatch-amd64.tar
```

And without Compose, once the image is there:

```bash
docker run -d --name shelly-netwatch --restart unless-stopped \
    -e TZ=Europe/Berlin \
    -e TARGET_URL=http://192.168.178.104:8050/api/state \
    -e SHELLY_HOST=192.168.178.60 \
    shelly-netwatch:latest
```

The container runs as user `shelly` (UID 1000), not as root, and shuts down
cleanly within a second on `docker stop`.

### Health check

The watcher touches a heartbeat file after every poll; the built-in health
check tests whether that file is fresh. **Healthy means: the watcher is
working** – not: the target is online. A switched-off target is a valid
result, not an error, and the container stays `healthy` through it.

```bash
docker inspect -f '{{.State.Health.Status}}' shelly-netwatch
```

### Passwords

If you would rather not have the Shelly or target password in clear text in
the Compose file, put it in a file and point at it with `…_FILE`:

```yaml
    environment:
      SHELLY_PASSWORD_FILE: /run/secrets/shelly_password
    secrets:
      - shelly_password

secrets:
  shelly_password:
    file: ./shelly_password.txt
```

This works for every variable in the table below: each name has a `…_FILE`
variant, and that variant wins.

---

## Installation without Docker

```bash
git clone https://github.com/jamal2362/shelly-netwatch.git
cd shelly-netwatch

# create the configuration and fill in the two addresses
sudo cp shelly-netwatch.example.ini /etc/shelly-netwatch.ini
sudo nano /etc/shelly-netwatch.ini

# install the script
sudo install -m 755 shelly_netwatch.py /usr/local/bin/shelly_netwatch.py
```

Exactly two lines need changing:

```ini
[target]
url  = http://192.168.178.104:8050/api/state

[shelly]
host = 192.168.178.60          # the Shelly's IP
```

The Shelly's IP is in the Shelly app under *Settings → Device information*,
or in the router. A fixed IP (DHCP reservation) is recommended.

### Try it first

```bash
shelly_netwatch.py test        # does the script reach both sides?
shelly_netwatch.py status      # what do the target and the socket say right now?
shelly_netwatch.py once -v     # one poll, switch once
shelly_netwatch.py watch -v -n # watch along without actually switching
```

`test` reports the Shelly's model, MAC and firmware – if that goes through,
address and password are right.

### As a service (systemd)

```bash
sudo cp systemd/shelly-netwatch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shelly-netwatch
journalctl -u shelly-netwatch -f
```

### On CoreELEC / LibreELEC

There is no `/usr/local/bin` and no normal systemd directory there; both live
under `/storage`:

```bash
mkdir -p /storage/shelly-netwatch
cp shelly_netwatch.py shelly-netwatch.example.ini /storage/shelly-netwatch/
mv /storage/shelly-netwatch/shelly-netwatch.example.ini \
   /storage/shelly-netwatch/shelly-netwatch.ini
chmod +x /storage/shelly-netwatch/shelly_netwatch.py

cp systemd/shelly-netwatch.service /storage/.config/system.d/
nano /storage/.config/system.d/shelly-netwatch.service   # adjust the paths,
                                                         # drop DynamicUser
systemctl daemon-reload
systemctl enable --now shelly-netwatch
```

If the script runs on the same box as the target, `http://127.0.0.1:8050/api/state`
is address enough.

---

## How it works

The script polls the target every 10 seconds. How it polls is decided by the
address:

| `url` | Check |
|---|---|
| `http://box:8050/api/state` | HTTP GET, the status code counts |
| `https://box/` | the same over TLS |
| `box:8050` (no scheme, no path) | **TCP**: connect to the port, nothing more |
| `tcp://box:8050` | the same, spelled out |
| `box:8050/api/state` | a path is present → HTTP |

The TCP check is the smallest possible test and works for everything that
speaks no HTTP – SSH, SMB, printers, game servers. For HTTP a cheap endpoint
pays off; with LCD4Linux that is `/api/state`, a few hundred bytes of JSON
instead of the whole page.

The answer becomes a state:

| Answer | Verdict |
|---|---|
| HTTP 200 | online |
| HTTP 401 (password protected) | online – the server is answering, after all |
| TCP connection succeeds | online |
| connection refused, timeout, DNS error | offline |
| HTTP 5xx | offline |

Switching only happens once the state has **really** changed:

* **on** after `online_after` good polls (default 1 – immediately),
* **off** after `offline_after` failed polls (default 3, so after ~30 s).

That way the socket survives a service restart, a briefly congested Wi-Fi or
a reload without clacking. As long as nothing changes, no command goes to the
Shelly either.

Every 5 minutes (`resync_interval`) the script additionally checks whether the
socket is still where it should be – if somebody flipped it through the app or
the button, it is put back. To keep manual switching, set
`resync_interval = 0`.

At startup the state is unknown; the first clear poll puts the socket in the
right position. On shutdown it is left alone (`on_exit = keep`), but can be set
to `off` or `on`.

### Talking to the Shelly

Gen2/Gen3 devices speak JSON-RPC over `POST /rpc`:

```
Switch.Set        {"id": 0, "on": true}    switches
Switch.GetStatus  {"id": 0}                reads the state back
Shelly.GetDeviceInfo                       model, MAC, firmware
```

If a password is set on the device under *Settings → Authentication*, the
script uses HTTP digest with SHA-256. The username on a Shelly is always
`admin`; only `shelly.password` needs filling in. Shelly deviates from the RFC
in its digest scheme (HA2 is the fixed string `dummy_method:dummy_uri`) – the
script knows both variants and tries them in turn.

Failed switch commands are retried up to `retries` times with a growing pause;
if it still does not work, a line lands in the log and the next poll tries
again. The watcher stays alive either way.

---

## Commands

```
shelly_netwatch.py [options] [watch|once|status|on|off|test|health]
```

| Command | Effect |
|---|---|
| `watch` | keep polling, the default |
| `once` | one poll, switch if needed, done – for `cron` |
| `status` | one line per side; exit code 0 = online, 2 = offline |
| `on` / `off` | switch the socket by hand |
| `test` | connection test with device information |
| `health` | the container health check, see above |

In the container the command is chosen through `COMMAND`, e.g. `COMMAND=test`.

For `cron` instead of a service:

```cron
* * * * * /usr/local/bin/shelly_netwatch.py once >> /var/log/shelly-netwatch.log 2>&1
```

Since `once` does not know the previous state, it switches on every run – for
continuous operation `watch` is the better choice.

---

## Configuration

Every setting can be given in three ways. Who wins is below; built-in defaults
< INI file < **environment** < command line.

| Environment variable | INI | Command line | Default | Meaning |
|---|---|---|---|---|
| `TARGET_URL` | `target.url` | `-t`, `--target-url` | – | the target's address: URL or `HOST:PORT` |
| `TARGET_INTERVAL` | `target.interval` | `-i`, `--interval` | `10` | seconds between two polls |
| `TARGET_TIMEOUT` | `target.timeout` | `--timeout` | `4` | how long a poll may take |
| `TARGET_ONLINE_AFTER` | `target.online_after` | `--online-after` | `1` | good polls before switching on |
| `TARGET_OFFLINE_AFTER` | `target.offline_after` | `--offline-after` | `3` | failed polls before switching off |
| `TARGET_USERNAME` | `target.username` | – | `shelly-netwatch` | username for HTTP basic auth (any value with LCD4Linux) |
| `TARGET_PASSWORD` | `target.password` | `--target-password` | – | the target's password |
| `TARGET_ACCEPT_STATUS` | `target.accept_status` | – | `200,401` | HTTP status codes counting as online; `any` = every answer |
| `SHELLY_HOST` | `shelly.host` | `-s`, `--shelly-host` | – | the Shelly's address |
| `SHELLY_CHANNEL` | `shelly.channel` | `--channel` | `0` | switch channel |
| `SHELLY_USERNAME` | `shelly.username` | – | `admin` | always `admin` on a Shelly |
| `SHELLY_PASSWORD` | `shelly.password` | `--shelly-password` | – | the Shelly's password |
| `SHELLY_TIMEOUT` | `shelly.timeout` | – | `5` | time limit for a switch command |
| `SHELLY_RETRIES` | `shelly.retries` | – | `3` | retries on failure |
| `RESYNC_INTERVAL` | `behaviour.resync_interval` | `--resync-interval` | `300` | correction pass, `0` = off |
| `ON_EXIT` | `behaviour.on_exit` | `--on-exit` | `keep` | `keep`, `off` or `on` when stopping |
| `DRY_RUN` | `behaviour.dry_run` | `-n`, `--dry-run` | `false` | only log, never switch |
| `VERBOSE` | `behaviour.verbose` | `-v`, `--verbose` | `false` | log every poll |
| `LOG_FILE` | `behaviour.log_file` | `--log-file` | – | into a file instead of stdout |
| `HEARTBEAT_FILE` | `behaviour.heartbeat_file` | `--heartbeat-file` | – | file for the health check (set in the image) |
| `COMMAND` | – | positional argument | `watch` | which command runs |
| `CONFIG_FILE` | – | `-c`, `--config` | – | path to the INI file |

Three things hold for **every** one of these names:

* **`…_FILE`** – instead of `SHELLY_PASSWORD`, `SHELLY_PASSWORD_FILE` can point
  at a file whose contents are the value. Handy for Docker secrets, and the
  value does not show up in `docker inspect`. The `…_FILE` variant wins over
  the direct value.
* **`SNW_` prefix** – `SNW_SHELLY_HOST` is read just like `SHELLY_HOST` and
  takes precedence. Only needed when a plain name like `VERBOSE` collides with
  something else outside a container.
* **Old names** – `DASHBOARD_*` and `L4LS_*` are still read, but lose to the
  new ones.

An INI file is nowhere mandatory. Without `--config` and without
`CONFIG_FILE`, the script looks for `./shelly-netwatch.ini`,
`~/.config/shelly-netwatch.ini` and `/etc/shelly-netwatch.ini` in that order
(and then the same paths under the old name `lcd4linux-shelly.ini`); if it
finds none, defaults and the environment apply. `shelly-netwatch.example.ini`
shows every value in file form.

---

## Troubleshooting

| Message | Cause |
|---|---|
| `no target configured` | `TARGET_URL` or `target.url` is empty – there is no default address any more. |
| `a target without a scheme needs a port` | `192.168.178.104` alone is not enough, the port belongs to it: `192.168.178.104:8050`. |
| `Connection refused` | Nothing is listening at the target: service off, wrong port, or it only listens on `127.0.0.1`. With LCD4Linux: *Settings → Web → web editor enabled*. |
| Target only reachable locally | The service binds to `127.0.0.1`. Switch it to "all interfaces" or run the script on the same box. |
| `HTTP 401` counted as offline | `accept_status` does not contain 401 – either add 401 or set `target.password`. |
| `the Shelly requires a password` | Authentication is enabled on the Shelly: fill in `shelly.password`. |
| `the Shelly rejected the password` | Wrong password. The username is always `admin`. |
| `unknown method Switch.Set` | A Gen1 device. Gen1 speaks `/relay/0?turn=on` instead of RPC and is not supported here. |
| The socket clacks | Raise `offline_after` or lengthen `interval`. |
| The container does not reach the target | Check from inside the container: `docker compose run --rm shelly-netwatch test`. Addresses have to be LAN addresses – inside a container `127.0.0.1` points at the container itself. |
| The container is `unhealthy` | The watcher has done nothing for more than three poll cycles. Look at the log; a target reported offline does not by itself trigger this. |
| Timestamps in the log are wrong | Set `TZ=Europe/Berlin`. |

`VERBOSE=true` or `-v` gives more detail, readable with
`docker compose logs -f` or `journalctl -u shelly-netwatch -f`.

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```
