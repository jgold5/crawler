"""`crawler probe`: a read-only crawl that writes a redacted technical fingerprint of a site.

Output holds only shapes and counts (see schema.py); a fail-closed leak scan guards the bundle.
"""
from .core import BUNDLE_FILES, probe
from .safety import ProbeLeakError

__all__ = ["probe", "ProbeLeakError", "BUNDLE_FILES"]
