#!/usr/bin/env python3
"""Keep a Shelly plug in sync with any reachable network service.

This watcher polls one target - an HTTP(S) URL or a plain ``host:port`` -
and mirrors its reachability onto the relay of a Shelly plug (Gen2/Gen3
RPC API):

    target reachable  ->  plug switches the 230 V on
    target gone       ->  plug switches the 230 V off

Anything that answers on an IP and a port works: a web dashboard, a media
player, a printer, an access point, a game server.

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
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

__version__ = "2.0.0"

LOG = logging.getLogger("shelly-netwatch")

DEFAULTS = {
    "target": {
        "url": "",
        "timeout": "4",
        "interval": "10",
        "online_after": "1",
        "offline_after": "3",
        "username": "shelly-netwatch",
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
        "verbose": "false",
        "log_file": "",
        "heartbeat_file": "",
    },
}

# Every option can also be set through the environment, which is how the
# container is configured.  Names may carry the SNW_ prefix when a bare
# name would collide with something else; both spellings are read.
ENV_MAP = {
    "TARGET_URL": ("target", "url"),
    "TARGET_TIMEOUT": ("target", "timeout"),
    "TARGET_INTERVAL": ("target", "interval"),
    "TARGET_ONLINE_AFTER": ("target", "online_after"),
    "TARGET_OFFLINE_AFTER": ("target", "offline_after"),
    "TARGET_USERNAME": ("target", "username"),
    "TARGET_PASSWORD": ("target", "password"),
    "TARGET_ACCEPT_STATUS": ("target", "accept_status"),
    "SHELLY_HOST": ("shelly", "host"),
    "SHELLY_CHANNEL": ("shelly", "channel"),
    "SHELLY_USERNAME": ("shelly", "username"),
    "SHELLY_PASSWORD": ("shelly", "password"),
    "SHELLY_TIMEOUT": ("shelly", "timeout"),
    "SHELLY_RETRIES": ("shelly", "retries"),
    "RESYNC_INTERVAL": ("behaviour", "resync_interval"),
    "ON_EXIT": ("behaviour", "on_exit"),
    "DRY_RUN": ("behaviour", "dry_run"),
    "VERBOSE": ("behaviour", "verbose"),
    "LOG_FILE": ("behaviour", "log_file"),
    "HEARTBEAT_FILE": ("behaviour", "heartbeat_file"),
}

ENV_PREFIX = "SNW_"

COMMANDS = ("watch", "once", "status", "on", "off", "test", "health")

TRUE_WORDS = ("1", "true", "yes", "on")


def as_bool(value):
    """Read a configuration flag the way a human would write it."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE_WORDS


def env_names(name):
    """Every spelling of one setting, most specific first.

    ``SNW_TARGET_URL`` beats a bare ``TARGET_URL``; the prefix is there for
    the cases where a plain name would collide with something else.
    """
    return [ENV_PREFIX + name, name]


def env_value(name, environ=None):
    """The value of one setting from the environment, or ``None``.

    ``NAME_FILE`` is read first and points at a file holding the value,
    which is how Docker secrets hand a password to a container without
    putting it into ``docker inspect``.
    """
    environ = os.environ if environ is None else environ
    candidates = env_names(name)
    for key in (candidate + "_FILE" for candidate in candidates):
        path = environ.get(key)
        if path:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    return handle.read().rstrip("\r\n")
            except OSError as err:
                raise SystemExit("cannot read %s (%s): %s" % (key, path, err))
    for key in candidates:
        if key in environ:
            return environ[key]
    return None


def build_opener():
    """An opener that ignores http_proxy: the plug and the box are on the LAN."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# the watched target
# ---------------------------------------------------------------------------

def split_host_port(text, default_port=None):
    """``host:port`` into its two halves, IPv6 brackets included."""
    text = (text or "").strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = text.rpartition(":")
        if not host:  # no colon at all
            host, port = text, ""
    if not port:
        port = default_port
    if not host or not port:
        raise SystemExit("a target without a scheme needs a port, "
                         "e.g. 192.168.178.104:8050")
    try:
        return host, int(port)
    except ValueError:
        raise SystemExit("%r is not a port number" % (port,))


class TcpProbe(object):
    """Asks whether something accepts connections on an IP and a port.

    The plainest check there is: open a socket, close it again.  Good for
    everything that speaks no HTTP - SSH, SMB, a printer, a game server -
    and for web servers whose answer does not matter.
    """

    def __init__(self, url, timeout=4.0, **_ignored):
        rest = url.split("://", 1)[1] if "://" in url else url
        self.host, self.port = split_host_port(rest)
        self.timeout = float(timeout)
        self.url = "tcp://%s:%d" % (self.host, self.port)

    def check(self):
        """Return ``(online, reason)`` for a single poll."""
        try:
            connection = socket.create_connection((self.host, self.port),
                                                  timeout=self.timeout)
        except OSError as err:
            return False, err.strerror or str(err)
        except Exception as err:
            return False, str(err)
        connection.close()
        return True, "port %d open" % self.port


class HttpProbe(object):
    """Asks an HTTP(S) service whether it is still there."""

    def __init__(self, url, timeout=4.0, username="", password="",
                 accept_status="200,401"):
        self.url = url
        self.timeout = float(timeout)
        self.username = username or "shelly-netwatch"
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
        request.add_header("User-Agent", "shelly-netwatch/%s" % __version__)
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
            # means the service is up and asking for its password, which is
            # still "online" as far as the plug is concerned.
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


def normalise_target(url):
    """Fill in what a hand-written target address leaves out.

    ``192.168.178.104:8050`` is a TCP check, ``box:8050/api/state`` is
    HTTP, and anything with a scheme is taken as it stands.
    """
    url = (url or "").strip()
    if not url:
        raise SystemExit("no target configured (set target.url, TARGET_URL "
                         "or --target-url)")
    if "://" in url:
        return url
    if "/" in url:  # host:port/path - clearly meant as a web address
        return "http://" + url
    split_host_port(url)  # validates, raises with a helpful message
    return "tcp://" + url


def make_probe_for(url, timeout=4.0, username="", password="",
                   accept_status="200,401"):
    """The right probe for a target address."""
    url = normalise_target(url)
    scheme = url.split("://", 1)[0].lower()
    if scheme == "tcp":
        return TcpProbe(url, timeout=timeout)
    if scheme in ("http", "https"):
        return HttpProbe(url, timeout=timeout, username=username,
                         password=password, accept_status=accept_status)
    raise SystemExit("unsupported target scheme %r, expected http, https "
                     "or tcp" % scheme)


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
        request.add_header("User-Agent", "shelly-netwatch/%s" % __version__)
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
        payload = {"id": self._id, "src": "shelly-netwatch", "method": method}
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
    """Polls the target and switches the plug when the answer changes.

    A single missed poll does not cut the power: ``offline_after`` failures
    in a row are needed before the plug goes off, which rides out the short
    gaps a restarting service or a busy network produces.
    """

    def __init__(self, probe, shelly, interval=10.0, online_after=1,
                 offline_after=3, resync_interval=300.0, dry_run=False,
                 on_exit="keep", heartbeat_file=""):
        self.probe = probe
        self.shelly = shelly
        self.interval = float(interval)
        self.online_after = max(1, int(online_after))
        self.offline_after = max(1, int(offline_after))
        self.resync_interval = float(resync_interval)
        self.dry_run = bool(dry_run)
        self.on_exit = on_exit
        self.heartbeat_file = heartbeat_file or ""
        self.state = None        # what we believe the plug should be
        self.up = 0
        self.down = 0
        self._last_resync = 0.0
        self._stop = threading.Event()

    def heartbeat(self):
        """Touch the file the container health check looks at."""
        if not self.heartbeat_file:
            return
        try:
            with open(self.heartbeat_file, "w") as handle:
                handle.write("%d\n" % time.time())
        except OSError as err:
            LOG.debug("cannot write the heartbeat file: %s", err)

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
        LOG.debug("target %s (%s), up=%d down=%d",
                  "online" if online else "offline", reason, self.up, self.down)

        want = None
        if online and self.state is not True and self.up >= self.online_after:
            want = True
        elif not online and self.state is not False and self.down >= self.offline_after:
            want = False

        if want is not None:
            if self.state is None:
                why = "target is %s" % ("online" if want else "offline")
            else:
                why = "target went %s" % ("online" if want else "offline")
            LOG.info("%s: %s", why, reason)
            if self._switch(want, reason):
                self.state = want
                self._last_resync = time.time()
            return want

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
        self.heartbeat()
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as err:  # never let the loop die
                LOG.error("unexpected error: %s", err)
                LOG.debug("", exc_info=True)
            self.heartbeat()
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

CONFIG_NAME = "shelly-netwatch.ini"


def config_candidates():
    """Where an INI file is looked for, nearest first."""
    return (CONFIG_NAME,
            os.path.expanduser("~/.config/" + CONFIG_NAME),
            "/etc/" + CONFIG_NAME)


def load_config(path=None):
    """Defaults, overlaid with an INI file when there is one."""
    parser = configparser.ConfigParser()
    parser.read_dict(DEFAULTS)
    if path:
        if not os.path.isfile(path):
            raise SystemExit("configuration file not found: %s" % path)
        parser.read(path, encoding="utf-8")
    else:
        for candidate in config_candidates():
            if os.path.isfile(candidate):
                parser.read(candidate, encoding="utf-8")
                LOG.debug("configuration read from %s", candidate)
                break
    return parser


def apply_environment(config, environ=None):
    """The environment beats the file, so a container needs no file at all."""
    for name in sorted(ENV_MAP):
        section, option = ENV_MAP[name]
        value = env_value(name, environ)
        if value is not None:
            config.set(section, option, value)
    return config


def apply_overrides(config, args):
    """Command line beats configuration file, and both beat the defaults."""
    mapping = {
        "target_url": ("target", "url"),
        "target_password": ("target", "password"),
        "interval": ("target", "interval"),
        "timeout": ("target", "timeout"),
        "online_after": ("target", "online_after"),
        "offline_after": ("target", "offline_after"),
        "shelly_host": ("shelly", "host"),
        "shelly_password": ("shelly", "password"),
        "channel": ("shelly", "channel"),
        "resync_interval": ("behaviour", "resync_interval"),
        "on_exit": ("behaviour", "on_exit"),
        "heartbeat_file": ("behaviour", "heartbeat_file"),
        "log_file": ("behaviour", "log_file"),
    }
    for attribute, (section, option) in mapping.items():
        value = getattr(args, attribute, None)
        if value is not None:
            config.set(section, option, str(value))
    if getattr(args, "dry_run", False):
        config.set("behaviour", "dry_run", "true")
    if getattr(args, "verbose", False):
        config.set("behaviour", "verbose", "true")
    return config


def make_probe(config):
    section = config["target"]
    return make_probe_for(section.get("url"),
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
    target = config["target"]
    behaviour = config["behaviour"]
    return Watcher(make_probe(config), make_shelly(config),
                   interval=target.getfloat("interval", fallback=10.0),
                   online_after=target.getint("online_after", fallback=1),
                   offline_after=target.getint("offline_after", fallback=3),
                   resync_interval=behaviour.getfloat("resync_interval",
                                                      fallback=300.0),
                   dry_run=as_bool(behaviour.get("dry_run", "false")),
                   on_exit=behaviour.get("on_exit", "keep").strip().lower(),
                   heartbeat_file=behaviour.get("heartbeat_file", "").strip())


def setup_logging(verbose=False, log_file=None):
    handler = logging.FileHandler(log_file) if log_file else logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                           "%Y-%m-%d %H:%M:%S"))
    LOG.handlers[:] = [handler]
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="shelly_netwatch.py",
        description="Switch a Shelly plug (Gen2/Gen3) with the reachability "
                    "of any network service.")
    parser.add_argument("command", nargs="?", default=None,
                        choices=list(COMMANDS),
                        help="watch: keep polling (default); once: a single "
                             "poll and switch; status: print what both sides "
                             "say; on/off: switch by hand; test: check the "
                             "connection to both sides; health: container "
                             "health check.  Also settable as COMMAND=...")
    parser.add_argument("-c", "--config", metavar="FILE",
                        help="INI file, otherwise $CONFIG_FILE, "
                             "./shelly-netwatch.ini, "
                             "~/.config/shelly-netwatch.ini or "
                             "/etc/shelly-netwatch.ini.  Every option in it "
                             "can also be given as an environment variable, "
                             "which takes precedence")
    parser.add_argument("-t", "--target-url", metavar="URL",
                        help="what to watch: an http(s) URL such as "
                             "http://192.168.178.104:8050/api/state, or a bare "
                             "HOST:PORT such as 192.168.178.104:8050 for a "
                             "plain TCP check")
    parser.add_argument("--target-password", metavar="PASSWORD",
                        help="password of the watched service, if it wants one")
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
    parser.add_argument("--heartbeat-file", metavar="FILE",
                        help="file the watcher touches after every poll, "
                             "read back by the 'health' command")
    parser.add_argument("-V", "--version", action="version",
                        version="shelly-netwatch %s" % __version__)
    return parser.parse_args(argv)


def command_status(config):
    probe = make_probe(config)
    online, reason = probe.check()
    print("Target %-8s %s (%s)" % ("online" if online else "OFFLINE",
                                   probe.url, reason))
    try:
        shelly = make_shelly(config)
        info = shelly.info()
        print("Shelly %-8s %s (%s, firmware %s)"
              % ("on" if shelly.output() else "off", shelly.host,
                 info.get("model") or info.get("id", "?"),
                 info.get("ver", "?")))
    except ShellyError as err:
        print("Shelly ERROR    %s" % err)
        return 1
    return 0 if online else 2


def command_test(config):
    probe = make_probe(config)
    print("Target: %s" % probe.url)
    online, reason = probe.check()
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


def command_health(config):
    """Is the watcher still turning?  Used as the container health check."""
    path = config["behaviour"].get("heartbeat_file", "").strip()
    if not path:
        print("no heartbeat file configured")
        return 0
    if not os.path.exists(path):
        print("no heartbeat yet: %s" % path)
        return 1
    interval = config["target"].getfloat("interval", fallback=10.0)
    timeout = config["target"].getfloat("timeout", fallback=4.0)
    limit = max(3 * (interval + timeout), 30.0)
    age = time.time() - os.path.getmtime(path)
    if age > limit:
        print("the last poll was %.0fs ago (limit %.0fs)" % (age, limit))
        return 1
    print("ok, last poll %.0fs ago" % age)
    return 0


def main(argv=None):
    args = parse_args(argv)
    config = load_config(args.config or env_value("CONFIG_FILE"))
    apply_overrides(apply_environment(config), args)
    behaviour = config["behaviour"]
    setup_logging(as_bool(behaviour.get("verbose", "false")),
                  behaviour.get("log_file", "").strip() or None)

    command = args.command or env_value("COMMAND") or "watch"
    if command not in COMMANDS:
        raise SystemExit("unknown command %r, expected one of %s"
                         % (command, ", ".join(COMMANDS)))

    if command == "status":
        return command_status(config)
    if command == "test":
        return command_test(config)
    if command == "health":
        return command_health(config)

    if command in ("on", "off"):
        want = command == "on"
        try:
            shelly = make_shelly(config)
            if as_bool(behaviour.get("dry_run", "false")):
                LOG.info("[dry-run] would switch the plug %s", command)
            else:
                shelly.set_output(want)
                LOG.info("plug switched %s", command)
        except ShellyError as err:
            LOG.error("%s", err)
            return 1
        return 0

    try:
        watcher = make_watcher(config)
    except ShellyError as err:
        LOG.error("%s", err)
        return 1

    if command == "once":
        watcher.step()
        watcher.heartbeat()
        return 0

    signal.signal(signal.SIGINT, watcher.stop)
    signal.signal(signal.SIGTERM, watcher.stop)
    return watcher.run()


if __name__ == "__main__":
    sys.exit(main())
