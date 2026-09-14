#!/usr/bin/env python3
"""Keep a Shelly Plug in sync with the LCD4Linux web dashboard.

The Kodi add-on ``script.lcd4linux`` serves its web dashboard on port 8050.
This watcher polls that dashboard and mirrors its reachability onto the
relay of a Shelly Plug (Gen2/Gen3 RPC API):

    dashboard reachable  ->  plug switches the 230 V on
    dashboard gone       ->  plug switches the 230 V off

Only the standard library is used, so the same file runs on CoreELEC, a
Raspberry Pi, a NAS or any other box with Python 3.7 or newer.
"""

import argparse
import base64
import configparser
import hashlib
import json
import logging
import os
import random
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

__version__ = "1.0.0"

LOG = logging.getLogger("lcd4linux-shelly")

DEFAULTS = {
    "dashboard": {
        "url": "http://192.168.178.104:8050/api/state",
        "timeout": "4",
        "interval": "10",
        "online_after": "1",
        "offline_after": "3",
        "username": "lcd4linux",
        "password": "",
        "accept_status": "200,401",
    },
    "shelly": {
        "host": "",
        "channel": "0",
        "username": "admin",
        "password": "",
        "timeout": "5",
        "retries": "3",
    },
    "behaviour": {
        "resync_interval": "300",
        "on_exit": "keep",
        "dry_run": "false",
    },
}

TRUE_WORDS = ("1", "true", "yes", "on", "ja", "wahr")


def as_bool(value):
    """Read a configuration flag the way a human would write it."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE_WORDS


def build_opener():
    """An opener that ignores http_proxy: the plug and the box are on the LAN."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------

class Probe(object):
    """Asks the LCD4Linux web dashboard whether it is still there."""

    def __init__(self, url, timeout=4.0, username="", password="",
                 accept_status="200,401"):
        self.url = url
        self.timeout = float(timeout)
        self.username = username or "lcd4linux"
        self.password = password or ""
        self.accept = self._parse_accept(accept_status)
        self._opener = build_opener()

    @staticmethod
    def _parse_accept(spec):
        if isinstance(spec, (set, frozenset, list, tuple)):
            return set(int(item) for item in spec)
        text = str(spec).strip().lower()
        if text in ("", "any", "all", "*"):
            return None  # any answer at all counts as "the server is up"
        codes = set()
        for part in text.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                codes.add(int(part))
        return codes or {200}

    def _request(self):
        request = urllib.request.Request(self.url, method="GET")
        request.add_header("User-Agent", "lcd4linux-shelly/%s" % __version__)
        if self.password:
            token = "%s:%s" % (self.username, self.password)
            request.add_header("Authorization", "Basic %s"
                               % base64.b64encode(token.encode("utf-8")).decode("ascii"))
        return self._opener.open(request, timeout=self.timeout)

    def check(self):
        """Return ``(online, reason)`` for a single poll."""
        try:
            response = self._request()
        except urllib.error.HTTPError as err:
            # The server answered, it just did not like the request.  A 401
            # means the dashboard is up and asking for its password, which
            # is still "online" as far as the plug is concerned.
            err.read()
            code = err.code
            if self.accept is None or code in self.accept:
                return True, "HTTP %d" % code
            return False, "HTTP %d" % code
        except urllib.error.URLError as err:
            return False, str(err.reason)
        except Exception as err:  # timeouts, resets, DNS, ...
            return False, str(err)
        try:
            code = response.getcode()
            response.read(2048)
        finally:
            response.close()
        if self.accept is None or code in self.accept:
            return True, "HTTP %d" % code
        return False, "HTTP %d" % code


# ---------------------------------------------------------------------------
# Shelly Gen2/Gen3 RPC
# ---------------------------------------------------------------------------

class ShellyError(Exception):
    pass


def parse_challenge(header):
    """Split a ``WWW-Authenticate: Digest ...`` header into its fields."""
    scheme, _, rest = (header or "").partition(" ")
    if scheme.lower() != "digest":
        return None
    fields = {}
    pattern = r'([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(?:"([^"]*)"|([^,\s]*))'
    for match in re.finditer(pattern, rest):
        value = match.group(2) if match.group(2) is not None else match.group(3)
        fields[match.group(1).lower()] = value
    return fields


def digest_header(challenge, username, password, method, uri, shelly_quirk=True):
    """Build an ``Authorization: Digest`` header for a Shelly.

    Shelly's Gen2+ firmware does not hash the request line the way RFC 7616
    describes; it uses the literal string ``dummy_method:dummy_uri`` for HA2.
    ``shelly_quirk=False`` falls back to the standard so the script also
    talks to a firmware that ever changes its mind.
    """
    algorithm = (challenge.get("algorithm") or "SHA-256").upper()
    if algorithm.startswith("SHA-256"):
        hasher = hashlib.sha256
    elif algorithm.startswith("SHA-512"):
        hasher = hashlib.sha512
    elif algorithm.startswith("MD5"):
        hasher = hashlib.md5
    else:
        raise ShellyError("unsupported digest algorithm %s" % algorithm)

    def H(text):
        return hasher(text.encode("utf-8")).hexdigest()

    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop = "auth"
    nc = "00000001"
    cnonce = "%08x" % random.getrandbits(32)
    ha1 = H("%s:%s:%s" % (username, realm, password))
    ha2 = H("dummy_method:dummy_uri") if shelly_quirk else H("%s:%s" % (method, uri))
    response = H(":".join([ha1, nonce, nc, cnonce, qop, ha2]))
    return ('Digest username="%s", realm="%s", nonce="%s", uri="%s", '
            'qop=%s, nc=%s, cnonce="%s", response="%s", algorithm=%s'
            % (username, realm, nonce, uri, qop, nc, cnonce, response, algorithm))


class Shelly(object):
    """The bit of the Shelly RPC API this script needs."""

    def __init__(self, host, channel=0, username="admin", password="",
                 timeout=5.0, retries=3):
        self.host = self._normalise(host)
        self.channel = int(channel)
        self.username = username or "admin"
        self.password = password or ""
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self.path = "/rpc"
        self.url = self.host.rstrip("/") + self.path
        self._opener = build_opener()
        self._id = 0

    @staticmethod
    def _normalise(host):
        host = (host or "").strip()
        if not host:
            raise ShellyError("no Shelly address configured")
        if "://" not in host:
            host = "http://" + host
        return host.rstrip("/")

    def _post(self, body, auth=None):
        request = urllib.request.Request(self.url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", "lcd4linux-shelly/%s" % __version__)
        if auth:
            request.add_header("Authorization", auth)
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), err.headers
        try:
            return response.getcode(), response.read(), response.headers
        finally:
            response.close()

    def _call_once(self, method, params):
        self._id += 1
        payload = {"id": self._id, "src": "lcd4linux-shelly", "method": method}
        if params:
            payload["params"] = params
        body = json.dumps(payload).encode("utf-8")

        code, data, headers = self._post(body)
        if code == 401:
            challenge = parse_challenge(headers.get("WWW-Authenticate", ""))
            if not challenge:
                raise ShellyError("the Shelly asked for authentication "
                                  "without a digest challenge")
            if not self.password:
                raise ShellyError("the Shelly requires a password "
                                  "(set shelly.password)")
            for quirk in (True, False):
                auth = digest_header(challenge, self.username, self.password,
                                     "POST", self.path, shelly_quirk=quirk)
                code, data, headers = self._post(body, auth)
                if code != 401:
                    break
        if code == 401:
            raise ShellyError("the Shelly rejected the password")
        if code >= 400:
            raise ShellyError("HTTP %d from %s" % (code, self.url))
        try:
            answer = json.loads(data.decode("utf-8"))
        except ValueError as err:
            raise ShellyError("the Shelly did not answer with JSON: %s" % err)
        if isinstance(answer, dict) and answer.get("error"):
            error = answer["error"]
            raise ShellyError("%s: %s" % (method, error.get("message", error)))
        return (answer or {}).get("result", {})

    def call(self, method, params=None):
        """One RPC call, retried a few times before giving up."""
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                return self._call_once(method, params)
            except ShellyError as err:
                last = err
                if "password" in str(err) or "rejected" in str(err):
                    break  # a wrong password will not fix itself
            except Exception as err:
                last = ShellyError("%s: %s" % (method, err))
            if attempt < self.retries:
                delay = min(2 ** (attempt - 1), 4)
                LOG.debug("retrying %s in %ss (%s)", method, delay, last)
                time.sleep(delay)
        raise last

    # -- the two things the watcher wants ---------------------------------
    def set_output(self, on):
        result = self.call("Switch.Set", {"id": self.channel, "on": bool(on)})
        return result.get("was_on")

    def output(self):
        status = self.call("Switch.GetStatus", {"id": self.channel})
        return bool(status.get("output"))

    def info(self):
        return self.call("Shelly.GetDeviceInfo")


# ---------------------------------------------------------------------------
# the watcher itself
# ---------------------------------------------------------------------------

class Watcher(object):
    """Polls the dashboard and switches the plug when the answer changes.

    A single missed poll does not cut the power: ``offline_after`` failures
    in a row are needed before the plug goes off, which rides out the short
    gaps a reloading add-on or a busy network produces.
    """

    def __init__(self, probe, shelly, interval=10.0, online_after=1,
                 offline_after=3, resync_interval=300.0, dry_run=False,
                 on_exit="keep"):
        self.probe = probe
        self.shelly = shelly
        self.interval = float(interval)
        self.online_after = max(1, int(online_after))
        self.offline_after = max(1, int(offline_after))
        self.resync_interval = float(resync_interval)
        self.dry_run = bool(dry_run)
        self.on_exit = on_exit
        self.state = None        # what we believe the plug should be
        self.up = 0
        self.down = 0
        self._last_resync = 0.0
        self._stop = threading.Event()

    # -- switching ---------------------------------------------------------
    def _switch(self, on, why):
        what = "on" if on else "off"
        if self.dry_run:
            LOG.info("[dry-run] would switch the plug %s (%s)", what, why)
            return True
        try:
            self.shelly.set_output(on)
        except ShellyError as err:
            LOG.error("cannot switch the plug %s: %s", what, err)
            return False
        LOG.info("plug switched %s (%s)", what, why)
        return True

    def _resync(self):
        """Put the plug back where it belongs if somebody moved it."""
        if self.state is None or self.dry_run:
            return
        try:
            actual = self.shelly.output()
        except ShellyError as err:
            LOG.warning("cannot read the plug: %s", err)
            return
        if actual != self.state:
            LOG.info("the plug is %s but should be %s - correcting",
                     "on" if actual else "off", "on" if self.state else "off")
            self._switch(self.state, "resync")

    # -- one turn of the loop ---------------------------------------------
    def step(self):
        online, reason = self.probe.check()
        if online:
            self.up += 1
            self.down = 0
        else:
            self.down += 1
            self.up = 0
        LOG.debug("dashboard %s (%s), up=%d down=%d",
                  "online" if online else "offline", reason, self.up, self.down)

        target = None
        if online and self.state is not True and self.up >= self.online_after:
            target = True
        elif not online and self.state is not False and self.down >= self.offline_after:
            target = False

        if target is not None:
            if self.state is None:
                why = "dashboard is %s" % ("online" if target else "offline")
            else:
                why = "dashboard went %s" % ("online" if target else "offline")
            LOG.info("%s: %s", why, reason)
            if self._switch(target, reason):
                self.state = target
                self._last_resync = time.time()
            return target

        if self.resync_interval > 0 and self.state is not None:
            if time.time() - self._last_resync >= self.resync_interval:
                self._last_resync = time.time()
                self._resync()
        return None

    # -- the loop ----------------------------------------------------------
    def stop(self, *_args):
        self._stop.set()

    def run(self):
        LOG.info("watching %s every %.0fs, plug at %s (channel %d)",
                 self.probe.url, self.interval, self.shelly.host,
                 self.shelly.channel)
        LOG.info("switching off after %d failed poll(s), on after %d good one(s)",
                 self.offline_after, self.online_after)
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as err:  # never let the loop die
                LOG.error("unexpected error: %s", err)
                LOG.debug("", exc_info=True)
            self._stop.wait(self.interval)
        self._finish()
        return 0

    def _finish(self):
        if self.on_exit == "off":
            self._switch(False, "watcher stopping")
        elif self.on_exit == "on":
            self._switch(True, "watcher stopping")
        else:
            LOG.info("watcher stopped, the plug is left as it is")


# ---------------------------------------------------------------------------
# configuration and command line
# ---------------------------------------------------------------------------

def load_config(path=None):
    """Defaults, overlaid with an INI file when there is one."""
    parser = configparser.ConfigParser()
    parser.read_dict(DEFAULTS)
    if path:
        if not os.path.isfile(path):
            raise SystemExit("configuration file not found: %s" % path)
        parser.read(path, encoding="utf-8")
    else:
        for candidate in ("lcd4linux-shelly.ini",
                          os.path.expanduser("~/.config/lcd4linux-shelly.ini"),
                          "/etc/lcd4linux-shelly.ini"):
            if os.path.isfile(candidate):
                parser.read(candidate, encoding="utf-8")
                LOG.debug("configuration read from %s", candidate)
                break
    return parser


def apply_overrides(config, args):
    """Command line beats configuration file, and both beat the defaults."""
    mapping = {
        "dashboard_url": ("dashboard", "url"),
        "dashboard_password": ("dashboard", "password"),
        "interval": ("dashboard", "interval"),
        "timeout": ("dashboard", "timeout"),
        "online_after": ("dashboard", "online_after"),
        "offline_after": ("dashboard", "offline_after"),
        "shelly_host": ("shelly", "host"),
        "shelly_password": ("shelly", "password"),
        "channel": ("shelly", "channel"),
        "resync_interval": ("behaviour", "resync_interval"),
        "on_exit": ("behaviour", "on_exit"),
    }
    for attribute, (section, option) in mapping.items():
        value = getattr(args, attribute, None)
        if value is not None:
            config.set(section, option, str(value))
    if getattr(args, "dry_run", False):
        config.set("behaviour", "dry_run", "true")
    return config


def make_probe(config):
    section = config["dashboard"]
    return Probe(section.get("url"),
                 timeout=section.getfloat("timeout", fallback=4.0),
                 username=section.get("username", ""),
                 password=section.get("password", ""),
                 accept_status=section.get("accept_status", "200,401"))


def make_shelly(config):
    section = config["shelly"]
    return Shelly(section.get("host", ""),
                  channel=section.getint("channel", fallback=0),
                  username=section.get("username", "admin"),
                  password=section.get("password", ""),
                  timeout=section.getfloat("timeout", fallback=5.0),
                  retries=section.getint("retries", fallback=3))


def make_watcher(config):
    dashboard = config["dashboard"]
    behaviour = config["behaviour"]
    return Watcher(make_probe(config), make_shelly(config),
                   interval=dashboard.getfloat("interval", fallback=10.0),
                   online_after=dashboard.getint("online_after", fallback=1),
                   offline_after=dashboard.getint("offline_after", fallback=3),
                   resync_interval=behaviour.getfloat("resync_interval",
                                                      fallback=300.0),
                   dry_run=as_bool(behaviour.get("dry_run", "false")),
                   on_exit=behaviour.get("on_exit", "keep").strip().lower())


def setup_logging(verbose=False, log_file=None):
    handler = logging.FileHandler(log_file) if log_file else logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                           "%Y-%m-%d %H:%M:%S"))
    LOG.handlers[:] = [handler]
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="lcd4linux_shelly.py",
        description="Switch a Shelly Plug (Gen2/Gen3) with the LCD4Linux "
                    "web dashboard.")
    parser.add_argument("command", nargs="?", default="watch",
                        choices=["watch", "once", "status", "on", "off", "test"],
                        help="watch: keep polling (default); once: a single "
                             "poll and switch; status: print what both sides "
                             "say; on/off: switch by hand; test: check the "
                             "connection to both sides")
    parser.add_argument("-c", "--config", metavar="FILE",
                        help="INI file, otherwise ./lcd4linux-shelly.ini, "
                             "~/.config/lcd4linux-shelly.ini or "
                             "/etc/lcd4linux-shelly.ini")
    parser.add_argument("-d", "--dashboard-url", metavar="URL",
                        help="dashboard URL, e.g. http://192.168.178.104:8050/api/state")
    parser.add_argument("--dashboard-password", metavar="PASSWORD",
                        help="password of the LCD4Linux web editor, if one is set")
    parser.add_argument("-s", "--shelly-host", metavar="HOST",
                        help="address of the Shelly Plug, e.g. 192.168.178.60")
    parser.add_argument("--shelly-password", metavar="PASSWORD",
                        help="Shelly password, if authentication is enabled")
    parser.add_argument("--channel", type=int, metavar="N",
                        help="switch channel of the Shelly (default 0)")
    parser.add_argument("-i", "--interval", type=float, metavar="SECONDS",
                        help="seconds between two polls (default 10)")
    parser.add_argument("--timeout", type=float, metavar="SECONDS",
                        help="how long a poll may take (default 4)")
    parser.add_argument("--online-after", type=int, metavar="N",
                        help="good polls needed before switching on (default 1)")
    parser.add_argument("--offline-after", type=int, metavar="N",
                        help="failed polls needed before switching off (default 3)")
    parser.add_argument("--resync-interval", type=float, metavar="SECONDS",
                        help="how often to correct a plug somebody else moved, "
                             "0 disables it (default 300)")
    parser.add_argument("--on-exit", choices=["keep", "off", "on"],
                        help="what to do with the plug when the watcher stops "
                             "(default keep)")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="log the switching but leave the plug alone")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every poll")
    parser.add_argument("--log-file", metavar="FILE", help="log into a file")
    parser.add_argument("-V", "--version", action="version",
                        version="lcd4linux-shelly %s" % __version__)
    return parser.parse_args(argv)


def command_status(config):
    online, reason = make_probe(config).check()
    print("Dashboard %-8s %s (%s)" % ("online" if online else "OFFLINE",
                                      config["dashboard"]["url"], reason))
    try:
        shelly = make_shelly(config)
        info = shelly.info()
        print("Shelly    %-8s %s (%s, firmware %s)"
              % ("on" if shelly.output() else "off", shelly.host,
                 info.get("model") or info.get("id", "?"),
                 info.get("ver", "?")))
    except ShellyError as err:
        print("Shelly    ERROR    %s" % err)
        return 1
    return 0 if online else 2


def command_test(config):
    print("LCD4Linux dashboard: %s" % config["dashboard"]["url"])
    online, reason = make_probe(config).check()
    print("  -> %s (%s)" % ("reachable" if online else "NOT reachable", reason))
    try:
        shelly = make_shelly(config)
    except ShellyError as err:
        print("Shelly: %s" % err)
        return 1
    print("Shelly: %s" % shelly.host)
    try:
        info = shelly.info()
        print("  -> %s, id %s, MAC %s, firmware %s, gen %s"
              % (info.get("model", "?"), info.get("id", "?"),
                 info.get("mac", "?"), info.get("ver", "?"),
                 info.get("gen", "?")))
        print("  -> channel %d is currently %s"
              % (shelly.channel, "on" if shelly.output() else "off"))
    except ShellyError as err:
        print("  -> ERROR: %s" % err)
        return 1
    return 0 if online else 2


def main(argv=None):
    args = parse_args(argv)
    setup_logging(args.verbose, args.log_file)
    config = apply_overrides(load_config(args.config), args)

    if args.command == "status":
        return command_status(config)
    if args.command == "test":
        return command_test(config)

    if args.command in ("on", "off"):
        want = args.command == "on"
        try:
            shelly = make_shelly(config)
            if as_bool(config["behaviour"].get("dry_run", "false")):
                LOG.info("[dry-run] would switch the plug %s", args.command)
            else:
                shelly.set_output(want)
                LOG.info("plug switched %s", args.command)
        except ShellyError as err:
            LOG.error("%s", err)
            return 1
        return 0

    try:
        watcher = make_watcher(config)
    except ShellyError as err:
        LOG.error("%s", err)
        return 1

    if args.command == "once":
        watcher.step()
        return 0

    signal.signal(signal.SIGINT, watcher.stop)
    signal.signal(signal.SIGTERM, watcher.stop)
    return watcher.run()


if __name__ == "__main__":
    sys.exit(main())
