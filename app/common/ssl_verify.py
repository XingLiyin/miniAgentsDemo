"""SSL verification helper for httpx clients.

Bridges Python's SSL layer to the OS certificate store so that corporate /
intranet CAs are trusted automatically.

Usage::

    from app.common.ssl_verify import make_ssl_verify, with_ssl_retry
    with httpx.Client(verify=make_ssl_verify(), trust_env=False) as client:
        ...

Priority (evaluated once and cached by make_ssl_verify):

Tier 1: ``http_ssl_verify = False``  → False (skip verification entirely)
Tier 2: ``http_ca_bundle`` set       → base context + bundle entries (file paths
        and/or download URLs, ';'-separated)
Tier 3: otherwise                    → base context only (certifi defaults +
        Windows registry + env-var CA bundles + AppData-cached CAs); reactive
        AIA chain-fetch fills any remaining gap via with_ssl_retry.
"""

from __future__ import annotations

import hashlib
import logging
import os
import ssl
import struct
import sys
from functools import lru_cache
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry paths — covers standard + GPO + enterprise + current-user
# ---------------------------------------------------------------------------
_WIN_REGISTRY_CERT_PATHS: list[tuple[int, str]] = []

if sys.platform == "win32":
    import winreg  # noqa: E402

    _WIN_REGISTRY_CERT_PATHS = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\SystemCertificates\Root\Certificates"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\SystemCertificates\CA\Certificates"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\SystemCertificates\AuthRoot\Certificates"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Policy\Microsoft\SystemCertificates\Root\Certificates"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Policy\Microsoft\SystemCertificates\CA\Certificates"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\EnterpriseCertificates\Root\Certificates"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\EnterpriseCertificates\CA\Certificates"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\SystemCertificates\Root\Certificates"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\SystemCertificates\CA\Certificates"),
    ]


# ---------------------------------------------------------------------------
# AppData cache for fetched corporate CA certs
# ---------------------------------------------------------------------------

def _ssl_cache_dir() -> Path:
    """Directory where fetched corporate CA certs are persisted (by fingerprint)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        root = Path(base) / "IPMaster-Cowork"
    else:
        root = Path(os.path.expanduser("~")) / ".ipmaster-cowork"
    return root / "ssl_certs"


def _save_ca_to_cache(der: bytes) -> Path:
    """Persist one DER cert to the cache, named by its SHA-256 fingerprint."""
    cache_dir = _ssl_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = hashlib.sha256(der).hexdigest()
    path = cache_dir / f"{fp}.der"
    if not path.exists():
        path.write_bytes(der)
    return path


def _collect_cached_ca_ders() -> list[bytes]:
    """Return all cached CA DER blobs (empty if cache dir missing)."""
    cache_dir = _ssl_cache_dir()
    if not cache_dir.is_dir():
        return []
    ders: list[bytes] = []
    for f in cache_dir.glob("*.der"):
        try:
            ders.append(f.read_bytes())
        except OSError:
            pass
    return ders


def _load_cached_cas(ctx: ssl.SSLContext) -> int:
    """Load all cached CA certs into *ctx*. Returns count loaded."""
    n = 0
    for der in _collect_cached_ca_ders():
        try:
            ctx.load_verify_locations(cadata=der)
            n += 1
        except Exception:
            pass
    return n


# ---------------------------------------------------------------------------
# Cert decoding — accept DER, PEM, or PKCS7 (.p7c) and normalise to DER
# ---------------------------------------------------------------------------

def _decode_certs(data: bytes) -> list[bytes]:
    """Decode *data* into a list of DER certs. Accepts DER, PEM, or PKCS7.

    Returns [] if nothing parseable is found.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs7

    der_enc = serialization.Encoding.DER

    # Single DER cert
    try:
        cert = x509.load_der_x509_certificate(data)
        return [cert.public_bytes(der_enc)]
    except Exception:
        pass

    # PEM (possibly multiple)
    try:
        certs = x509.load_pem_x509_certificates(data)
        if certs:
            return [c.public_bytes(der_enc) for c in certs]
    except Exception:
        pass

    # PKCS7 DER (.p7c / .p7b) — common for AIA CA-Issuers responses
    try:
        certs = pkcs7.load_der_pkcs7_certificates(data)
        if certs:
            return [c.public_bytes(der_enc) for c in certs]
    except Exception:
        pass

    # PKCS7 PEM
    try:
        certs = pkcs7.load_pem_pkcs7_certificates(data)
        if certs:
            return [c.public_bytes(der_enc) for c in certs]
    except Exception:
        pass

    return []


# ---------------------------------------------------------------------------
# Cert inspection — AIA CA-Issuers URLs, self-signed test, CA test
# ---------------------------------------------------------------------------

def _extract_ca_issuer_urls(der: bytes) -> list[str]:
    """Return the CA-Issuers URLs from a cert's AIA extension (empty if none)."""
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, AuthorityInformationAccessOID

    try:
        cert = x509.load_der_x509_certificate(der)
        aia = cert.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_INFORMATION_ACCESS
        ).value
    except Exception:
        return []

    urls: list[str] = []
    for desc in aia:
        if desc.access_method == AuthorityInformationAccessOID.CA_ISSUERS:
            loc = desc.access_location
            if isinstance(loc, x509.UniformResourceIdentifier):
                u = loc.value
                if u.lower().startswith(("http://", "https://")):
                    urls.append(u)
    return urls


def _is_self_signed(der: bytes) -> bool:
    """True if subject == issuer (i.e. a root)."""
    from cryptography import x509
    try:
        cert = x509.load_der_x509_certificate(der)
        return cert.subject == cert.issuer
    except Exception:
        return False


def _is_ca_cert(der: bytes) -> bool:
    """True if BasicConstraints marks this as a CA."""
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID
    try:
        cert = x509.load_der_x509_certificate(der)
        bc = cert.extensions.get_extension_for_oid(
            ExtensionOID.BASIC_CONSTRAINTS
        ).value
        return bool(bc.ca)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Environment-variable CA bundles (IT often pushes these machine-wide)
# ---------------------------------------------------------------------------

_ENV_CA_FILE_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
_ENV_CA_DIR_VARS = ("SSL_CERT_DIR",)


def _load_env_var_cas(ctx: ssl.SSLContext) -> int:
    """Load CA bundles pointed to by common OpenSSL/requests/node env vars."""
    n = 0
    for var in _ENV_CA_FILE_VARS:
        p = os.environ.get(var)
        if p and os.path.isfile(p):
            try:
                ctx.load_verify_locations(cafile=p)
                n += 1
            except Exception:
                pass
    for var in _ENV_CA_DIR_VARS:
        d = os.environ.get(var)
        if d and os.path.isdir(d):
            try:
                ctx.load_verify_locations(capath=d)
                n += 1
            except Exception:
                pass
    return n


# ---------------------------------------------------------------------------
# HTTP GET helper (honours system proxy via httpx trust_env)
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 10) -> bytes | None:
    """GET *url* and return the raw body bytes, or None on failure.

    Uses httpx with trust_env=True so the system proxy is honoured. CA-Issuers
    URLs are almost always plain http, so verification is not a concern here.
    """
    import httpx
    try:
        with httpx.Client(timeout=timeout, trust_env=True, verify=False) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as exc:
        logger.debug("SSL: _http_get(%s) failed — %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# http_ca_bundle — multi-entry (file path OR http(s) URL), ';'-separated
# ---------------------------------------------------------------------------

def _parse_ca_bundle_entries(raw: str) -> tuple[list[str], list[str]]:
    """Split a ';'-separated http_ca_bundle into (file_paths, urls)."""
    files: list[str] = []
    urls: list[str] = []
    for part in raw.split(";"):
        entry = part.strip()
        if not entry:
            continue
        if entry.lower().startswith(("http://", "https://")):
            urls.append(entry)
        else:
            files.append(entry)
    return files, urls


def _load_ca_bundle(ctx: ssl.SSLContext, raw: str) -> int:
    """Load every entry of a multi-entry http_ca_bundle into *ctx*.

    File entries are read directly; URL entries are downloaded (and cached to
    AppData). Returns the number of certs loaded.
    """
    files, urls = _parse_ca_bundle_entries(raw)
    loaded = 0

    for path in files:
        try:
            ctx.load_verify_locations(cafile=path)
            loaded += 1
        except Exception as exc:
            logger.warning("SSL: failed to load CA bundle file %s — %s", path, exc)

    for url in urls:
        data = _http_get(url)
        if not data:
            continue
        for der in _decode_certs(data):
            try:
                ctx.load_verify_locations(cadata=der)
                _save_ca_to_cache(der)
                loaded += 1
            except Exception:
                pass

    return loaded


# ---------------------------------------------------------------------------
# Blob parsing
# ---------------------------------------------------------------------------

def _parse_cert_blob(blob: bytes) -> bytes | None:
    """Extract DER-encoded cert from a Windows registry cert Blob.

    Windows stores cert properties in the format:
        DWORD  dwPropId
        DWORD  dwReserved  (always 1 in practice)
        DWORD  cbData
        BYTE   data[cbData]

    CERT_CERT_PROP_ID = 32 (0x20) holds the raw DER X.509 cert.
    Both the 12-byte (with reserved) and 8-byte (without reserved) layouts
    are tried; whichever produces a byte sequence starting with 0x30
    (ASN.1 SEQUENCE) is accepted.
    """
    CERT_CERT_PROP_ID = 32
    for hdr in (12, 8):
        pos = 0
        while pos + hdr <= len(blob):
            if hdr == 12:
                prop_id, _, cb = struct.unpack_from("<III", blob, pos)
            else:
                prop_id, cb = struct.unpack_from("<II", blob, pos)
            data_start = pos + hdr
            if data_start + cb > len(blob) or cb > 65536:
                break
            if prop_id == CERT_CERT_PROP_ID and cb > 32:
                cand = blob[data_start: data_start + cb]
                if cand and cand[0] == 0x30:
                    return cand
            pos = data_start + cb
    return None


# ---------------------------------------------------------------------------
# Phase 1 — registry
# ---------------------------------------------------------------------------

def _load_registry_certs(ctx: ssl.SSLContext) -> tuple[int, dict[str, int]]:
    """Load certs from all Windows registry cert paths into *ctx*.

    Returns (total_loaded, {short_path_label: count}).
    """
    import winreg

    loaded = 0
    counts: dict[str, int] = {}

    for hive, reg_path in _WIN_REGISTRY_CERT_PATHS:
        hive_name = "HKLM" if hive == winreg.HKEY_LOCAL_MACHINE else "HKCU"
        label = hive_name + "\\" + "\\".join(reg_path.split("\\")[-3:])  # unique label
        try:
            store_key = winreg.OpenKey(hive, reg_path, 0, winreg.KEY_READ)
        except (FileNotFoundError, PermissionError, OSError):
            continue

        n = 0
        idx = 0
        while True:
            try:
                thumb = winreg.EnumKey(store_key, idx)
            except OSError:
                break
            idx += 1
            try:
                cert_key = winreg.OpenKey(store_key, thumb)
                try:
                    blob, _ = winreg.QueryValueEx(cert_key, "Blob")
                    if isinstance(blob, bytes):
                        der = _parse_cert_blob(blob)
                        if der:
                            try:
                                ctx.load_verify_locations(cadata=der)
                                n += 1
                                loaded += 1
                            except Exception:
                                pass
                finally:
                    cert_key.Close()
            except Exception:
                pass
        store_key.Close()
        if n:
            counts[label] = n

    return loaded, counts


# ---------------------------------------------------------------------------
# Phase 2 (reactive) — unverified probe to the caller's actual URL
# ---------------------------------------------------------------------------

def _chain_from_ssock(ssock) -> list[bytes]:
    """Extract the DER chain a TLS peer presented during an unverified handshake.

    Prefers SSLSocket.get_unverified_chain() (Python 3.10+) which returns the
    full chain (leaf + intermediates the proxy sent). Falls back to the leaf
    cert only.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    der_enc = serialization.Encoding.DER
    ders: list[bytes] = []

    get_chain = getattr(ssock, "get_unverified_chain", None)
    if callable(get_chain):
        try:
            for entry in get_chain() or []:
                if isinstance(entry, (bytes, bytearray)):
                    ders.append(bytes(entry))
                elif hasattr(entry, "public_bytes"):
                    ders.append(entry.public_bytes(der_enc))
        except Exception as exc:
            logger.debug("SSL: get_unverified_chain failed — %s", exc)

    if not ders:
        try:
            leaf = ssock.getpeercert(binary_form=True)
            if leaf:
                ders.append(leaf)
        except Exception as exc:
            logger.debug("SSL: getpeercert fallback failed — %s", exc)

    return ders


def _get_leaf_and_chain(url: str) -> list[bytes]:
    """Open an UNVERIFIED TLS connection to *url* and return the presented chain.

    Routes through the system HTTPS proxy (CONNECT tunnel) when configured,
    falling back to a direct connection. Returns [] on failure.
    """
    import socket
    import urllib.request
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return []
    target_host = parsed.hostname
    if not target_host:
        return []
    target_port = parsed.port or 443

    probe_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    probe_ctx.check_hostname = False
    probe_ctx.verify_mode = ssl.CERT_NONE

    raw_sock: socket.socket | None = None

    # Try via system proxy (CONNECT tunnel)
    try:
        proxies = urllib.request.getproxies()
        proxy_url = proxies.get("https") or proxies.get("http") or ""
        if proxy_url:
            if not proxy_url.startswith("http"):
                proxy_url = "http://" + proxy_url
            pp = urlparse(proxy_url)
            if pp.hostname:
                sock = socket.create_connection((pp.hostname, pp.port or 8080), timeout=8)
                connect_hdr = (
                    f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
                    f"Host: {target_host}:{target_port}\r\n\r\n"
                )
                sock.sendall(connect_hdr.encode())
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                if b" 200 " in resp[:50]:
                    raw_sock = sock
                    logger.debug("SSL probe: CONNECT tunnel via %s → %s:%s",
                                 pp.hostname, target_host, target_port)
                else:
                    sock.close()
                    logger.debug("SSL probe: proxy CONNECT non-200: %s", resp[:100])
    except Exception as exc:
        logger.debug("SSL probe: proxy tunnel failed — %s", exc)

    # Fall back to direct connection
    if raw_sock is None:
        try:
            raw_sock = socket.create_connection((target_host, target_port), timeout=5)
        except Exception as exc:
            logger.debug("SSL probe: direct connect failed — %s", exc)
            return []

    try:
        with probe_ctx.wrap_socket(raw_sock, server_hostname=target_host) as ssock:
            return _chain_from_ssock(ssock)
    except Exception as exc:
        logger.debug("SSL probe: TLS handshake failed — %s", exc)
        return []


# ---------------------------------------------------------------------------
# Reactive chain completion — walk AIA up to a self-signed root
# ---------------------------------------------------------------------------

_AIA_MAX_HOPS = 10  # safety bound against AIA loops


def _fetch_corporate_ca_chain(url: str) -> list[bytes]:
    """Obtain the corporate CA cert(s) needed to trust *url*.

    1. Unverified probe to *url* → the chain the proxy presents (leaf + any
       intermediates).
    2. For each non-root cert, if its issuer is missing, follow the AIA
       CA-Issuers URL to download the next cert up, until a self-signed root
       or no further AIA.
    3. Cache every CA cert collected to AppData.

    Returns the list of CA DER blobs collected (intermediates + root); [] on
    total failure.
    """
    presented = _get_leaf_and_chain(url)
    if not presented:
        return []

    collected: list[bytes] = []
    seen: set[str] = set()
    queue: list[bytes] = list(presented)
    hops = 0

    while queue and hops < _AIA_MAX_HOPS:
        der = queue.pop(0)
        fp = hashlib.sha256(der).hexdigest()
        if fp in seen:
            continue
        seen.add(fp)

        if _is_ca_cert(der):
            collected.append(der)
            _save_ca_to_cache(der)

        if _is_self_signed(der):
            continue

        # Walk up via AIA to fetch the issuer (only if not already present)
        for issuer_url in _extract_ca_issuer_urls(der):
            data = _http_get(issuer_url)
            if not data:
                continue
            for issuer_der in _decode_certs(data):
                if hashlib.sha256(issuer_der).hexdigest() not in seen:
                    queue.append(issuer_der)
            hops += 1

    return collected


# ---------------------------------------------------------------------------
# Base trust store — certifi defaults + registry + env vars + AppData cache
# ---------------------------------------------------------------------------

def _build_base_context() -> ssl.SSLContext:
    """Build the base SSL context shared by all callers (before reactive AIA)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    # Public roots (certifi / OS default)
    try:
        ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
    except Exception as exc:
        logger.debug("SSL: load_default_certs failed — %s", exc)

    reg_count = 0
    if sys.platform == "win32":
        reg_count, reg_counts = _load_registry_certs(ctx)
        logger.warning("SSL base: registry loaded %d certs. Per-path: %s",
                       reg_count, reg_counts)

    env_count = _load_env_var_cas(ctx)
    cache_count = _load_cached_cas(ctx)
    logger.warning("SSL base: env-var CAs=%d, AppData-cached CAs=%d",
                   env_count, cache_count)

    return ctx


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def make_ssl_verify() -> Union[bool, ssl.SSLContext, str]:
    """Return the ``verify=`` argument for httpx clients (cached).

    Tier 1: http_ssl_verify=False  → False (skip verification)
    Tier 2: http_ca_bundle set     → base context + bundle entries
    Tier 3: otherwise              → base context (reactive AIA fills gaps)
    """
    from app.config.settings import get_settings
    cfg = get_settings()

    if not cfg.http_ssl_verify:
        logger.warning("SSL: verification DISABLED (http_ssl_verify=False)")
        return False

    ctx = _build_base_context()

    if cfg.http_ca_bundle:
        n = _load_ca_bundle(ctx, cfg.http_ca_bundle)
        logger.warning("SSL: loaded %d certs from http_ca_bundle", n)

    return ctx


def _is_cert_verify_error(exc: BaseException) -> bool:
    """True if *exc* (or a cause in its chain) is an SSL cert verification error."""
    seen = 0
    cur: BaseException | None = exc
    while cur is not None and seen < 10:
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        # httpx wraps ssl errors; match by message as a fallback
        msg = str(cur).upper()
        if "CERTIFICATE_VERIFY_FAILED" in msg or "CERTIFICATE VERIFY FAILED" in msg:
            return True
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return False


def with_ssl_retry(do_request, url: str):
    """Run do_request(verify); on cert-verify failure, fetch the corporate CA
    for *url*, refresh the cached context, and retry once.

    do_request(verify) must perform the request with the given verify value and
    return its result (raising on failure).
    """
    try:
        return do_request(make_ssl_verify())
    except Exception as exc:  # noqa: BLE001 — we re-raise unless it's a cert error
        if not _is_cert_verify_error(exc):
            raise
        logger.warning("SSL: cert verify failed for %s — fetching corporate CA", url)
        cas = _fetch_corporate_ca_chain(url)
        if not cas:
            logger.warning("SSL: corporate CA fetch yielded nothing — re-raising")
            raise
        make_ssl_verify.cache_clear()
        logger.warning("SSL: fetched %d CA cert(s); retrying request once", len(cas))
        return do_request(make_ssl_verify())
