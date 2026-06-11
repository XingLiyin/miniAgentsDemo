"""Shared pytest fixtures."""
from __future__ import annotations

import datetime
from dataclasses import dataclass

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtensionOID


@dataclass
class CertTriple:
    """A self-signed root, an intermediate it signs, and a leaf the intermediate signs.

    `*_der` are DER bytes. `aia_url` is the CA-Issuers URL embedded in leaf+intermediate.
    """
    root_der: bytes
    intermediate_der: bytes
    leaf_der: bytes
    intermediate_aia_url: str  # in leaf, points to intermediate
    root_aia_url: str          # in intermediate, points to root


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _aia(url: str) -> x509.AuthorityInformationAccess:
    return x509.AuthorityInformationAccess([
        x509.AccessDescription(
            x509.oid.AuthorityInformationAccessOID.CA_ISSUERS,
            x509.UniformResourceIdentifier(url),
        )
    ])


@pytest.fixture
def cert_triple() -> CertTriple:
    not_before = datetime.datetime(2020, 1, 1)
    not_after = datetime.datetime(2040, 1, 1)
    intermediate_aia_url = "http://pki.corp.test/intermediate.crt"
    root_aia_url = "http://pki.corp.test/root.crt"

    root_key = _key()
    root = (
        x509.CertificateBuilder()
        .subject_name(_name("Corp Root CA"))
        .issuer_name(_name("Corp Root CA"))
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )

    inter_key = _key()
    inter = (
        x509.CertificateBuilder()
        .subject_name(_name("Corp Intermediate CA"))
        .issuer_name(root.subject)
        .public_key(inter_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_aia(root_aia_url), critical=False)
        .sign(root_key, hashes.SHA256())
    )

    leaf_key = _key()
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name("api.corp.test"))
        .issuer_name(inter.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_aia(intermediate_aia_url), critical=False)
        .sign(inter_key, hashes.SHA256())
    )

    der = serialization.Encoding.DER
    return CertTriple(
        root_der=root.public_bytes(der),
        intermediate_der=inter.public_bytes(der),
        leaf_der=leaf.public_bytes(der),
        intermediate_aia_url=intermediate_aia_url,
        root_aia_url=root_aia_url,
    )
