"""Provider adapters behind one interface. See base.py for the contract and README for how to add one."""

from .base import (
    AdapterError, AdapterMetadata, AdapterResult, Capability, CarrierCheck, ErrorCode, MoveRequest,
    PriceKind, Quote, QuoteAdapter, ServiceType, SourceKind, VettingAdapter,
)
from .bridge import RegistrySource
from .fmcsa import FMCSAAdapter
from .registry import QuoteCache, Registry
from .request import request_from_intake
from .sample_catalog import SampleCatalogAdapter
from .vehicle import VehicleEstimateAdapter
from .warp import WarpLTLAdapter


def default_registry(cache: QuoteCache | None = None) -> Registry:
    return Registry([SampleCatalogAdapter(), VehicleEstimateAdapter(), WarpLTLAdapter()], cache=cache)
