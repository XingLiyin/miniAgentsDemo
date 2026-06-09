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
