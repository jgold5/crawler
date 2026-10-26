"""Inject the in-page engine (crawler/js/core.js) as ``window.__crawler``.

Two ways to get it into a page:

* ``ensure_engine(page, cfg)`` evaluates it on demand (idempotent, cheap when already present).
* ``context.add_init_script(engine_script(cfg))`` makes every new document start with it.

Both are safe to combine: the script re-configures an existing engine of the same version
instead of redefining it.

Every frame has its own ``window``: inject per frame (``ensure_engine(frame, cfg)``, or
``crawler.frames.ensure_engines(page, cfg)`` for all of them). An init script added to the context runs
in every frame, cross-origin ones included.
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
    """Idempotently make ``window.__crawler`` available in ``page``'s document.

    ``page`` may be a Page (its main frame) or a Frame (any iframe, same- or cross-origin).
    """
    if page.evaluate(_CHECK_JS, engine_key(cfg)):
        return
    page.evaluate(engine_script(cfg))
