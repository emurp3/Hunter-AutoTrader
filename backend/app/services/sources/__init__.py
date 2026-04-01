from app.services.sources.base import SourceAdapter, SourceHealth, SourceOpportunity
from app.services.sources.social_listener import SocialListenerAdapter
from app.services.sources.gig_scanner import GigScannerAdapter
from app.services.sources.marketplace_scanner import MarketplaceScannerAdapter
from app.services.sources.github_scanner import GitHubScannerAdapter
from app.services.sources.local_business_prospector import LocalBusinessProspectorAdapter
from app.services.sources.digital_product_scanner import DigitalProductGapAdapter
from app.services.sources.rfp_scanner import RfpScannerAdapter
from app.services.sources.affiliate_scanner import AffiliateScannerAdapter

__all__ = [
    "SourceAdapter",
    "SourceHealth",
    "SourceOpportunity",
    "SocialListenerAdapter",
    "GigScannerAdapter",
    "MarketplaceScannerAdapter",
    "GitHubScannerAdapter",
    "LocalBusinessProspectorAdapter",
    "DigitalProductGapAdapter",
    "RfpScannerAdapter",
    "AffiliateScannerAdapter",
]
