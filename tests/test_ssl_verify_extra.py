"""Additional coverage for app.common.ssl_verify.

test_ssl_verify.py covers the AIA walk, retry policy and error sniffing; this file
fills the remaining paths: the AppData cert cache, env-var / bundle loading, the
Windows registry blob parser, the raw TLS probe (direct + proxy CONNECT), and
make_ssl_verify's tier selection.
"""

from __future__ import annotations

import hashlib
import os
import socket
import ssl
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.common.ssl_verify as sv

# captured before the autouse fixture patches the name, so the real
# implementation stays reachable for its own tests
_REAL_SSL_CACHE_DIR = sv._ssl_cache_dir


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point the cert cache at tmp_path and reset module-level trust state."""
    monkeypatch.setattr(sv, "_ssl_cache_dir", lambda: tmp_path / "ssl_certs")
    saved_pending = list(sv._PENDING_CA_DERS)
    saved_relax = sv._RELAX_HOSTNAME[0]
    sv._PENDING_CA_DERS.clear()
    sv._RELAX_HOSTNAME[0] = False
    sv.make_ssl_verify.cache_clear()
    yield
    sv._PENDING_CA_DERS[:] = saved_pending
    sv._RELAX_HOSTNAME[0] = saved_relax
    sv.make_ssl_verify.cache_clear()


def _settings(**kw):
    base = dict(http_ssl_verify=True, http_ca_bundle="", http_check_hostname=True)
    base.update(kw)
    return SimpleNamespace(**base)


# ── cache directory ───────────────────────────────────────────────────────────

class TestSslCacheDir:
    def test_windows_uses_appdata(self, monkeypatch):
        monkeypatch.setattr(sv.sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", os.path.join("C:", "Users", "x", "AppData"))
        d = _REAL_SSL_CACHE_DIR()
        assert "IPMaster-Cowork" in str(d) and d.name == "ssl_certs"

    def test_windows_without_appdata_falls_back_home(self, monkeypatch):
        monkeypatch.setattr(sv.sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        d = _REAL_SSL_CACHE_DIR()
        assert d.name == "ssl_certs" and "IPMaster-Cowork" in str(d)

    def test_posix_uses_a_dotdir(self, monkeypatch):
        monkeypatch.setattr(sv.sys, "platform", "linux")
        assert ".ipmaster-cowork" in str(_REAL_SSL_CACHE_DIR())


class TestCertCache:
    def test_save_names_by_fingerprint(self, cert_triple, tmp_path):
        path = sv._save_ca_to_cache(cert_triple.root_der)
        assert path.name == hashlib.sha256(cert_triple.root_der).hexdigest() + ".der"
        assert path.read_bytes() == cert_triple.root_der

    def test_save_is_idempotent(self, cert_triple):
        first = sv._save_ca_to_cache(cert_triple.root_der)
        mtime = first.stat().st_mtime_ns
        second = sv._save_ca_to_cache(cert_triple.root_der)
        assert second == first and second.stat().st_mtime_ns == mtime

    def test_collect_returns_every_cached_der(self, cert_triple):
        sv._save_ca_to_cache(cert_triple.root_der)
        sv._save_ca_to_cache(cert_triple.intermediate_der)
        assert len(sv._collect_cached_ca_ders()) == 2

    def test_collect_is_empty_without_a_cache_dir(self):
        assert sv._collect_cached_ca_ders() == []

    def test_collect_skips_unreadable_files(self, cert_triple, monkeypatch):
        sv._save_ca_to_cache(cert_triple.root_der)
        monkeypatch.setattr(Path, "read_bytes",
                            lambda self: (_ for _ in ()).throw(OSError("locked")))
        assert sv._collect_cached_ca_ders() == []

    def test_load_cached_counts_successes(self, cert_triple):
        sv._save_ca_to_cache(cert_triple.root_der)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_cached_cas(ctx) == 1

    def test_load_cached_skips_garbage(self, tmp_path):
        cache = tmp_path / "ssl_certs"
        cache.mkdir(parents=True)
        (cache / "bad.der").write_bytes(b"not a cert")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_cached_cas(ctx) == 0


# ── env-var CA loading ────────────────────────────────────────────────────────

class TestLoadEnvVarCas:
    def _pem(self, tmp_path, cert_triple) -> Path:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_der_x509_certificate(cert_triple.root_der)
        p = tmp_path / "root.pem"
        p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return p

    def test_file_vars_are_loaded(self, tmp_path, cert_triple, monkeypatch):
        pem = self._pem(tmp_path, cert_triple)
        for var in sv._ENV_CA_FILE_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("SSL_CERT_FILE", str(pem))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_env_var_cas(ctx) == 1

    def test_all_three_file_vars_count(self, tmp_path, cert_triple, monkeypatch):
        pem = self._pem(tmp_path, cert_triple)
        for var in sv._ENV_CA_FILE_VARS:
            monkeypatch.setenv(var, str(pem))
        for var in sv._ENV_CA_DIR_VARS:
            monkeypatch.delenv(var, raising=False)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_env_var_cas(ctx) == 3

    def test_missing_paths_are_ignored(self, monkeypatch, tmp_path):
        for var in sv._ENV_CA_FILE_VARS + sv._ENV_CA_DIR_VARS:
            monkeypatch.setenv(var, str(tmp_path / "nope"))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_env_var_cas(ctx) == 0

    def test_unset_vars_are_ignored(self, monkeypatch):
        for var in sv._ENV_CA_FILE_VARS + sv._ENV_CA_DIR_VARS:
            monkeypatch.delenv(var, raising=False)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_env_var_cas(ctx) == 0

    def test_bad_file_is_counted_as_a_failure(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.pem"
        bad.write_text("not a cert", encoding="utf-8")
        for var in sv._ENV_CA_FILE_VARS:
            monkeypatch.delenv(var, raising=False)
        for var in sv._ENV_CA_DIR_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("SSL_CERT_FILE", str(bad))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_env_var_cas(ctx) == 0

    def test_dir_var_is_loaded(self, tmp_path, monkeypatch):
        for var in sv._ENV_CA_FILE_VARS:
            monkeypatch.delenv(var, raising=False)
        capath = tmp_path / "certs"
        capath.mkdir()
        monkeypatch.setenv("SSL_CERT_DIR", str(capath))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_env_var_cas(ctx) == 1


# ── _http_get ─────────────────────────────────────────────────────────────────

class TestHttpGet:
    def test_returns_the_body(self):
        resp = MagicMock(content=b"payload")
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = resp
        with patch("httpx.Client", return_value=client):
            assert sv._http_get("http://pki/root.crt") == b"payload"

    def test_failure_returns_none(self):
        with patch("httpx.Client", side_effect=RuntimeError("no route")):
            assert sv._http_get("http://pki/root.crt") is None

    def test_status_error_returns_none(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = RuntimeError("404")
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = resp
        with patch("httpx.Client", return_value=client):
            assert sv._http_get("http://pki/root.crt") is None


# ── _load_ca_bundle ───────────────────────────────────────────────────────────

class TestLoadCaBundle:
    def _pem_file(self, tmp_path, cert_triple) -> Path:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_der_x509_certificate(cert_triple.root_der)
        p = tmp_path / "bundle.pem"
        p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return p

    def test_file_entry_is_loaded(self, tmp_path, cert_triple):
        pem = self._pem_file(tmp_path, cert_triple)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_ca_bundle(ctx, str(pem)) == 1

    def test_bad_file_entry_is_skipped(self, tmp_path):
        bad = tmp_path / "bad.pem"
        bad.write_text("nope", encoding="utf-8")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        assert sv._load_ca_bundle(ctx, str(bad)) == 0

    def test_url_entry_is_downloaded_and_cached(self, cert_triple):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch.object(sv, "_http_get", return_value=cert_triple.root_der):
            assert sv._load_ca_bundle(ctx, "http://pki/root.crt") == 1
        assert len(sv._collect_cached_ca_ders()) == 1

    def test_unreachable_url_is_skipped(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch.object(sv, "_http_get", return_value=None):
            assert sv._load_ca_bundle(ctx, "http://pki/root.crt") == 0

    def test_url_returning_garbage_loads_nothing(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch.object(sv, "_http_get", return_value=b"junk"):
            assert sv._load_ca_bundle(ctx, "http://pki/root.crt") == 0

    def test_mixed_entries(self, tmp_path, cert_triple):
        pem = self._pem_file(tmp_path, cert_triple)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch.object(sv, "_http_get", return_value=cert_triple.intermediate_der):
            n = sv._load_ca_bundle(ctx, f"{pem};http://pki/inter.crt")
        assert n == 2


# ── Windows registry blob parsing ─────────────────────────────────────────────

def _blob(der: bytes, hdr: int = 12, prop_id: int = 32) -> bytes:
    if hdr == 12:
        return struct.pack("<III", prop_id, 1, len(der)) + der
    return struct.pack("<II", prop_id, len(der)) + der


class TestParseCertBlob:
    def test_twelve_byte_layout(self, cert_triple):
        assert sv._parse_cert_blob(_blob(cert_triple.root_der)) == cert_triple.root_der

    def test_eight_byte_layout(self, cert_triple):
        blob = _blob(cert_triple.root_der, hdr=8)
        assert sv._parse_cert_blob(blob) == cert_triple.root_der

    def test_other_property_ids_are_skipped(self, cert_triple):
        blob = _blob(cert_triple.root_der, prop_id=3) + _blob(cert_triple.root_der)
        assert sv._parse_cert_blob(blob) == cert_triple.root_der

    def test_empty_blob_is_none(self):
        assert sv._parse_cert_blob(b"") is None

    def test_short_blob_is_none(self):
        assert sv._parse_cert_blob(b"\x00\x00") is None

    def test_oversized_length_is_rejected(self):
        assert sv._parse_cert_blob(struct.pack("<III", 32, 1, 999_999)) is None

    def test_length_past_the_end_is_rejected(self):
        assert sv._parse_cert_blob(struct.pack("<III", 32, 1, 500) + b"\x30" * 10) is None

    def test_payload_not_starting_with_sequence_is_rejected(self):
        payload = b"\xff" * 64
        assert sv._parse_cert_blob(_blob(payload)) is None

    def test_tiny_payload_is_rejected(self):
        assert sv._parse_cert_blob(_blob(b"\x30" * 8)) is None


@pytest.mark.skipif(sys.platform != "win32", reason="registry paths are Windows-only")
class TestLoadRegistryCerts:
    def test_walks_the_configured_paths(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        loaded, counts = sv._load_registry_certs(ctx)
        assert isinstance(loaded, int) and isinstance(counts, dict)

    def test_missing_key_is_skipped(self):
        import winreg
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch.object(winreg, "OpenKey", side_effect=FileNotFoundError):
            assert sv._load_registry_certs(ctx) == (0, {})

    def test_cert_is_loaded_from_a_blob(self, cert_triple):
        import winreg
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        store_key, cert_key = MagicMock(), MagicMock()
        enum = {"n": 0}

        def enum_key(key, idx):
            enum["n"] += 1
            if enum["n"] > 1:
                raise OSError("no more")
            return "THUMB1"

        with patch.object(winreg, "OpenKey", side_effect=[store_key, cert_key] * 20), \
             patch.object(winreg, "EnumKey", side_effect=enum_key), \
             patch.object(winreg, "QueryValueEx",
                          return_value=(_blob(cert_triple.root_der), 3)):
            loaded, counts = sv._load_registry_certs(ctx)
        assert loaded >= 1 and counts

    def test_query_failure_is_swallowed(self):
        import winreg
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        enum = {"n": 0}

        def enum_key(key, idx):
            enum["n"] += 1
            if enum["n"] > 1:
                raise OSError("no more")
            return "THUMB1"

        with patch.object(winreg, "OpenKey", return_value=MagicMock()), \
             patch.object(winreg, "EnumKey", side_effect=enum_key), \
             patch.object(winreg, "QueryValueEx", side_effect=OSError("denied")):
            loaded, _ = sv._load_registry_certs(ctx)
        assert loaded == 0

    def test_non_bytes_blob_is_ignored(self):
        import winreg
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        enum = {"n": 0}

        def enum_key(key, idx):
            enum["n"] += 1
            if enum["n"] > 1:
                raise OSError("no more")
            return "THUMB1"

        with patch.object(winreg, "OpenKey", return_value=MagicMock()), \
             patch.object(winreg, "EnumKey", side_effect=enum_key), \
             patch.object(winreg, "QueryValueEx", return_value=("a string", 1)):
            loaded, _ = sv._load_registry_certs(ctx)
        assert loaded == 0


# ── proxy CONNECT tunnel ──────────────────────────────────────────────────────

class TestProxyConnectSocket:
    def test_no_proxy_returns_none(self):
        with patch("urllib.request.getproxies", return_value={}):
            assert sv._proxy_connect_socket("api.corp.test", 443) is None

    def test_successful_tunnel_returns_the_socket(self):
        sock = MagicMock()
        sock.recv.side_effect = [b"HTTP/1.1 200 Connection established\r\n\r\n"]
        with patch("urllib.request.getproxies", return_value={"https": "http://px:8080"}), \
             patch("socket.create_connection", return_value=sock):
            assert sv._proxy_connect_socket("api.corp.test", 443) is sock
        assert b"CONNECT api.corp.test:443" in sock.sendall.call_args.args[0]

    def test_scheme_is_added_when_missing(self):
        sock = MagicMock()
        sock.recv.side_effect = [b"HTTP/1.1 200 ok\r\n\r\n"]
        with patch("urllib.request.getproxies", return_value={"http": "px:3128"}), \
             patch("socket.create_connection", return_value=sock) as conn:
            sv._proxy_connect_socket("h", 443)
        assert conn.call_args.args[0] == ("px", 3128)

    def test_default_port_is_8080(self):
        sock = MagicMock()
        sock.recv.side_effect = [b"HTTP/1.1 200 ok\r\n\r\n"]
        with patch("urllib.request.getproxies", return_value={"https": "http://px"}), \
             patch("socket.create_connection", return_value=sock) as conn:
            sv._proxy_connect_socket("h", 443)
        assert conn.call_args.args[0] == ("px", 8080)

    def test_proxy_without_a_hostname_returns_none(self):
        with patch("urllib.request.getproxies", return_value={"https": "http:///path"}):
            assert sv._proxy_connect_socket("h", 443) is None

    def test_non_200_closes_and_returns_none(self):
        sock = MagicMock()
        sock.recv.side_effect = [b"HTTP/1.1 407 Proxy Auth Required\r\n\r\n"]
        with patch("urllib.request.getproxies", return_value={"https": "http://px:8080"}), \
             patch("socket.create_connection", return_value=sock):
            assert sv._proxy_connect_socket("h", 443) is None
        assert sock.close.called

    def test_connection_closed_mid_response(self):
        sock = MagicMock()
        sock.recv.side_effect = [b"HTTP/1.1 ", b""]
        with patch("urllib.request.getproxies", return_value={"https": "http://px:8080"}), \
             patch("socket.create_connection", return_value=sock):
            assert sv._proxy_connect_socket("h", 443) is None

    def test_socket_failure_returns_none(self):
        with patch("urllib.request.getproxies", return_value={"https": "http://px:8080"}), \
             patch("socket.create_connection", side_effect=OSError("refused")):
            assert sv._proxy_connect_socket("h", 443) is None


# ── _get_leaf_and_chain ───────────────────────────────────────────────────────

class TestGetLeafAndChain:
    def test_direct_connection_is_preferred(self, cert_triple):
        sock = MagicMock()
        with patch("socket.create_connection", return_value=sock), \
             patch.object(sv, "_tls_chain_via_pyopenssl",
                          return_value=[cert_triple.leaf_der]) as tls:
            out = sv._get_leaf_and_chain("https://api.corp.test")
        assert out == [cert_triple.leaf_der]
        assert tls.call_args.args[1] == "api.corp.test"
        assert sock.close.called

    def test_falls_back_to_the_proxy(self, cert_triple):
        proxied = MagicMock()
        with patch("socket.create_connection", side_effect=OSError("blocked")), \
             patch.object(sv, "_proxy_connect_socket", return_value=proxied), \
             patch.object(sv, "_tls_chain_via_pyopenssl",
                          return_value=[cert_triple.leaf_der]):
            assert sv._get_leaf_and_chain("https://api.corp.test") == [cert_triple.leaf_der]

    def test_no_connection_returns_empty(self):
        with patch("socket.create_connection", side_effect=OSError("blocked")), \
             patch.object(sv, "_proxy_connect_socket", return_value=None):
            assert sv._get_leaf_and_chain("https://api.corp.test") == []

    def test_handshake_failure_returns_empty(self):
        with patch("socket.create_connection", return_value=MagicMock()), \
             patch.object(sv, "_tls_chain_via_pyopenssl",
                          side_effect=RuntimeError("alert")):
            assert sv._get_leaf_and_chain("https://api.corp.test") == []

    def test_custom_port_is_used(self, cert_triple):
        with patch("socket.create_connection", return_value=MagicMock()) as conn, \
             patch.object(sv, "_tls_chain_via_pyopenssl", return_value=[]):
            sv._get_leaf_and_chain("https://api.corp.test:8443")
        assert conn.call_args.args[0] == ("api.corp.test", 8443)

    def test_socket_close_failure_is_swallowed(self, cert_triple):
        sock = MagicMock()
        sock.close.side_effect = OSError("already closed")
        with patch("socket.create_connection", return_value=sock), \
             patch.object(sv, "_tls_chain_via_pyopenssl",
                          return_value=[cert_triple.leaf_der]):
            assert sv._get_leaf_and_chain("https://api.corp.test") == [cert_triple.leaf_der]

    def test_url_without_a_host_returns_empty(self):
        assert sv._get_leaf_and_chain("https://") == []


class TestTlsChainViaPyopenssl:
    def _run(self, handshake_effects, chain=None):
        from OpenSSL import SSL
        conn = MagicMock()
        conn.do_handshake.side_effect = handshake_effects
        conn.get_peer_cert_chain.return_value = chain
        with patch.object(SSL, "Context", return_value=MagicMock()), \
             patch.object(SSL, "Connection", return_value=conn), \
             patch("OpenSSL.crypto.dump_certificate", side_effect=lambda t, c: b"DER"), \
             patch("select.select", return_value=([], [], [])):
            return sv._tls_chain_via_pyopenssl(MagicMock(), "api.corp.test"), conn

    def test_immediate_handshake(self):
        ders, conn = self._run([None], chain=[MagicMock()])
        assert ders == [b"DER"]
        assert conn.set_connect_state.called

    def test_no_peer_chain_is_empty(self):
        ders, _ = self._run([None], chain=None)
        assert ders == []

    def test_want_read_then_success(self):
        from OpenSSL import SSL
        ders, _ = self._run([SSL.WantReadError(), None], chain=[MagicMock()])
        assert ders == [b"DER"]

    def test_want_write_then_success(self):
        from OpenSSL import SSL
        ders, _ = self._run([SSL.WantWriteError(), None], chain=[MagicMock()])
        assert ders == [b"DER"]

    def test_sni_failure_is_swallowed(self):
        from OpenSSL import SSL
        conn = MagicMock()
        conn.set_tlsext_host_name.side_effect = RuntimeError("bad name")
        conn.do_handshake.side_effect = [None]
        conn.get_peer_cert_chain.return_value = []
        with patch.object(SSL, "Context", return_value=MagicMock()), \
             patch.object(SSL, "Connection", return_value=conn):
            assert sv._tls_chain_via_pyopenssl(MagicMock(), "h") == []

    def test_shutdown_failure_is_swallowed(self):
        from OpenSSL import SSL
        conn = MagicMock()
        conn.do_handshake.side_effect = [None]
        conn.get_peer_cert_chain.return_value = []
        conn.shutdown.side_effect = RuntimeError("already down")
        with patch.object(SSL, "Context", return_value=MagicMock()), \
             patch.object(SSL, "Connection", return_value=conn):
            assert sv._tls_chain_via_pyopenssl(MagicMock(), "h") == []

    def test_read_timeout_raises(self, monkeypatch):
        import time as time_mod
        from OpenSSL import SSL
        conn = MagicMock()
        conn.do_handshake.side_effect = SSL.WantReadError()
        clock = {"t": 0.0}

        def monotonic():
            clock["t"] += 20.0
            return clock["t"]

        with patch.object(SSL, "Context", return_value=MagicMock()), \
             patch.object(SSL, "Connection", return_value=conn), \
             patch.object(time_mod, "monotonic", monotonic):
            with pytest.raises(TimeoutError):
                sv._tls_chain_via_pyopenssl(MagicMock(), "h")

    def test_write_timeout_raises(self):
        import time as time_mod
        from OpenSSL import SSL
        conn = MagicMock()
        conn.do_handshake.side_effect = SSL.WantWriteError()
        clock = {"t": 0.0}

        def monotonic():
            clock["t"] += 20.0
            return clock["t"]

        with patch.object(SSL, "Context", return_value=MagicMock()), \
             patch.object(SSL, "Connection", return_value=conn), \
             patch.object(time_mod, "monotonic", monotonic):
            with pytest.raises(TimeoutError):
                sv._tls_chain_via_pyopenssl(MagicMock(), "h")


# ── make_ssl_verify tiers ─────────────────────────────────────────────────────

class TestMakeSslVerify:
    def test_disabled_returns_false(self):
        with patch("app.config.settings.get_settings",
                   return_value=_settings(http_ssl_verify=False)):
            assert sv.make_ssl_verify() is False

    def test_returns_a_context_by_default(self):
        with patch("app.config.settings.get_settings", return_value=_settings()):
            ctx = sv.make_ssl_verify()
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_bundle_entries_are_loaded(self, tmp_path, cert_triple):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_der_x509_certificate(cert_triple.root_der)
        pem = tmp_path / "b.pem"
        pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        with patch("app.config.settings.get_settings",
                   return_value=_settings(http_ca_bundle=str(pem))), \
             patch.object(sv, "_load_ca_bundle", return_value=1) as load:
            sv.make_ssl_verify()
        assert load.called

    def test_hostname_check_disabled_by_settings(self):
        with patch("app.config.settings.get_settings",
                   return_value=_settings(http_check_hostname=False)):
            ctx = sv.make_ssl_verify()
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_auto_relaxed_hostname_overrides_the_setting(self):
        sv._RELAX_HOSTNAME[0] = True
        with patch("app.config.settings.get_settings", return_value=_settings()):
            assert sv.make_ssl_verify().check_hostname is False

    def test_result_is_cached(self):
        with patch("app.config.settings.get_settings", return_value=_settings()):
            assert sv.make_ssl_verify() is sv.make_ssl_verify()

    def test_pending_cas_are_installed(self, cert_triple):
        sv._PENDING_CA_DERS.append(cert_triple.root_der)
        with patch("app.config.settings.get_settings", return_value=_settings()):
            ctx = sv.make_ssl_verify()
        assert isinstance(ctx, ssl.SSLContext)

    def test_bad_pending_ca_is_skipped(self):
        sv._PENDING_CA_DERS.append(b"garbage")
        with patch("app.config.settings.get_settings", return_value=_settings()):
            assert isinstance(sv.make_ssl_verify(), ssl.SSLContext)

    def test_default_certs_failure_is_tolerated(self):
        with patch("app.config.settings.get_settings", return_value=_settings()), \
             patch.object(ssl.SSLContext, "load_default_certs",
                          side_effect=RuntimeError("no store")):
            assert isinstance(sv.make_ssl_verify(), ssl.SSLContext)
