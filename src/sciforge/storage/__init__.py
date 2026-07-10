"""Storage backend protocol and implementations."""

from sciforge.storage.protocol import StorageBackend
from sciforge.storage.query import QualityGateFilter

__all__ = ["QualityGateFilter", "StorageBackend"]
