"""Outbound URL validation for optional portal BYOK calls."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeOutboundUrl(ValueError):
    """Raised when an operator-supplied model endpoint is unsafe to contact."""


def validate_public_https_url(value: str) -> str:
    """Permit HTTPS endpoints resolving only to globally routable IP addresses."""
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeOutboundUrl("BYOK base_url must be an https URL without credentials")
    if parsed.port not in (None, 443):
        raise UnsafeOutboundUrl("BYOK base_url may only use port 443")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeOutboundUrl("BYOK hostname cannot be resolved") from exc
    resolved = {item[4][0] for item in addresses}
    if not resolved:
        raise UnsafeOutboundUrl("BYOK hostname has no resolved addresses")
    for address in resolved:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeOutboundUrl("BYOK endpoint must not resolve to private or reserved network space")
    return value.rstrip("/")
