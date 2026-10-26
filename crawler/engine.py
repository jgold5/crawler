"""Inject the in-page engine (crawler/js/core.js) as ``window.__crawler``.

Two ways to get it into a page:

* ``ensure_engine(page, cfg)`` evaluates it on demand (idempotent, cheap when already present).
* ``context.add_init_script(engine_script(cfg))`` makes every new document start with it.

Both are safe to combine: the script re-configures an existing engine of the same version
instead of redefining it.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .config import SiteConfig

CORE_JS = Path(__file__).parent / "js" / "core.js"
_BOOT_PLACEHOLDER = "/*__CRAWLER_BOOT__*/null"

_CHECK_JS = "k => !!(window.__crawler && window.__crawler.key === k)"


@lru_cache(maxsize=1)
def _core() -> tuple[str, str]:
    src = CORE_JS.read_text(encoding="utf-8")
    if _BOOT_PLACEHOLDER not in src:
        raise RuntimeError(f"{CORE_JS} lost its boot placeholder {_BOOT_PLACEHOLDER!r}")
    return src, hashlib.sha1(src.encode()).hexdigest()[:12]


def _boot(cfg: SiteConfig) -> dict:
    src, version = _core()
    js_cfg = cfg.js_config()
    key = hashlib.sha1((version + json.dumps(js_cfg, sort_keys=True)).encode()).hexdigest()[:16]
    return {"cfg": js_cfg, "key": key, "version": version}


def engine_key(cfg: SiteConfig) -> str:
    """Identifies (engine version, config). ``window.__crawler.key`` equals it once injected."""
    return _boot(cfg)["key"]


def engine_script(cfg: SiteConfig) -> str:
    """The full engine source configured for ``cfg`` (an expression evaluating to ``true``)."""
    src, _ = _core()
    return src.replace(_BOOT_PLACEHOLDER, json.dumps(_boot(cfg)))


def ensure_engine(page, cfg: SiteConfig) -> None:
    """Idempotently make ``window.__crawler`` available in the page's main frame.

    ``page`` may also be a Frame (anything with ``evaluate``).
    """
    if page.evaluate(_CHECK_JS, engine_key(cfg)):
        return
    page.evaluate(engine_script(cfg))
