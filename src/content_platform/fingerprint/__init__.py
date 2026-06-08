"""High-accuracy unified fingerprinting module.

Provides consistent fingerprinting algorithms for both device-side extraction
and server-side matching, ensuring compatible fingerprints across the system.
"""

from content_platform.fingerprint.unified import UnifiedFingerprinter

__all__ = ["UnifiedFingerprinter"]
