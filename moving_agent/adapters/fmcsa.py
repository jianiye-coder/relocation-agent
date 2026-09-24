"""Mover vetting through FMCSA's public QCMobile API (free web key from mobile.fmcsa.dot.gov/QCDevsite)."""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx

from .base import (
    AdapterError, AdapterMetadata, Capability, CarrierCheck, ErrorCode, RateLimit, SourceKind, VettingAdapter, now,
)

BASE_URL = "https://mobile.fmcsa.dot.gov/qc/services"


def _carriers(payload: dict) -> list[dict]:
    """Normalize single-record and name-search QCMobile response shapes."""
    content = payload.get("content")
    if isinstance(content, list):
        return [item.get("carrier", item) for item in content if isinstance(item, dict)]
    if not isinstance(content, dict):
        return []
    carrier = content.get("carrier", content)
    return [carrier] if isinstance(carrier, dict) else []


def _carrier(payload: dict) -> dict | None:
    return next(iter(_carriers(payload)), None)


def _allowed(carrier: dict) -> bool | None:
    # Docs name it allowToOperate; some responses use allowedToOperate. Accept both.
    flag = carrier.get("allowToOperate", carrier.get("allowedToOperate"))
    return None if flag is None else str(flag).upper() == "Y"


class FMCSAAdapter(VettingAdapter):
    metadata = AdapterMetadata(
        id="fmcsa_qcmobile",
        name="FMCSA QCMobile (carrier registration)",
        capabilities=[Capability.vetting],
        auth="api_key",
        auth_env=["FMCSA_WEB_KEY"],
        rate_limit=RateLimit(requests=5, per_seconds=1),
        source_kind=SourceKind.official_api,
        cache_ttl_seconds=24 * 3600,
        docs_url="https://mobile.fmcsa.dot.gov/QCDevsite/docs/qcApi",
    )

    def __init__(self, client: httpx.AsyncClient | None = None, base_url: str = BASE_URL):
        self.client = client
        self.base_url = base_url

    async def _get(self, path: str) -> httpx.Response:
        key = os.getenv("FMCSA_WEB_KEY")
        if not key:
            raise AdapterError(ErrorCode.auth_missing, "set FMCSA_WEB_KEY")
        client = self.client or httpx.AsyncClient(timeout=15)
        try:
            response = await client.get(self.base_url + path, params={"webKey": key})
        finally:
            if self.client is None:
                await client.aclose()
        if response.status_code in (401, 403):
            raise AdapterError(ErrorCode.blocked, f"FMCSA refused the key ({response.status_code})")
        if response.status_code == 429:
            raise AdapterError(ErrorCode.rate_limited, "FMCSA rate limit")
        if response.status_code >= 500:
            raise AdapterError(ErrorCode.unavailable, f"FMCSA error {response.status_code}")
        return response

    @staticmethod
    def _to_check(carrier: dict, query: str, source: str) -> CarrierCheck:
        allowed = _allowed(carrier)
        notes = [] if allowed else ["Not allowed to operate according to FMCSA. Do not book."]
        return CarrierCheck(
            adapter_id=FMCSAAdapter.metadata.id, query=query, found=True,
            usdot_number=carrier.get("dotNumber"), mc_number=carrier.get("mcNumber"),
            legal_name=carrier.get("legalName", ""), dba_name=carrier.get("dbaName") or "",
            allowed_to_operate=allowed, city=carrier.get("phyCity", ""), state=carrier.get("phyState", ""),
            source=source, fetched_at=now(), notes=notes,
        )

    async def check(self, usdot: int | None = None, mc: str | None = None) -> CarrierCheck:
        if usdot:
            path, query = f"/carriers/{usdot}", f"USDOT {usdot}"
        elif mc:
            digits = "".join(ch for ch in mc if ch.isdigit())
            path, query = f"/carriers/docket-number/{digits}", f"MC {digits}"
        else:
            raise AdapterError(ErrorCode.invalid_request, "give a USDOT or MC number")
        r = await self._get(path)
        carrier = _carrier(r.json()) if r.status_code == 200 else None
        source = f"FMCSA QCMobile {path}"
        if not carrier:
            return CarrierCheck(adapter_id=self.metadata.id, query=query, found=False, source=source, fetched_at=now(),
                                notes=["No FMCSA record. Interstate household-goods movers must be registered."])
        return self._to_check(carrier, query, source)

    async def search(self, name: str) -> list[CarrierCheck]:
        """Find up to FMCSA's first page of carriers by legal or DBA name."""
        name = name.strip()
        if not name:
            raise AdapterError(ErrorCode.invalid_request, "give a carrier name")
        path = f"/carriers/name/{quote(name, safe='')}"
        response = await self._get(path)
        source = f"FMCSA QCMobile {path}"
        return [self._to_check(carrier, f"name {name}", source) for carrier in _carriers(response.json())]
