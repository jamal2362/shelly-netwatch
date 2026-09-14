#!/usr/bin/env python3
"""Tests for the watcher.

Everything runs against small HTTP servers on localhost, so the suite needs
neither a Shelly nor a running LCD4Linux:

    python3 -m unittest discover -s tests -v
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lcd4linux_shelly as app  # noqa: E402

DEFAULT_URL = app.DEFAULTS["dashboard"]["url"]


def configparser_defaults():
    """A configuration with nothing but the built-in defaults in it."""
    import configparser
    parser = configparser.ConfigParser()
    parser.read_dict(app.DEFAULTS)
    return parser

# The suite provokes errors on purpose; their log lines are not the point.
app.LOG.addHandler(__import__("logging").NullHandler())
app.LOG.propagate = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class Server(object):
    """A throwaway HTTP server on a free port."""

    def __init__(self, handler):
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05})
        self.thread.daemon = True
        self.thread.start()

    @property
    def port(self):
        return self.httpd.server_address[1]

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def quiet(handler_class):
    handler_class.log_message = lambda *args, **kwargs: None
    return handler_class


@quiet
class DashboardHandler(BaseHTTPRequestHandler):
    status = 200
    body = b'{"active": "default.json", "live": true}'

    def do_GET(self):
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        if self.status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="LCD4Linux"')
        self.end_headers()
        self.wfile.write(self.body)


class FakeShellyHandler(BaseHTTPRequestHandler):
    """Enough of the Gen2/Gen3 RPC API to switch a relay."""

    password = ""
    realm = "shellyplugmg3-a4cf12f454b9"
    nonce = "6c9c5b4b"
    output_state = False
    calls = []

    def log_message(self, *args, **kwargs):
        pass

    def _json(self, code, payload, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self):
        cls = type(self)
        if not cls.password:
            return True
        fields = app.parse_challenge(self.headers.get("Authorization", ""))
        if not fields:
            return False

        def H(text):
            return hashlib.sha256(text.encode("utf-8")).hexdigest()

        ha1 = H("admin:%s:%s" % (cls.realm, cls.password))
        ha2 = H("dummy_method:dummy_uri")
        expected = H(":".join([ha1, cls.nonce, fields.get("nc", ""),
                               fields.get("cnonce", ""), "auth", ha2]))
        return fields.get("response") == expected

    def do_POST(self):
        cls = type(self)
        if not self._authorised():
            challenge = ('Digest qop="auth", realm="%s", nonce="%s", '
                         'algorithm=SHA-256' % (cls.realm, cls.nonce))
            self._json(401, {"error": "unauthorized"},
                       {"WWW-Authenticate": challenge})
            return
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        method = request.get("method")
        params = request.get("params") or {}
        cls.calls.append((method, params))

        if method == "Switch.Set":
            was_on = cls.output_state
            cls.output_state = bool(params.get("on"))
            self._json(200, {"id": request.get("id"), "result": {"was_on": was_on}})
        elif method == "Switch.GetStatus":
            self._json(200, {"id": request.get("id"),
                             "result": {"id": 0, "output": cls.output_state,
                                        "apower": 12.3}})
        elif method == "Shelly.GetDeviceInfo":
            self._json(200, {"id": request.get("id"),
                             "result": {"id": cls.realm, "model": "S3PL-00112EU",
                                        "mac": "A4CF12F454B9", "gen": 3,
                                        "ver": "1.4.4"}})
        else:
            self._json(200, {"id": request.get("id"),
                             "error": {"code": -32601, "message": "unknown method"}})


class FakePlug(object):
    """A Shelly stand-in for the state-machine tests."""

    def __init__(self, state=False, fail=False):
        self.state = state
        self.fail = fail
        self.switches = []
        self.host = "fake"
        self.channel = 0

    def set_output(self, on):
        if self.fail:
            raise app.ShellyError("plug unreachable")
        was = self.state
        self.state = bool(on)
        self.switches.append(bool(on))
        return was

    def output(self):
        if self.fail:
            raise app.ShellyError("plug unreachable")
        return self.state


class FakeProbe(object):
    def __init__(self, answers):
        self.answers = list(answers)
        self.url = "http://fake:8050/api/state"

    def check(self):
        value = self.answers.pop(0) if self.answers else False
        return value, "stub"


# ---------------------------------------------------------------------------
# dashboard probe
# ---------------------------------------------------------------------------

class ProbeTest(unittest.TestCase):
    def setUp(self):
        DashboardHandler.status = 200
        self.server = Server(DashboardHandler)
        self.addCleanup(self.server.close)

    def test_online(self):
        probe = app.Probe(self.server.url + "/api/state", timeout=2)
        online, reason = probe.check()
        self.assertTrue(online)
        self.assertEqual("HTTP 200", reason)

    def test_password_protected_dashboard_counts_as_online(self):
        DashboardHandler.status = 401
        probe = app.Probe(self.server.url + "/api/state", timeout=2)
        self.assertTrue(probe.check()[0])

    def test_unexpected_status_is_offline(self):
        DashboardHandler.status = 503
        probe = app.Probe(self.server.url + "/api/state", timeout=2)
        online, reason = probe.check()
        self.assertFalse(online)
        self.assertEqual("HTTP 503", reason)

    def test_closed_port_is_offline(self):
        port = self.server.port
        self.server.close()
        probe = app.Probe("http://127.0.0.1:%d/api/state" % port, timeout=2)
        self.addCleanup(lambda: None)
        self.assertFalse(probe.check()[0])

    def test_accept_any(self):
        DashboardHandler.status = 500
        probe = app.Probe(self.server.url + "/", timeout=2, accept_status="any")
        self.assertTrue(probe.check()[0])

    def test_accept_status_parsing(self):
        self.assertEqual({200, 401}, app.Probe._parse_accept("200,401"))
        self.assertIsNone(app.Probe._parse_accept("any"))
        self.assertEqual({200}, app.Probe._parse_accept("nonsense"))


# ---------------------------------------------------------------------------
# Shelly RPC
# ---------------------------------------------------------------------------

class ShellyTest(unittest.TestCase):
    def setUp(self):
        FakeShellyHandler.password = ""
        FakeShellyHandler.output_state = False
        FakeShellyHandler.calls = []
        self.server = Server(FakeShellyHandler)
        self.addCleanup(self.server.close)
        self.shelly = app.Shelly(self.server.url, timeout=2, retries=1)

    def test_switch_on_and_off(self):
        self.shelly.set_output(True)
        self.assertTrue(self.shelly.output())
        self.shelly.set_output(False)
        self.assertFalse(self.shelly.output())

    def test_device_info(self):
        info = self.shelly.info()
        self.assertEqual(3, info["gen"])
        self.assertEqual("S3PL-00112EU", info["model"])

    def test_digest_authentication(self):
        FakeShellyHandler.password = "geheim"
        shelly = app.Shelly(self.server.url, password="geheim",
                            timeout=2, retries=1)
        shelly.set_output(True)
        self.assertTrue(shelly.output())

    def test_wrong_password_is_reported(self):
        FakeShellyHandler.password = "geheim"
        shelly = app.Shelly(self.server.url, password="falsch",
                            timeout=2, retries=2)
        with self.assertRaises(app.ShellyError):
            shelly.output()

    def test_missing_password_is_reported(self):
        FakeShellyHandler.password = "geheim"
        with self.assertRaises(app.ShellyError) as caught:
            self.shelly.output()
        self.assertIn("password", str(caught.exception))

    def test_rpc_error_is_raised(self):
        with self.assertRaises(app.ShellyError):
            self.shelly.call("Switch.Nonsense")

    def test_host_without_scheme(self):
        shelly = app.Shelly("192.168.178.60")
        self.assertEqual("http://192.168.178.60/rpc", shelly.url)

    def test_empty_host_is_rejected(self):
        with self.assertRaises(app.ShellyError):
            app.Shelly("")


class DigestTest(unittest.TestCase):
    def test_parse_challenge(self):
        fields = app.parse_challenge(
            'Digest qop="auth", realm="shellyplugmg3-aabb", '
            'nonce="1234", algorithm=SHA-256')
        self.assertEqual("shellyplugmg3-aabb", fields["realm"])
        self.assertEqual("1234", fields["nonce"])
        self.assertEqual("SHA-256", fields["algorithm"])

    def test_parse_challenge_ignores_basic(self):
        self.assertIsNone(app.parse_challenge('Basic realm="LCD4Linux"'))

    def test_header_uses_the_shelly_ha2(self):
        challenge = {"realm": "r", "nonce": "n", "algorithm": "SHA-256"}
        header = app.digest_header(challenge, "admin", "pw", "POST", "/rpc")
        fields = app.parse_challenge(header)
        ha1 = hashlib.sha256(b"admin:r:pw").hexdigest()
        ha2 = hashlib.sha256(b"dummy_method:dummy_uri").hexdigest()
        expected = hashlib.sha256(
            ":".join([ha1, "n", fields["nc"], fields["cnonce"],
                      "auth", ha2]).encode()).hexdigest()
        self.assertEqual(expected, fields["response"])

    def test_unknown_algorithm(self):
        with self.assertRaises(app.ShellyError):
            app.digest_header({"algorithm": "RC4"}, "admin", "pw", "POST", "/rpc")


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------

class WatcherTest(unittest.TestCase):
    def watcher(self, answers, plug=None, **kwargs):
        plug = plug or FakePlug()
        options = {"offline_after": 3, "online_after": 1, "resync_interval": 0}
        options.update(kwargs)
        return app.Watcher(FakeProbe(answers), plug, **options), plug

    def test_switches_on_when_the_dashboard_appears(self):
        watcher, plug = self.watcher([True])
        watcher.step()
        self.assertEqual([True], plug.switches)
        self.assertTrue(watcher.state)

    def test_stays_on_while_the_dashboard_is_there(self):
        watcher, plug = self.watcher([True, True, True])
        for _ in range(3):
            watcher.step()
        self.assertEqual([True], plug.switches)

    def test_one_missed_poll_does_not_cut_the_power(self):
        watcher, plug = self.watcher([True, False, True, True])
        for _ in range(4):
            watcher.step()
        self.assertEqual([True], plug.switches)
        self.assertTrue(watcher.state)

    def test_switches_off_after_the_configured_failures(self):
        watcher, plug = self.watcher([True, False, False, False])
        for _ in range(4):
            watcher.step()
        self.assertEqual([True, False], plug.switches)
        self.assertFalse(watcher.state)

    def test_comes_back_on(self):
        watcher, plug = self.watcher([True, False, False, False, True])
        for _ in range(5):
            watcher.step()
        self.assertEqual([True, False, True], plug.switches)

    def test_offline_at_start_switches_off(self):
        watcher, plug = self.watcher([False, False, False], plug=FakePlug(True))
        for _ in range(3):
            watcher.step()
        self.assertEqual([False], plug.switches)

    def test_online_after_debounces_flapping(self):
        watcher, plug = self.watcher([True, False, True, True], online_after=2)
        for _ in range(4):
            watcher.step()
        self.assertEqual([True], plug.switches)

    def test_failed_switch_is_retried_next_poll(self):
        plug = FakePlug(fail=True)
        watcher, _ = self.watcher([True, True], plug=plug)
        watcher.step()
        self.assertIsNone(watcher.state)
        plug.fail = False
        watcher.step()
        self.assertEqual([True], plug.switches)
        self.assertTrue(watcher.state)

    def test_dry_run_never_touches_the_plug(self):
        watcher, plug = self.watcher([True], dry_run=True)
        watcher.step()
        self.assertEqual([], plug.switches)
        self.assertTrue(watcher.state)

    def test_resync_corrects_a_plug_somebody_moved(self):
        watcher, plug = self.watcher([True, True], resync_interval=0.0001)
        watcher.step()
        plug.state = False          # somebody used the button on the plug
        watcher._last_resync = 0
        watcher.step()
        self.assertEqual([True, True], plug.switches)

    def test_on_exit_off(self):
        watcher, plug = self.watcher([True], on_exit="off")
        watcher.step()
        watcher._finish()
        self.assertEqual([True, False], plug.switches)

    def test_on_exit_keep(self):
        watcher, plug = self.watcher([True], on_exit="keep")
        watcher.step()
        watcher._finish()
        self.assertEqual([True], plug.switches)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

class ConfigTest(unittest.TestCase):
    def empty_config(self):
        """An empty INI file, so only the built-in defaults are in play."""
        handle = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_defaults_are_complete(self):
        config = app.load_config(self.empty_config())
        self.assertEqual("0", config["shelly"]["channel"])
        self.assertEqual("3", config["dashboard"]["offline_after"])

    def test_command_line_wins(self):
        args = app.parse_args(["-d", "http://box:8050/", "-s", "10.0.0.5",
                               "--offline-after", "5", "--dry-run"])
        config = app.apply_overrides(app.load_config(self.empty_config()), args)
        self.assertEqual("http://box:8050/", config["dashboard"]["url"])
        self.assertEqual("10.0.0.5", config["shelly"]["host"])
        self.assertEqual("5", config["dashboard"]["offline_after"])
        self.assertTrue(app.as_bool(config["behaviour"]["dry_run"]))

    def test_example_file_parses(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example = os.path.join(here, "lcd4linux-shelly.example.ini")
        config = app.load_config(example)
        watcher = app.make_watcher(config)
        self.assertEqual(3, watcher.offline_after)
        self.assertEqual("http://192.168.178.60/rpc", watcher.shelly.url)

    def test_missing_config_file(self):
        with self.assertRaises(SystemExit):
            app.load_config("/definitely/not/here.ini")

    def test_as_bool(self):
        self.assertTrue(app.as_bool("Ja"))
        self.assertTrue(app.as_bool("TRUE"))
        self.assertFalse(app.as_bool("nein"))
        self.assertFalse(app.as_bool(""))


# ---------------------------------------------------------------------------
# environment (this is how the container is configured)
# ---------------------------------------------------------------------------

class EnvironmentTest(unittest.TestCase):
    def empty_config(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def config_from(self, environ, ini=None):
        config = app.load_config(ini or self.empty_config())
        return app.apply_environment(config, environ)

    def test_every_section_can_be_set(self):
        config = self.config_from({
            "DASHBOARD_URL": "http://box:8050/api/state",
            "DASHBOARD_OFFLINE_AFTER": "7",
            "SHELLY_HOST": "10.0.0.5",
            "SHELLY_CHANNEL": "1",
            "RESYNC_INTERVAL": "0",
            "ON_EXIT": "off",
        })
        watcher = app.make_watcher(config)
        self.assertEqual("http://box:8050/api/state", watcher.probe.url)
        self.assertEqual(7, watcher.offline_after)
        self.assertEqual("http://10.0.0.5/rpc", watcher.shelly.url)
        self.assertEqual(1, watcher.shelly.channel)
        self.assertEqual(0, watcher.resync_interval)
        self.assertEqual("off", watcher.on_exit)

    def test_every_mapped_name_reaches_a_real_option(self):
        config = app.load_config(self.empty_config())
        for name, (section, option) in app.ENV_MAP.items():
            self.assertTrue(config.has_option(section, option),
                            "%s points at [%s] %s, which has no default"
                            % (name, section, option))

    def test_prefixed_name_wins(self):
        config = self.config_from({"SHELLY_HOST": "bare",
                                   "L4LS_SHELLY_HOST": "prefixed"})
        self.assertEqual("prefixed", config["shelly"]["host"])

    def test_value_from_a_file(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        handle.write("geheim\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        config = self.config_from({"SHELLY_PASSWORD_FILE": handle.name})
        self.assertEqual("geheim", config["shelly"]["password"])

    def test_file_wins_over_the_plain_value(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        handle.write("aus-der-datei")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        config = self.config_from({"SHELLY_PASSWORD": "im-klartext",
                                   "SHELLY_PASSWORD_FILE": handle.name})
        self.assertEqual("aus-der-datei", config["shelly"]["password"])

    def test_unreadable_file_stops_the_start(self):
        with self.assertRaises(SystemExit):
            self.config_from({"SHELLY_PASSWORD_FILE": "/definitely/not/here"})

    def test_environment_beats_the_ini_file(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
        handle.write("[shelly]\nhost = aus-der-ini\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        config = self.config_from({"SHELLY_HOST": "aus-der-umgebung"},
                                  ini=handle.name)
        self.assertEqual("aus-der-umgebung", config["shelly"]["host"])

    def test_command_line_beats_the_environment(self):
        config = self.config_from({"SHELLY_HOST": "aus-der-umgebung"})
        args = app.parse_args(["-s", "von-der-kommandozeile"])
        app.apply_overrides(config, args)
        self.assertEqual("von-der-kommandozeile", config["shelly"]["host"])

    def test_empty_environment_changes_nothing(self):
        config = self.config_from({})
        self.assertEqual(DEFAULT_URL, config["dashboard"]["url"])


# ---------------------------------------------------------------------------
# heartbeat and health check
# ---------------------------------------------------------------------------

class HealthTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".beat", delete=False)
        handle.close()
        self.beat = handle.name
        os.unlink(self.beat)
        self.addCleanup(lambda: os.path.exists(self.beat) and os.unlink(self.beat))
        self.config = app.apply_environment(configparser_defaults(),
                                            {"HEARTBEAT_FILE": self.beat})

    def test_watcher_writes_the_heartbeat(self):
        watcher = app.Watcher(FakeProbe([True]), FakePlug(),
                              heartbeat_file=self.beat, resync_interval=0)
        watcher.heartbeat()
        self.assertTrue(os.path.exists(self.beat))

    def health(self, config=None):
        """command_health without its printing landing in the test output."""
        with contextlib.redirect_stdout(io.StringIO()):
            return app.command_health(self.config if config is None else config)

    def test_missing_heartbeat_is_unhealthy(self):
        self.assertEqual(1, self.health())

    def test_fresh_heartbeat_is_healthy(self):
        open(self.beat, "w").write("now")
        self.assertEqual(0, self.health())

    def test_stale_heartbeat_is_unhealthy(self):
        open(self.beat, "w").write("old")
        old = time.time() - 3600
        os.utime(self.beat, (old, old))
        self.assertEqual(1, self.health())

    def test_without_a_heartbeat_file_health_says_nothing(self):
        config = configparser_defaults()
        self.assertEqual(0, self.health(config))

    def test_an_unwritable_path_does_not_kill_the_watcher(self):
        watcher = app.Watcher(FakeProbe([True]), FakePlug(),
                              heartbeat_file="/definitely/not/here/beat",
                              resync_interval=0)
        watcher.heartbeat()   # logs a debug line and carries on


if __name__ == "__main__":
    unittest.main()
