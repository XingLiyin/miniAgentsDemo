# 企业 SSL 检测代理 CA 自动信任 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 IPMaster-Cowork 后端在公司 SSL 检测代理环境下自动信任企业 CA，无需手动设置 `http_ssl_verify=false`。

**Architecture:** 反应式三级分流。第 3 级在真实连接 cert-verify 失败时，对调用方实际 URL 做一次不验证握手，优先用握手已呈现的证书链，缺失部分沿 AIA 向上 HTTP 下载到根，结果持久化到 AppData 缓存并喂给所有调用点。基础信任库由 certifi + Windows 注册表 + 环境变量 CA + AppData 缓存四层组成。

**Tech Stack:** Python 3.11–3.12, `ssl`, `cryptography`（AIA 解析 / PKCS7 解码）, `httpx`, `urllib.request.getproxies`, pytest。

**参考 spec:** `docs/superpowers/specs/2026-06-09-ssl-corporate-ca-design.md`

---

## File Structure

| 文件 | 职责 |
|------|------|
| `app/common/ssl_verify.py` | 重写：四层基础上下文 + 反应式 AIA 抓取 + 三级分流 + `with_ssl_retry`。保留 `_parse_cert_blob` / `_load_registry_certs`，删除 `_get_leaf_cert` / `_build_chain_via_windows_cryptoapi` / `_PROBE_FALLBACK_URLS` / `_make_ssl_ctx_windows` 的 Phase 2 部分 |
| `app/llm/transport_httpx.py` | `post` 改为经 `with_ssl_retry` |
| `app/api/v1/routes/llms.py` | `_fetch_available_models` 与 ping 路径经 `with_ssl_retry` |
| `tests/conftest.py` | 新增 pytest fixture：用 `cryptography` 现场生成 root/intermediate/leaf 证书，供测试使用 |
| `tests/test_ssl_verify.py` | 新增：覆盖缓存、证书解码、AIA 解析、环境变量、bundle 解析、链补全 |

调用点 `mcp_http_provider.py` / `skill_pull_service.py` / `skill_store_client.py` 不改，靠 AppData 缓存被动获益。

---

## Task 1: 测试用证书 fixture

**Files:**
- Create/Modify: `tests/conftest.py`

- [ ] **Step 1: 写 conftest fixture（现场生成证书链）**

新建或追加到 `tests/conftest.py`：

```python
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
```

- [ ] **Step 2: 验证 fixture 可用**

Run: `uv run pytest tests/ -q --collect-only`
Expected: 收集成功，无 import 错误。

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add cert_triple fixture for ssl_verify tests"
```

---

## Task 2: AppData 缓存层

**Files:**
- Modify: `app/common/ssl_verify.py`
- Test: `tests/test_ssl_verify.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_ssl_verify.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ssl_verify.py::test_save_and_load_cached_cas -v`
Expected: FAIL — `AttributeError: module 'app.common.ssl_verify' has no attribute '_ssl_cache_dir'`

- [ ] **Step 3: 实现缓存层**

在 `app/common/ssl_verify.py` 顶部 import 区补充：

```python
import hashlib
import os
from pathlib import Path
```

在 `_parse_cert_blob` 之前新增：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ssl_verify.py::test_save_and_load_cached_cas -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/common/ssl_verify.py tests/test_ssl_verify.py
git commit -m "feat(ssl): AppData cache for fetched corporate CA certs"
```

---

## Task 3: 证书解码（DER / PEM / PKCS7）

**Files:**
- Modify: `app/common/ssl_verify.py`
- Test: `tests/test_ssl_verify.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ssl_verify.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ssl_verify.py -k decode_certs -v`
Expected: FAIL — `AttributeError: ... has no attribute '_decode_certs'`

- [ ] **Step 3: 实现解码器**

在 `app/common/ssl_verify.py` 新增（放在缓存层之后）：

```python
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
```

> 注：`x509.load_pem_x509_certificates`（复数）需 cryptography ≥ 39。项目已装的版本满足；若收集报错，退回逐块解析。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ssl_verify.py -k decode_certs -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/common/ssl_verify.py tests/test_ssl_verify.py
git commit -m "feat(ssl): decode DER/PEM/PKCS7 cert payloads"
```

---

## Task 4: AIA 解析与证书属性判定

**Files:**
- Modify: `app/common/ssl_verify.py`
- Test: `tests/test_ssl_verify.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ssl_verify.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ssl_verify.py -k "ca_issuer or self_signed or is_ca" -v`
Expected: FAIL — 缺少 `_extract_ca_issuer_urls`

- [ ] **Step 3: 实现**

在 `app/common/ssl_verify.py` 新增（放在 `_decode_certs` 之后）：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ssl_verify.py -k "ca_issuer or self_signed or is_ca" -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/common/ssl_verify.py tests/test_ssl_verify.py
git commit -m "feat(ssl): AIA CA-Issuers parsing + CA/self-signed inspection"
```

---

## Task 5: 环境变量 CA 加载

**Files:**
- Modify: `app/common/ssl_verify.py`
- Test: `tests/test_ssl_verify.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ssl_verify.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ssl_verify.py -k env_var_cas -v`
Expected: FAIL — 缺少 `_load_env_var_cas`

- [ ] **Step 3: 实现**

在 `app/common/ssl_verify.py` 新增：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ssl_verify.py -k env_var_cas -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/common/ssl_verify.py tests/test_ssl_verify.py
git commit -m "feat(ssl): load CA bundles from SSL_CERT_FILE/REQUESTS_CA_BUNDLE/etc"
```

---

## Task 6: 多条目 `http_ca_bundle`（文件 / URL）

**Files:**
- Modify: `app/common/ssl_verify.py`
- Test: `tests/test_ssl_verify.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ssl_verify.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ssl_verify.py -k ca_bundle -v`
Expected: FAIL — 缺少 `_parse_ca_bundle_entries` / `_http_get` / `_load_ca_bundle`

- [ ] **Step 3: 实现**

在 `app/common/ssl_verify.py` 新增：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ssl_verify.py -k ca_bundle -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/common/ssl_verify.py tests/test_ssl_verify.py
git commit -m "feat(ssl): multi-entry http_ca_bundle (file path or downloadable URL)"
```

---

## Task 7: 不验证握手取链（`get_unverified_chain`）

**Files:**
- Modify: `app/common/ssl_verify.py`（替换旧 `_get_leaf_cert`）
- Test: `tests/test_ssl_verify.py`

> 此函数做真实 socket I/O，不写网络单测；用一个针对解析路径的轻量测试 + 手测覆盖。这里只验证：当 `get_unverified_chain` 可用时返回多张 DER，不可用时回退到单张叶子。

- [ ] **Step 1: 写失败测试（用假 ssock 验证提取逻辑）**

追加到 `tests/test_ssl_verify.py`：

```python
def test_chain_from_ssock_prefers_unverified_chain(cert_triple):
    class FakeSSock:
        def get_unverified_chain(self):
            # cryptography-style: objects with public_bytes(Encoding.DER)
            from cryptography import x509
            return [
                x509.load_der_x509_certificate(cert_triple.leaf_der),
                x509.load_der_x509_certificate(cert_triple.intermediate_der),
            ]
        def getpeercert(self, binary_form=False):
            return cert_triple.leaf_der

    ders = sv._chain_from_ssock(FakeSSock())
    assert cert_triple.leaf_der in ders
    assert cert_triple.intermediate_der in ders


def test_chain_from_ssock_fallback_leaf_only(cert_triple):
    class FakeSSock:
        def getpeercert(self, binary_form=False):
            assert binary_form is True
            return cert_triple.leaf_der

    ders = sv._chain_from_ssock(FakeSSock())
    assert ders == [cert_triple.leaf_der]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ssl_verify.py -k chain_from_ssock -v`
Expected: FAIL — 缺少 `_chain_from_ssock`

- [ ] **Step 3: 实现 `_chain_from_ssock` 与新的 `_get_leaf_and_chain`**

在 `app/common/ssl_verify.py` 中，**删除**旧的 `_get_leaf_cert` 整个函数，替换为：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ssl_verify.py -k chain_from_ssock -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/common/ssl_verify.py tests/test_ssl_verify.py
git commit -m "feat(ssl): unverified probe captures full presented chain via get_unverified_chain"
```

---

## Task 8: 反应式 CA 链补全 `_fetch_corporate_ca_chain`

**Files:**
- Modify: `app/common/ssl_verify.py`（删除旧 `_build_chain_via_windows_cryptoapi`）
- Test: `tests/test_ssl_verify.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ssl_verify.py`：

```python
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
    # And both were cached
    cached = sv._collect_cached_ca_ders()
    assert cert_triple.intermediate_der in cached
    assert cert_triple.root_der in cached


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ssl_verify.py -k fetch_corporate -v`
Expected: FAIL — 缺少 `_fetch_corporate_ca_chain`

- [ ] **Step 3: 实现；删除旧 ctypes 函数**

在 `app/common/ssl_verify.py` 中 **删除** 整个 `_build_chain_via_windows_cryptoapi` 函数（及其内部所有 ctypes 结构定义）。新增：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ssl_verify.py -k fetch_corporate -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/common/ssl_verify.py tests/test_ssl_verify.py
git commit -m "feat(ssl): reactive AIA chain completion to self-signed root"
```

---

## Task 9: 基础上下文重写 + 三级分流 + `with_ssl_retry`

**Files:**
- Modify: `app/common/ssl_verify.py`（重写 `_make_ssl_ctx_windows`→`_build_base_context`、`make_ssl_verify`、新增 `with_ssl_retry`）
- Test: `tests/test_ssl_verify.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ssl_verify.py`：

```python
def test_with_ssl_retry_success_first_try(monkeypatch):
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    calls = []
    def do(verify):
        calls.append(verify)
        return "ok"
    assert sv.with_ssl_retry(do, "https://api.corp.test") == "ok"
    assert calls == [True]


def test_with_ssl_retry_fetches_then_retries(monkeypatch, cert_triple):
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    fetched = {"n": 0}
    monkeypatch.setattr(sv, "_fetch_corporate_ca_chain",
                        lambda url: (fetched.__setitem__("n", 1) or [cert_triple.root_der]))
    # make_ssl_verify is lru_cache in real code; here it's a plain lambda, so
    # patch cache_clear to a no-op attribute
    monkeypatch.setattr(sv.make_ssl_verify, "cache_clear", lambda: None, raising=False)

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


def test_with_ssl_retry_reraises_non_cert_error(monkeypatch):
    monkeypatch.setattr(sv, "make_ssl_verify", lambda: True)
    def do(verify):
        raise ValueError("unrelated")
    import pytest
    with pytest.raises(ValueError):
        sv.with_ssl_retry(do, "https://api.corp.test")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ssl_verify.py -k with_ssl_retry -v`
Expected: FAIL — 缺少 `with_ssl_retry`

- [ ] **Step 3: 重写上下文构建、三级分流、新增 retry**

在 `app/common/ssl_verify.py` 中：

(a) **删除** `_PROBE_FALLBACK_URLS` 常量。

(b) **替换** `_make_ssl_ctx_windows` 为 `_build_base_context`：

```python
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
```

(c) **替换** `make_ssl_verify` 为三级分流并新增 `with_ssl_retry`：

```python
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
```

(d) 确认 `from typing import Union` 仍在；`functools.lru_cache` import 仍在。

- [ ] **Step 4: 运行整个文件测试**

Run: `uv run pytest tests/test_ssl_verify.py -v`
Expected: 全部 PASS（含此前各任务）

- [ ] **Step 5: Commit**

```bash
git add app/common/ssl_verify.py tests/test_ssl_verify.py
git commit -m "feat(ssl): 4-layer base context + 3-tier verify + with_ssl_retry"
```

---

## Task 10: 接入调用点

**Files:**
- Modify: `app/llm/transport_httpx.py`
- Modify: `app/api/v1/routes/llms.py`

- [ ] **Step 1: 改 `transport_httpx.py` 的 `post`**

将 `HttpxTransport.post`（约 19–31 行）改为经 `with_ssl_retry`：

```python
    def post(self, url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """发送 POST 请求并返回 JSON 响应（非流式）。"""
        from app.common.ssl_verify import with_ssl_retry

        def _do(verify):
            with httpx.Client(timeout=timeout or self._timeout, trust_env=False, verify=verify) as client:
                resp = client.post(url, headers=headers, json=json)
                resp.raise_for_status()
                return resp.json()

        try:
            return with_ssl_retry(_do, url)
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f'HTTP 状态错误: {exc.response.status_code} {exc.response.text}') from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError('HTTP 请求超时') from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f'HTTP 请求失败: {exc}') from exc
```

> `stream_post` 不改（依赖已缓存的 CA），保留现有 `verify=make_ssl_verify()`。`make_ssl_verify` 的 import 保留在文件顶部。

- [ ] **Step 2: 改 `llms.py` 的 `_fetch_available_models`**

将 `_fetch_available_models`（约 92–117 行）的请求部分改为经 `with_ssl_retry`：

```python
def _fetch_available_models(style: str, api_key: str, base_url: str) -> list[str]:
    """调用 provider 的 /v1/models 接口，返回模型 ID 列表。"""
    import httpx
    from app.common.ssl_verify import with_ssl_retry

    base = base_url.rstrip("/") or (
        "https://api.anthropic.com" if style == "anthropic" else "https://api.openai.com"
    )
    url = f"{base}/v1/models"
    headers = (
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        if style == "anthropic"
        else {"Authorization": f"Bearer {api_key}"}
    )

    def _do(verify):
        with httpx.Client(timeout=15, trust_env=False, verify=verify) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    try:
        data = with_ssl_retry(_do, url)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"HTTP {exc.response.status_code}: {exc.response.text[:300]}")
    except Exception as exc:
        raise RuntimeError(str(exc))

    items = data.get("data") or []
    return sorted(item.get("id", "") for item in items if item.get("id"))
```

> ping 端点（`/{name}/ping`）通过 `HttpxTransport`（已在 Step 1 接入）发请求，自动获益，无需单独改。确认 `ping` 调用链确实走 `transport.post`；若走的是 stream，则补包一层 `with_ssl_retry`。

- [ ] **Step 3: 运行后端测试套件**

Run: `uv run pytest tests/ -q`
Expected: 全部 PASS（无回归）

- [ ] **Step 4: 类型/语法自检**

Run: `uv run python -c "import app.common.ssl_verify; import app.llm.transport_httpx; import app.api.v1.routes.llms; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 5: Commit**

```bash
git add app/llm/transport_httpx.py app/api/v1/routes/llms.py
git commit -m "feat(ssl): route LLM test + chat transport through with_ssl_retry"
```

---

## Task 11: 手测验证（打包构建）

> 单测无法覆盖真实代理环境。按 CLAUDE.md 构建注意事项执行一次完整 PyInstaller 重建并在用户机器实测。

- [ ] **Step 1: 升级版本号**

`electron/package.json`：`0.2.54` → `0.2.55`（触发 main.js 缓存清理）。

- [ ] **Step 2: 完整构建**

> 注意：FE 未改可跳过前端构建，但 **不可** `-SkipBackend`（PyInstaller 会把后端重新冻结）。绝不要 `*>&1`（会产生假 exit-1）。先删除旧 `win-unpacked/`。

```
uv run pyinstaller "packaging\ipmaster-cowork-desktop.spec" --noconfirm --distpath "build\dist" --workpath "build\work"
Copy-Item "resources" "build\dist\ipmaster-cowork" -Recurse -Force
cd electron; npm run build
```

- [ ] **Step 3: 用户实测**

在公司网络下，填 LLM 配置点测试。预期日志（WARNING 级）：
```
SSL base: registry loaded N certs ...
SSL base: env-var CAs=.., AppData-cached CAs=..
SSL: cert verify failed for https://... — fetching corporate CA
SSL: fetched M CA cert(s); retrying request once
```
测试应成功返回模型列表。重启应用后再次测试，应**不再**出现 "cert verify failed"（CA 已从 AppData 缓存直接加载）。

- [ ] **Step 4: 验证缓存落盘**

确认 `%APPDATA%\IPMaster-Cowork\ssl_certs\` 下出现 `<sha256>.der` 文件。

- [ ] **Step 5: 发布（用户手动）**

实测通过后，用户手动发布到更新服务器（10.25.228.203:8077）。

---

## Self-Review Notes

- **Spec coverage:** 三级分流(Task 9) / 多条目 bundle(Task 6) / 四层基础库(Task 9) / get_unverified_chain(Task 7) / 环境变量 CA(Task 5) / AIA 下载到根(Task 8) / AppData 缓存(Task 2) / 共享 with_ssl_retry + 接入点(Task 9,10) / 流式不包(Task 10 注) — 全部覆盖。
- **删除项:** 旧 `_get_leaf_cert`(Task 7)、`_build_chain_via_windows_cryptoapi`(Task 8)、`_PROBE_FALLBACK_URLS`(Task 9)、`_make_ssl_ctx_windows`(Task 9 替换) — 已显式列出。
- **类型一致:** `_decode_certs`→list[bytes]、`_get_leaf_and_chain`→list[bytes]、`_fetch_corporate_ca_chain`→list[bytes]、`make_ssl_verify`→bool|SSLContext|str、`with_ssl_retry(do_request,url)` 全程一致。
- **保留项:** `_parse_cert_blob`、`_load_registry_certs`、`_WIN_REGISTRY_CERT_PATHS` 不动。
```
