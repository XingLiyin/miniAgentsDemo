"""Tests for app.common.ssl_verify."""
from __future__ import annotations

import hashlib

import pytest

import app.common.ssl_verify as sv


@pytest.fixture(autouse=True)
def _reset_relax_hostname():
    """Keep the process-global hostname-relax flag from leaking across tests."""
    sv._RELAX_HOSTNAME[0] = False
    yield
    sv._RELAX_HOSTNAME[0] = False


def test_save_and_load_cached_cas(tmp_path, monkeypatch, cert_triple):
    monkeypatch.setattr(sv, "_ssl_cache_dir", lambda: tmp_path)

    path = sv._save_ca_to_cache(cert_triple.root_der)
    assert path.exists()
    # filename is the sha256 fingerprint
    assert path.stem == hashlib.sha256(cert_triple.root_der).hexdigest()
    # file content is exactly the DER
    assert path.read_bytes() == cert_triple.root_der

    # saving the same cert again does not duplicate
    sv._save_ca_to_cache(cert_triple.root_der)
    assert len(list(tmp_path.glob("*.der"))) == 1

    # _load_cached_cas returns all cached DERs
    sv._save_ca_to_cache(cert_triple.intermediate_der)
    loaded = sv._collect_cached_ca_ders()
    assert cert_triple.root_der in loaded
    assert cert_triple.intermediate_der in loaded


from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs7


def test_decode_certs_der(cert_triple):
    out = sv._decode_certs(cert_triple.root_der)
    assert out == [cert_triple.root_der]


def test_decode_certs_pem(cert_triple):
    cert = x509.load_der_x509_certificate(cert_triple.root_der)
    pem = cert.public_bytes(serialization.Encoding.PEM)
    out = sv._decode_certs(pem)
    assert out == [cert_triple.root_der]


def test_decode_certs_pkcs7(cert_triple):
    certs = [
        x509.load_der_x509_certificate(cert_triple.intermediate_der),
        x509.load_der_x509_certificate(cert_triple.root_der),
    ]
    p7 = pkcs7.serialize_certificates(certs, serialization.Encoding.DER)
    out = sv._decode_certs(p7)
    assert cert_triple.intermediate_der in out
    assert cert_triple.root_der in out


def test_decode_certs_garbage():
    assert sv._decode_certs(b"not a cert") == []


def test_extract_ca_issuer_urls(cert_triple):
    urls = sv._extract_ca_issuer_urls(cert_triple.leaf_der)
    assert urls == [cert_triple.intermediate_aia_url]

    urls2 = sv._extract_ca_issuer_urls(cert_triple.intermediate_der)
    assert urls2 == [cert_triple.root_aia_url]

    # root has no AIA
    assert sv._extract_ca_issuer_urls(cert_triple.root_der) == []


def test_is_self_signed(cert_triple):
    assert sv._is_self_signed(cert_triple.root_der) is True
    assert sv._is_self_signed(cert_triple.intermediate_der) is False
    assert sv._is_self_signed(cert_triple.leaf_der) is False


def test_is_ca_cert(cert_triple):
    assert sv._is_ca_cert(cert_triple.root_der) is True
    assert sv._is_ca_cert(cert_triple.intermediate_der) is True
    assert sv._is_ca_cert(cert_triple.leaf_der) is False


import ssl


def test_load_env_var_cas(tmp_path, monkeypatch, cert_triple):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    pem = x509.load_der_x509_certificate(cert_triple.root_der).public_bytes(
        serialization.Encoding.PEM
    )
    ca_file = tmp_path / "corp.pem"
    ca_file.write_bytes(pem)

    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    n = sv._load_env_var_cas(ctx)
    assert n >= 1


def test_load_env_var_cas_none_set(monkeypatch):
    for var in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        monkeypatch.delenv(var, raising=False)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert sv._load_env_var_cas(ctx) == 0


def test_parse_ca_bundle_entries():
    raw = " C:\\certs\\a.crt ; http://pki.corp.test/root.crt ;; /etc/b.pem "
    files, urls = sv._parse_ca_bundle_entries(raw)
    assert files == ["C:\\certs\\a.crt", "/etc/b.pem"]
    assert urls == ["http://pki.corp.test/root.crt"]


def test_load_ca_bundle_into_ctx(tmp_path, monkeypatch, cert_triple):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    pem = x509.load_der_x509_certificate(cert_triple.root_der).public_bytes(
        serialization.Encoding.PEM
    )
    f = tmp_path / "a.pem"
    f.write_bytes(pem)

    # URL entry: mock the HTTP download to return intermediate DER
    monkeypatch.setattr(sv, "_http_get", lambda url, timeout=10: cert_triple.intermediate_der)
    monkeypatch.setattr(sv, "_ssl_cache_dir", lambda: tmp_path / "cache")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    raw = f"{f};http://pki.corp.test/intermediate.crt"
    loaded = sv._load_ca_bundle(ctx, raw)
    assert loaded >= 2


def test_get_leaf_and_chain_rejects_non_https():
    # http / non-https URLs are not probed
    assert sv._get_leaf_and_chain("http://example.test") == []
    assert sv._get_leaf_and_chain("ftp://example.test") == []
    assert sv._get_leaf_and_chain("not a url") == []


def test_get_leaf_and_chain_handles_unreachable(monkeypatch):
    # Direct connect fails AND no proxy → returns [] gracefully
    import socket as _socket

    def _boom(*a, **k):
        raise OSError("unreachable")

    monkeypatch.setattr(_socket, "create_connection", _boom)
    monkeypatch.setattr(sv, "_proxy_connect_socket", lambda h, p: None)
    assert sv._get_leaf_and_chain("https://nonexistent.invalid:443") == []


def test_fetch_corporate_ca_chain_walks_aia(tmp_path, monkeypatch, cert_triple):
    monkeypatch.setattr(sv, "_ssl_cache_dir", lambda: tmp_path)

    # Probe presents ONLY the leaf (proxy sent no intermediates) — forces AIA walk
    monkeypatch.setattr(sv, "_get_leaf_and_chain", lambda url: [cert_triple.leaf_der])

    # AIA downloads: leaf→intermediate, intermediate→root
    def fake_get(url, timeout=10):
        if url == cert_triple.intermediate_aia_url:
            return cert_triple.intermediate_der
        if url == cert_triple.root_aia_url:
            return cert_triple.root_der
        return None
    monkeypatch.setattr(sv, "_http_get", fake_get)

    cas = sv._fetch_corporate_ca_chain("https://api.corp.test")
    # Both CA certs collected (leaf is NOT a CA, so excluded)
    assert cert_triple.intermediate_der in cas
    assert cert_triple.root_der in cas
    assert cert_triple.leaf_der not in cas


def test_fetch_corporate_ca_chain_uses_presented_intermediate(tmp_path, monkeypatch, cert_triple):
    monkeypatch.setattr(sv, "_ssl_cache_dir", lambda: tmp_path)
    # Proxy presents leaf + intermediate; only root must be AIA-fetched
    monkeypatch.setattr(sv, "_get_leaf_and_chain",
                        lambda url: [cert_triple.leaf_der, cert_triple.intermediate_der])

    def fake_get(url, timeout=10):
        return cert_triple.root_der if url == cert_triple.root_aia_url else None
    monkeypatch.setattr(sv, "_http_get", fake_get)

    cas = sv._fetch_corporate_ca_chain("https://api.corp.test")
    assert cert_triple.intermediate_der in cas
    assert cert_triple.root_der in cas


def test_fetch_corporate_ca_chain_probe_fails(monkeypatch):
    monkeypatch.setattr(sv, "_get_leaf_and_chain", lambda url: [])
    assert sv._fetch_corporate_ca_chain("https://x.test") == []


def test_with_ssl_retry_success_first_try(monkeypatch):
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    calls = []
    def do(verify):
        calls.append(verify)
        return "ok"
    assert sv.with_ssl_retry(do, "https://api.corp.test") == "ok"
    assert calls == [True]


def test_with_ssl_retry_fetches_then_retries(tmp_path, monkeypatch, cert_triple):
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    monkeypatch.setattr(sv, "_ssl_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(sv, "_PENDING_CA_DERS", [])
    monkeypatch.setattr(sv.make_ssl_verify, "cache_clear", lambda: None, raising=False)

    fetched = {"n": 0}
    monkeypatch.setattr(sv, "_fetch_corporate_ca_chain",
                        lambda url: (fetched.__setitem__("n", 1) or [cert_triple.root_der]))

    attempts = {"n": 0}
    def do(verify):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ssl.SSLCertVerificationError("verify failed")
        return "ok-after-retry"

    result = sv.with_ssl_retry(do, "https://api.corp.test")
    assert result == "ok-after-retry"
    assert fetched["n"] == 1
    assert attempts["n"] == 2
    # cache-on-success: the proven CA was persisted to disk
    assert cert_triple.root_der in sv._collect_cached_ca_ders()
    # overlay rolled back to its prior (empty) state after persisting
    assert sv._PENDING_CA_DERS == []


def test_with_ssl_retry_reraises_non_cert_error(monkeypatch):
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    def do(verify):
        raise ValueError("unrelated")
    import pytest
    with pytest.raises(ValueError):
        sv.with_ssl_retry(do, "https://api.corp.test")


def test_is_cert_verify_error_isinstance():
    assert sv._is_cert_verify_error(ssl.SSLCertVerificationError("verify failed")) is True


def test_is_cert_verify_error_wrapped_string_no_chain():
    # The ping/stream path produces a plain RuntimeError with the SSL text in the
    # message and NO __cause__/__context__ chain — exercises the string fallback.
    exc = RuntimeError("HTTP 流式请求失败: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate")
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert sv._is_cert_verify_error(exc) is True


def test_is_cert_verify_error_via_cause_chain():
    inner = ssl.SSLCertVerificationError("verify failed")
    outer = RuntimeError("connect error")
    outer.__cause__ = inner
    assert sv._is_cert_verify_error(outer) is True


def test_is_cert_verify_error_unrelated():
    assert sv._is_cert_verify_error(ValueError("totally unrelated")) is False


def test_with_ssl_retry_failed_retry_does_not_persist(tmp_path, monkeypatch, cert_triple):
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    monkeypatch.setattr(sv, "_ssl_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(sv, "_PENDING_CA_DERS", [])
    monkeypatch.setattr(sv.make_ssl_verify, "cache_clear", lambda: None, raising=False)
    monkeypatch.setattr(sv, "_fetch_corporate_ca_chain", lambda url: [cert_triple.root_der])

    # Both the initial attempt AND the retry fail with a cert error.
    def do(verify):
        raise ssl.SSLCertVerificationError("verify failed")

    import pytest
    with pytest.raises(ssl.SSLCertVerificationError):
        sv.with_ssl_retry(do, "https://api.corp.test")

    # Nothing persisted; overlay rolled back.
    assert sv._collect_cached_ca_ders() == []
    assert sv._PENDING_CA_DERS == []


def test_is_hostname_mismatch_error():
    assert sv._is_hostname_mismatch_error(
        ssl.SSLCertVerificationError(
            "certificate verify failed: IP address mismatch, "
            "certificate is not valid for '10.1.2.3'")) is True
    assert sv._is_hostname_mismatch_error(
        ssl.SSLCertVerificationError("hostname 'x' doesn't match 'y'")) is True
    # A pure CA-trust error is NOT a hostname mismatch
    assert sv._is_hostname_mismatch_error(
        ssl.SSLCertVerificationError(
            "self-signed certificate in certificate chain")) is False


def test_with_ssl_retry_relaxes_hostname_after_ca_trusted(tmp_path, monkeypatch, cert_triple):
    # Sequence: attempt0 = CA error → fetch CA; attempt1 = IP mismatch → relax
    # hostname; attempt2 = success.
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    monkeypatch.setattr(sv, "_ssl_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(sv, "_PENDING_CA_DERS", [])
    monkeypatch.setattr(sv.make_ssl_verify, "cache_clear", lambda: None, raising=False)
    monkeypatch.setattr(sv, "_fetch_corporate_ca_chain", lambda url: [cert_triple.root_der])

    attempts = {"n": 0}
    def do(verify):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ssl.SSLCertVerificationError("self-signed certificate in certificate chain")
        if attempts["n"] == 2:
            raise ssl.SSLCertVerificationError(
                "IP address mismatch, certificate is not valid for '10.1.2.3'")
        return "ok"

    result = sv.with_ssl_retry(do, "https://10.1.2.3:8443")
    assert result == "ok"
    assert attempts["n"] == 3
    assert sv._RELAX_HOSTNAME[0] is True
    # CA persisted on success
    assert cert_triple.root_der in sv._collect_cached_ca_ders()


def test_with_ssl_retry_relaxes_hostname_only(monkeypatch, cert_triple):
    # CA already trusted: first error is straight IP mismatch → relax → success.
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    monkeypatch.setattr(sv, "_PENDING_CA_DERS", [])
    monkeypatch.setattr(sv.make_ssl_verify, "cache_clear", lambda: None, raising=False)
    monkeypatch.setattr(sv, "_fetch_corporate_ca_chain", lambda url: [])

    attempts = {"n": 0}
    def do(verify):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ssl.SSLCertVerificationError(
                "IP address mismatch, certificate is not valid for '10.1.2.3'")
        return "ok"

    result = sv.with_ssl_retry(do, "https://10.1.2.3:8443")
    assert result == "ok"
    assert sv._RELAX_HOSTNAME[0] is True


def test_build_base_context_check_hostname_default():
    ctx = sv._build_base_context()
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_build_base_context_check_hostname_disabled():
    # Disabling hostname check keeps CA chain verification on.
    ctx = sv._build_base_context(check_hostname=False)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED
