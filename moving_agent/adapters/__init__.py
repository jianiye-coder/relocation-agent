"""Provider adapters behind one interface. See base.py for the contract and README for how to add one."""

from .base import (
    AdapterError, AdapterMetadata, AdapterResult, Capability, CarrierCheck, ErrorCode, MoveRequest,
    PriceKind, Quote, QuoteAdapter, ServiceType, SourceKind, VettingAdapter,
)
from .bridge import RegistrySource
from .budget_truck import BudgetTruckAdapter
from .fmcsa import FMCSAAdapter
from .public_storage import PublicStorageAdapter
from .registry import QuoteCache, Registry
from .request import request_from_intake
from .sample_catalog import SampleCatalogAdapter
from .uhaul import UHaulAdapter
from .vehicle import VehicleEstimateAdapter
from .warp import WarpLTLAdapter


def default_registry(cache: QuoteCache | None = None) -> Registry:
    # The website adapters are unofficial, so the Registry only runs them when ENABLE_UNOFFICIAL_ADAPTERS names them.
    return Registry([SampleCatalogAdapter(), VehicleEstimateAdapter(), WarpLTLAdapter(),
                     PublicStorageAdapter(), UHaulAdapter(), BudgetTruckAdapter()], cache=cache)
