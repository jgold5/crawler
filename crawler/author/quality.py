"""Fragility flags of a locator (XPath or CSS), the same notions the engine and distill use.

A *fragile* locator still works today but breaks easily: it depends on exact text nodes (``text()``),
on classes (other than a Font Awesome icon identity), starts with ``//*``, picks by position, is absolute,
is very long, or uses a generated id. Engine candidates carry their strategy: ``positional``/``indexed``/
``grid-cell-index`` mean positional and ``class`` means class-dependent.
"""
from __future__ import annotations

import re

from ..config import SiteConfig

VERY_LONG = 200
FLAG_TEXT = "text-dependent"
FLAG_CLASS = "class-dependent"
FLAG_STAR = "starts with //*"
FLAG_POS = "positional"
FLAG_ABS = "absolute"
FLAG_LONG = "very long"
FLAG_ID = "generated id"

_LIT = re.compile(r"'[^']*'|\"[^\"]*\"")
# a Font Awesome icon identity is stable by construction: not a class dependency
# Font Awesome icon identity is stable vocabulary, not a fragile class: the full token form, and the short
# form the engine emits only for compound names no other class on the page extends.
_FA = re.compile(r"contains\(\s*concat\(\s*' '\s*,\s*normalize-space\(\s*@class\s*\)\s*,\s*' '\s*\)\s*,\s*' fa-[\w-]+ '\s*\)"
                 r"|contains\(\s*@class\s*,\s*'fa-[a-z0-9]+(?:-[a-z0-9]+)+'\s*\)")
_NEAR = re.compile(r"(?:following|preceding)(?:-sibling)?::[\w*-]+(?:\[[^\]]*\])?\[1\]")
_STRATEGY_FLAGS = {"positional": FLAG_POS, "indexed": FLAG_POS, "grid-cell-index": FLAG_POS, "class": FLAG_CLASS}


def _id_values(loc: str, css: bool) -> list[str]:
    if css:
        return re.findall(r"#([A-Za-z_][\w:-]*)", loc) + re.findall(r"\[id\s*=\s*['\"]([^'\"]+)['\"]\]", loc)
    return [m[1] or m[2] for m in re.finditer(r"@id\s*=\s*(?:'([^']*)'|\"([^\"]*)\")", loc)]


def _generated(value: str, cfg: SiteConfig | None) -> bool:
    cfg = cfg or SiteConfig()
    if any(re.search(p, value) for p in cfg.stable_id_allow or []):
        return False
    return any(re.search(p, value) for p in cfg.unstable_id_patterns or [])


def fragility(locator: str | None, kind: str = "xpath", cfg: SiteConfig | None = None,
              strategy: str | None = None, *, template: bool = False) -> list[str]:
    """Fragility flags of ``locator`` (empty list: not fragile). ``template``: a row template (no length flag)."""
    if not locator:
        return []
    loc = " ".join(locator.split())
    flags: list[str] = []
    if strategy in _STRATEGY_FLAGS:
        flags.append(_STRATEGY_FLAGS[strategy])
    css = kind == "css"
    if css:
        bare = _LIT.sub("''", loc)
        if re.search(r"(?<![\w\]'\"])\.[A-Za-z_-]", bare) or "[class" in bare:
            flags.append(FLAG_CLASS)
        if re.search(r":nth-|:first-child|:last-child", bare):
            flags.append(FLAG_POS)
        if re.match(r"\s*(html|body)\b", bare):
            flags.append(FLAG_ABS)
    else:
        bare = _LIT.sub("''", _FA.sub("true()", loc))
        if re.search(r"\btext\s*\(\s*\)", bare):
            flags.append(FLAG_TEXT)
        if "@class" in bare:
            flags.append(FLAG_CLASS)
        if re.match(r"\(*\s*//\*", bare):
            flags.append(FLAG_STAR)
        if re.search(r"\[\s*\d+\s*\]|position\s*\(\s*\)|last\s*\(\s*\)", _NEAR.sub("", bare)):
            flags.append(FLAG_POS)
        if re.match(r"\(*\s*/(?!/)", bare):
            flags.append(FLAG_ABS)
    if not template and len(loc) > VERY_LONG:
        flags.append(FLAG_LONG)
    if any(_generated(v, cfg) for v in _id_values(loc, css)):
        flags.append(FLAG_ID)
    return list(dict.fromkeys(flags))


__all__ = ["fragility", "VERY_LONG"]
