"""Tests for app.common.ssl_verify."""
from __future__ import annotations

import hashlib

import app.common.ssl_verify as sv


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
