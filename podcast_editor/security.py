import hashlib
import ipaddress
import socket
from urllib.parse import urlparse
from urllib.parse import urljoin

import httpx
from fastapi import HTTPException, Request

from .config import Settings


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def trusted_origins(settings: Settings) -> set[str]:
    configured = {item.strip().rstrip("/") for item in settings.trusted_origins.split(",")}
    configured.discard("")
    configured.add(settings.app_base_url.rstrip("/"))
    if settings.better_auth_url:
        configured.add(settings.better_auth_url.rstrip("/"))
    return configured


def enforce_same_origin(request: Request, settings: Settings) -> None:
    if request.method not in UNSAFE_METHODS:
        return
    if request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
        raise HTTPException(status_code=403, detail="cross-site request blocked")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in trusted_origins(settings):
        raise HTTPException(status_code=403, detail="untrusted request origin")
    referer = request.headers.get("referer")
    if not origin and referer:
        parsed = urlparse(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if referer_origin not in trusted_origins(settings):
            raise HTTPException(status_code=403, detail="untrusted request origin")


def client_rate_key(request: Request, settings: Settings) -> str:
    address = request.headers.get("x-real-ip", "").strip()
    if not address and request.client:
        address = request.client.host
    secret = settings.rate_limit_salt or settings.better_auth_secret or "local-development"
    return hashlib.sha256(f"{secret}:{address or 'unknown'}".encode()).hexdigest()


def validate_public_http_url(value: str) -> str:
    if len(value) > 2048:
        raise HTTPException(status_code=422, detail="URL is too long")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Only public HTTP and HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="URLs containing credentials are not allowed")
    if parsed.port not in {None, 80, 443}:
        raise HTTPException(
            status_code=422, detail="Only standard HTTP and HTTPS ports are allowed"
        )
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
        raise HTTPException(status_code=422, detail="Local network URLs are not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise HTTPException(status_code=422, detail="URL host could not be resolved") from exc
    if not addresses:
        raise HTTPException(status_code=422, detail="URL host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise HTTPException(
                status_code=422, detail="Local or reserved network URLs are not allowed"
            )
    return value


def public_http_request(
    method: str,
    url: str,
    *,
    max_bytes: int = 5_000_000,
    max_redirects: int = 5,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    current = url
    with httpx.Client(follow_redirects=False, timeout=20.0) as client:
        for _ in range(max_redirects + 1):
            validate_public_http_url(current)
            with client.stream(method, current, headers=headers) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                if method.upper() == "HEAD":
                    return httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        request=response.request,
                    )
                declared = int(response.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise HTTPException(status_code=422, detail="Remote response is too large")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise HTTPException(status_code=422, detail="Remote response is too large")
                headers = httpx.Headers(response.headers)
                # iter_bytes() returns decoded content. Keeping the upstream encoding
                # metadata would make the reconstructed response decode it a second time.
                headers.pop("content-encoding", None)
                headers.pop("content-length", None)
                return httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=bytes(content),
                    request=response.request,
                )
    raise HTTPException(status_code=422, detail="Too many URL redirects")


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "media-src 'self' blob: https:; connect-src 'self' https://unpkg.com https://*.supabase.co; "
        "worker-src 'self' blob:; manifest-src 'self'; upgrade-insecure-requests"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
