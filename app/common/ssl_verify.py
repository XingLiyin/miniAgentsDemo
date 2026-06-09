"""SSL verification helper for httpx clients.

Bridges Python's SSL layer to the OS certificate store so that corporate /
intranet CAs are trusted automatically.

Usage::

    from app.common.ssl_verify import make_ssl_verify
    with httpx.Client(verify=make_ssl_verify(), trust_env=False) as client:
        ...

Priority (evaluated once and cached):

1. ``http_ssl_verify = False``  → disable certificate verification entirely
2. ``http_ca_bundle`` set        → use an explicit CA bundle file path
3. Windows                       → winreg registry + Windows chain build → OpenSSL
4. Non-Windows / fallback        → ssl.create_default_context()

Windows cert-store strategy (0.2.54)
-------------------------------------
Previous approaches (0.2.48–0.2.53) that failed:

- truststore: calls CertVerifyCertificateChainPolicy → CERT_E_UNTRUSTED_ROOT
  (Windows Schannel policy gate rejects enterprise proxy CAs)
- ssl.create_default_context() / ctypes CertOpenSystemStoreW: CURRENT_USER
  aggregated view missed the enterprise registry paths
- PowerShell Cert:\\… export: same aggregated view, still missed the CA
- winreg explicit paths (0.2.53): ROOT(2)+CA(6)+AuthRoot(38)=76 certs loaded,
  enterprise/GPO paths all empty → corp CA not in any static registry key

Root cause: the corporate CA is delivered via Active Directory / AIA fetching,
NOT written to the standard registry certificate paths.  Only Windows
CertGetCertificateChain() reaches it (it searches enterprise stores, network
CTLs, and fetches intermediates via AIA automatically).

Current approach (Two-Phase):

Phase 1 – winreg: read all nine registry cert paths (ROOT, CA, AuthRoot ×
  LocalMachine/Policy/Enterprise/CurrentUser) and load into ssl.SSLContext.

Phase 2 – CertGetCertificateChain probe: make a TLS connection WITHOUT
  verification to the configured LLM API URL (or a well-known fallback), get
  the leaf cert that the corporate proxy presents, then hand it to Windows
  CertGetCertificateChain().  Windows builds the full chain using AIA, network
  CTLs, AD stores, etc. — everything it can reach.  We extract every cert in
  the chain and load them into the ssl.SSLContext.

Phase 2 must run AFTER Phase 1 because the probe URL itself may already work
correctly with Phase 1 certs; if not, chain-building still succeeds on the
Windows side and we get the missing CA cert DER bytes to add.
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
# Fallback probe URLs when default_llm_base_url is not configured or is HTTP.
# The corporate proxy intercepts ALL HTTPS traffic, so any accessible HTTPS
# URL will present the same corporate CA cert chain.  Use Windows-ecosystem
# URLs that corporate firewalls always allow (Windows Update depends on them).
# ---------------------------------------------------------------------------
_PROBE_FALLBACK_URLS = [
    "https://www.microsoft.com",      # always reachable from corporate Windows
    "https://ctldl.windowsupdate.com", # Windows CTL download endpoint
    "https://www.bing.com",
]

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
# Phase 2 — Windows CertGetCertificateChain probe
# ---------------------------------------------------------------------------

def _get_leaf_cert(url: str) -> bytes | None:
    """Connect to *url* WITHOUT SSL verification and return the leaf cert DER.

    In corporate environments, direct connections to external hosts are usually
    blocked by the firewall.  We therefore:

    1. Read the system HTTPS proxy via ``urllib.request.getproxies()`` (which
       reads the Windows registry on Windows).
    2. If a proxy is found, open a plain TCP connection to the proxy and issue
       an HTTP CONNECT tunnel to the target host.  The proxy performs SSL
       inspection and returns *its own* certificate chain — signed by the
       corporate CA — which is exactly the chain we need.
    3. Fall back to a direct connection if no proxy is configured or the
       tunnel fails.
    """
    import socket
    import urllib.request
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    target_host = parsed.hostname
    if not target_host:
        return None
    target_port = parsed.port or 443

    probe_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    probe_ctx.check_hostname = False
    probe_ctx.verify_mode = ssl.CERT_NONE

    raw_sock: socket.socket | None = None

    # ── Try via system proxy ────────────────────────────────────────────────
    try:
        proxies = urllib.request.getproxies()  # reads Windows registry on Win
        proxy_url = proxies.get("https") or proxies.get("http") or ""
        if proxy_url:
            if not proxy_url.startswith("http"):
                proxy_url = "http://" + proxy_url
            pp = urlparse(proxy_url)
            proxy_host = pp.hostname
            proxy_port = pp.port or 8080
            if proxy_host:
                sock = socket.create_connection((proxy_host, proxy_port), timeout=8)
                connect_hdr = (
                    f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
                    f"Host: {target_host}:{target_port}\r\n\r\n"
                )
                sock.sendall(connect_hdr.encode())
                # Read until end of HTTP response headers
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                if b" 200 " in resp[:50]:
                    raw_sock = sock
                    logger.debug(
                        "SSL probe: CONNECT tunnel established via %s:%s → %s:%s",
                        proxy_host, proxy_port, target_host, target_port,
                    )
                else:
                    sock.close()
                    logger.debug(
                        "SSL probe: proxy CONNECT returned non-200 "
                        "(may need NTLM auth): %s", resp[:100]
                    )
    except Exception as exc:
        logger.debug("SSL probe: proxy tunnel failed — %s", exc)

    # ── Fall back to direct connection ──────────────────────────────────────
    if raw_sock is None:
        try:
            raw_sock = socket.create_connection((target_host, target_port), timeout=5)
            logger.debug("SSL probe: direct connection to %s:%s", target_host, target_port)
        except Exception as exc:
            logger.debug("SSL probe: direct connection failed — %s", exc)
            return None

    # ── TLS handshake ───────────────────────────────────────────────────────
    try:
        with probe_ctx.wrap_socket(raw_sock, server_hostname=target_host) as ssock:
            return ssock.getpeercert(binary_form=True)
    except Exception as exc:
        logger.debug("SSL probe: TLS handshake failed — %s", exc)
        return None


def _build_chain_via_windows_cryptoapi(leaf_der: bytes) -> list[bytes]:
    """Use Windows CertGetCertificateChain to build the complete cert chain.

    Windows will use ALL available sources (registry stores, AIA fetching,
    enterprise / AD stores, CTLs) to build the chain.  We extract every
    DER-encoded cert from the returned chain, including CA certs that are
    NOT in any local registry path.

    Returns a list of DER bytes for all certs in the first chain (leaf first,
    root last).  Returns [] on failure.
    """
    import ctypes
    import ctypes.wintypes

    crypt32 = ctypes.windll.crypt32

    # ── Struct definitions ──────────────────────────────────────────────────

    class _CERT_CONTEXT(ctypes.Structure):
        _fields_ = [
            ("dwCertEncodingType", ctypes.wintypes.DWORD),
            ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbCertEncoded", ctypes.wintypes.DWORD),
            ("pCertInfo", ctypes.c_void_p),
            ("hCertStore", ctypes.c_void_p),
        ]

    class _CERT_TRUST_STATUS(ctypes.Structure):
        _fields_ = [
            ("dwErrorStatus", ctypes.wintypes.DWORD),
            ("dwInfoStatus", ctypes.wintypes.DWORD),
        ]

    class _CERT_CHAIN_ELEMENT(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("pCertContext", ctypes.POINTER(_CERT_CONTEXT)),
            ("TrustStatus", _CERT_TRUST_STATUS),
            ("pRevocationInfo", ctypes.c_void_p),
            ("pIssuanceUsage", ctypes.c_void_p),
            ("pApplicationUsage", ctypes.c_void_p),
            ("pwszExtendedErrorInfo", ctypes.c_wchar_p),
        ]

    class _CERT_SIMPLE_CHAIN(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("TrustStatus", _CERT_TRUST_STATUS),
            ("cElement", ctypes.wintypes.DWORD),
            ("rgpElement",
             ctypes.POINTER(ctypes.POINTER(_CERT_CHAIN_ELEMENT))),
            ("pTrustListInfo", ctypes.c_void_p),
            ("fHasRevocationFreshnessTime", ctypes.wintypes.BOOL),
            ("dwRevocationFreshnessTime", ctypes.wintypes.DWORD),
        ]

    class _CERT_CHAIN_CONTEXT(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("TrustStatus", _CERT_TRUST_STATUS),
            ("cChain", ctypes.wintypes.DWORD),
            ("rgpChain",
             ctypes.POINTER(ctypes.POINTER(_CERT_SIMPLE_CHAIN))),
            ("cLowerQualityChainContext", ctypes.wintypes.DWORD),
            ("rgpLowerQualityChainContext", ctypes.c_void_p),
            ("fHasRevocationFreshnessTime", ctypes.wintypes.BOOL),
            ("dwRevocationFreshnessTime", ctypes.wintypes.DWORD),
        ]

    class _CTL_USAGE(ctypes.Structure):
        _fields_ = [
            ("cUsageIdentifier", ctypes.wintypes.DWORD),
            ("rgpszUsageIdentifier", ctypes.c_void_p),
        ]

    class _CERT_USAGE_MATCH(ctypes.Structure):
        _fields_ = [
            ("dwType", ctypes.wintypes.DWORD),
            ("Usage", _CTL_USAGE),
        ]

    class _CERT_CHAIN_PARA(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("RequestedUsage", _CERT_USAGE_MATCH),
        ]

    # ── Function signatures ─────────────────────────────────────────────────
    X509_ASN_ENCODING = 0x00000001

    crypt32.CertCreateCertificateContext.restype = ctypes.POINTER(_CERT_CONTEXT)
    crypt32.CertCreateCertificateContext.argtypes = [
        ctypes.wintypes.DWORD, ctypes.c_char_p, ctypes.wintypes.DWORD
    ]
    crypt32.CertGetCertificateChain.restype = ctypes.wintypes.BOOL
    crypt32.CertGetCertificateChain.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_CERT_CONTEXT),
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(_CERT_CHAIN_PARA),
        ctypes.wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(_CERT_CHAIN_CONTEXT)),
    ]
    crypt32.CertFreeCertificateChain.argtypes = [
        ctypes.POINTER(_CERT_CHAIN_CONTEXT)
    ]
    crypt32.CertFreeCertificateContext.argtypes = [
        ctypes.POINTER(_CERT_CONTEXT)
    ]

    # ── Build the chain ─────────────────────────────────────────────────────
    cert_ctx = crypt32.CertCreateCertificateContext(
        X509_ASN_ENCODING, leaf_der, len(leaf_der)
    )
    if not cert_ctx:
        logger.warning("SSL: CertCreateCertificateContext failed")
        return []

    chain_ders: list[bytes] = []
    try:
        para = _CERT_CHAIN_PARA()
        para.cbSize = ctypes.sizeof(_CERT_CHAIN_PARA)
        chain_ctx_pp = ctypes.POINTER(_CERT_CHAIN_CONTEXT)()

        ok = crypt32.CertGetCertificateChain(
            None, cert_ctx, None, None,
            ctypes.byref(para),
            0x00000001,  # CERT_CHAIN_CACHE_END_CERT
            None,
            ctypes.byref(chain_ctx_pp),
        )

        if ok and chain_ctx_pp:
            try:
                cc = chain_ctx_pp.contents
                for i in range(cc.cChain):
                    if not cc.rgpChain:
                        break
                    sc = cc.rgpChain[i].contents
                    for j in range(sc.cElement):
                        if not sc.rgpElement:
                            break
                        el = sc.rgpElement[j].contents
                        if el.pCertContext:
                            c = el.pCertContext.contents
                            try:
                                der = bytes(
                                    c.pbCertEncoded[:c.cbCertEncoded]
                                )
                                if der not in chain_ders:
                                    chain_ders.append(der)
                            except Exception:
                                pass
            finally:
                crypt32.CertFreeCertificateChain(chain_ctx_pp)
        else:
            logger.warning(
                "SSL: CertGetCertificateChain returned False "
                "(error 0x%08x)", ctypes.GetLastError()
            )
    finally:
        crypt32.CertFreeCertificateContext(cert_ctx)

    return chain_ders


# ---------------------------------------------------------------------------
# Top-level Windows SSL context builder
# ---------------------------------------------------------------------------

def _make_ssl_ctx_windows() -> ssl.SSLContext:
    """Build ssl.SSLContext via registry (Phase 1) + Windows chain build (Phase 2)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    # ── Phase 1: registry ───────────────────────────────────────────────────
    reg_count, reg_counts = _load_registry_certs(ctx)
    logger.warning(
        "SSL Phase 1 (registry): loaded %d certs. Per-path: %s",
        reg_count, reg_counts,
    )

    # ── Phase 2: Windows CertGetCertificateChain probe ─────────────────────
    try:
        from app.config.settings import get_settings
        cfg = get_settings()
        probe_urls: list[str] = []
        if cfg.default_llm_base_url:
            probe_urls.append(cfg.default_llm_base_url)
        probe_urls.extend(_PROBE_FALLBACK_URLS)

        leaf_der: bytes | None = None
        probe_used = ""
        for url in probe_urls:
            leaf_der = _get_leaf_cert(url)
            if leaf_der:
                probe_used = url
                break

        if leaf_der:
            chain_ders = _build_chain_via_windows_cryptoapi(leaf_der)
            chain_loaded = 0
            for der in chain_ders:
                try:
                    ctx.load_verify_locations(cadata=der)
                    chain_loaded += 1
                except Exception:
                    pass
            logger.warning(
                "SSL Phase 2 (CertGetCertificateChain via %s): "
                "chain has %d certs, loaded %d new",
                probe_used, len(chain_ders), chain_loaded,
            )
        else:
            logger.warning(
                "SSL Phase 2: all probe URLs unreachable — "
                "using registry certs only"
            )
    except Exception as exc:
        logger.warning("SSL Phase 2 failed: %s", exc)

    if reg_count == 0:
        raise RuntimeError(
            "SSL: no certs loaded from Windows registry — context is empty"
        )
    return ctx


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def make_ssl_verify() -> Union[bool, str]:
    """Return the ``verify=`` argument for httpx.Client / httpx.AsyncClient.

    Cached after the first call; the same SSLContext is reused for all
    connections.
    """
    from app.config.settings import get_settings

    cfg = get_settings()

    if not cfg.http_ssl_verify:
        logger.warning("SSL: verification DISABLED (http_ssl_verify=False)")
        return False

    if cfg.http_ca_bundle:
        logger.warning("SSL: using explicit CA bundle: %s", cfg.http_ca_bundle)
        return cfg.http_ca_bundle

    if sys.platform == "win32":
        try:
            return _make_ssl_ctx_windows()  # type: ignore[return-value]
        except Exception as exc:
            logger.warning(
                "SSL: Windows cert loader failed (%s); "
                "falling back to ssl.create_default_context()",
                exc,
            )

    ctx = ssl.create_default_context()
    logger.warning("SSL: using ssl.create_default_context() as fallback")
    return ctx  # type: ignore[return-value]
