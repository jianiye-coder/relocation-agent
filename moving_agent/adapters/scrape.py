"""Shared plumbing for the unofficial website adapters (Public Storage, U-Haul, Budget).

These adapters read the same public pages and endpoints a person's browser uses to
get a price. They are opt-in (ENABLE_UNOFFICIAL_ADAPTERS), identify themselves
with a project User-Agent, make a handful of requests per search, never book or
reserve anything, and never try to get past bot protection: a 403, a challenge
page or a CAPTCHA is reported as `blocked`, not worked around.
"""

from __future__ import annotations

import html
import re

import httpx

from .base import AdapterError, ErrorCode, Location

USER_AGENT = "Mozilla/5.0 (compatible; relocation-agent/0.1; +https://github.com/jianiye-coder/relocation-agent)"
TIMEOUT_S = 15
ZIP_URL = "https://api.zippopotam.us/us/{zip}"
CHALLENGE_MARKERS = ("captcha", "are you a robot", "access denied", "request unsuccessful")


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US"},
                             follow_redirects=True, timeout=TIMEOUT_S)


def check(response: httpx.Response, provider: str) -> httpx.Response:
    """Map an HTTP response to the shared error codes. Bot checks are reported, not bypassed."""
    status = response.status_code
    if status in (401, 403):
        raise AdapterError(ErrorCode.blocked, f"{provider} refused the request ({status})")
    if status == 429:
        raise AdapterError(ErrorCode.rate_limited, f"{provider} rate limit")
    if status == 404:
        raise AdapterError(ErrorCode.no_coverage, f"{provider} has no page for this location")
    if status >= 500:
        raise AdapterError(ErrorCode.unavailable, f"{provider} error {status}")
    if status >= 400:
        raise AdapterError(ErrorCode.invalid_request, f"{provider} rejected the request ({status})")
    if "html" in response.headers.get("content-type", "") and len(response.text) < 20_000:
        head = response.text[:5_000].lower()
        if any(marker in head for marker in CHALLENGE_MARKERS):
            raise AdapterError(ErrorCode.blocked, f"{provider} showed a bot check")
    return response


async def city_state(client: httpx.AsyncClient, loc: Location) -> tuple[str, str]:
    """City and state for a location, looking the ZIP up when the request doesn't carry them."""
    if loc.city and loc.state:
        return loc.city, loc.state.upper()
    response = await client.get(ZIP_URL.format(zip=loc.zip))
    if response.status_code != 200:
        raise AdapterError(ErrorCode.invalid_request, f"couldn't find the city for ZIP {loc.zip}")
    place = response.json()["places"][0]
    return place["place name"], place["state abbreviation"]


def text(fragment: str) -> str:
    """Visible text of an HTML fragment, whitespace collapsed."""
    fragment = re.sub(r"<!--.*?-->|<script.*?</script>|<style.*?</style>", " ", fragment, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def hidden_token(page: str) -> str:
    match = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page)
    if not match:
        raise AdapterError(ErrorCode.unavailable, "page layout changed: no request token")
    return match.group(1)
