"""Turn raw per-page facts (in memory only) into the redacted profile and the suggested config."""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..config import SiteConfig
from . import classify as C
from .safety import is_safe_api_name, is_safe_attr_name, is_safe_regex, is_version
from .schema import SCHEMA_VERSION
from .vocab import (AG_THEMES, CLASS_SHAPES, FRAMEWORKS, ID_SHAPES, LINK_SKIPS, OTHER_GRIDS, ROW_MODELS,
                    UNSTABLE_CLASS_SHAPES, UNSTABLE_ID_SHAPES)


@dataclass
class PageFacts:
    """Everything learned about one page. Holds raw strings: never serialized."""
    index: int
    url: str
    raw: dict[str, Any]
    ready_ms: int
    ready_blocker: str
    public_shape: str
    reload: dict[str, Any] | None = None


@dataclass
class CrawlStats:
    links_seen: int = 0
    crawled: int = 0
    skipped: Counter = field(default_factory=Counter)
    page_limit_reached: bool = False
    elapsed_s: float = 0.0
    storage_state_used: bool = False


def pct(n: float, d: float) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _version(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lstrip("vV")
    return s if is_version(s) else None


def _theme(classes: list[str], styled_root: bool) -> str | None:
    """Legacy CSS theme class, or the Theming-API base theme (v33+: ag-theme-styleQuartz-7 etc.)."""
    legacy = [c for c in classes if c in AG_THEMES]
    if legacy:
        return legacy[0]
    for c in classes:
        m = re.fullmatch(r"ag-theme-style([A-Za-z]+)-\d+", c)
        if m and m.group(1).lower() in ("quartz", "alpine", "balham", "material"):
            return f"theming-api-{m.group(1).lower()}"
    if any(not re.fullmatch(r"ag-theme-[A-Za-z]+-\d+", c) for c in classes):
        return "<custom>"
    return "theming-api" if styled_root or classes else None


def build(pages: list[PageFacts], stats: CrawlStats, cfg: SiteConfig, *, max_pages: int,
          include_path_shapes: bool, denylist: list[str]) -> tuple[dict, dict]:
    deny = [d.lower() for d in denylist if d and d.strip()]
    raws = [p.raw for p in pages]

    # ---------------- frameworks
    fw: dict[str, dict] = {}
    for r in raws:
        for name, ver in (r.get("frameworks") or {}).items():
            if name not in FRAMEWORKS:
                continue
            e = fw.setdefault(name, {"pages": 0, "version": None})
            e["pages"] += 1
            e["version"] = e["version"] or _version(ver or None)

    # ---------------- AG Grid
    ag_version = None
    themes: set[str] = set()
    api_names: list[str] = []
    grids: list[dict] = []
    pages_with_grids = 0
    for p in pages:
        ag = p.raw.get("ag") or {}
        ag_version = ag_version or _version(ag.get("version"))
        apis = [a for a in ag.get("apis") or [] if not a.get("destroyed")]
        if ag.get("grids"):
            pages_with_grids += 1
        for a in apis:
            nm = a["name"] if is_safe_api_name(a["name"], deny) else "<custom>"
            if nm not in api_names:
                api_names.append(nm)
        for g in ag.get("grids") or []:
            api = next((a for a in apis if a.get("gridId") is not None and a.get("gridId") == g.get("gridIdAttr")), None)
            if api is None and len(apis) == 1 and len(ag.get("grids")) == 1:
                api = apis[0]
            theme = _theme(g.get("themes") or [], bool(g.get("styledRoot")))
            if theme:
                themes.add(theme)
            total = None
            if api and isinstance(api.get("displayed"), int):
                total = api["displayed"]
            elif g.get("ariaRowCount", -1) > 0:
                total = max(0, g["ariaRowCount"] - max(1, g.get("headerRows") or 1))
            col_ids = g.get("colIds") or []
            total_cols = api.get("columns") if api and api.get("columns") else (
                g["ariaColCount"] if g.get("ariaColCount", -1) > 0 else None)
            row_shape = C.classify_row_ids(g.get("rowIds") or [], total)
            rm = api.get("rowModel") if api else None
            api_expr = None
            if api and is_safe_api_name(api["name"], deny):
                api_expr = f"window.{api['name']}" + (".api" if api.get("sub") else "")
            grids.append({
                "page": p.index,
                "visible": bool(g.get("visible")),
                "theme": theme,
                "header_columns": len(col_ids),
                "group_headers": int(g.get("groupHeaders") or 0),
                "total_columns": total_cols,
                "pinned_left_cols": max(int(g.get("pinnedLeft") or 0), int(api.get("pinnedLeft") or 0) if api else 0),
                "pinned_right_cols": max(int(g.get("pinnedRight") or 0), int(api.get("pinnedRight") or 0) if api else 0),
                "rendered_rows": int(g.get("renderedRows") or 0),
                "total_rows": total,
                "row_virtualized": bool(total is not None and total > int(g.get("renderedRows") or 0)),
                "col_virtualized": (total_cols > len(col_ids)) if total_cols else None,
                "row_model": rm if rm in ROW_MODELS else "unknown",
                "dom_layout": g.get("layout") if g.get("layout") in ("normal", "autoHeight", "print") else "unknown",
                "row_id_shape": row_shape,
                "get_row_id": api.get("getRowId") if api and isinstance(api.get("getRowId"), bool) else None,
                "get_row_id_likely": row_shape not in ("none", "sequential-number"),
                "col_id_shapes": dict(Counter(C.classify_col_id(c) for c in col_ids)),
                "editable_columns": int(api["editable"]) if api and api.get("columns") else None,
                "editable_cell_hints": int(g.get("editableCellHints") or 0),
                "floating_filters": bool(g.get("floatingFilter")),
                "selection_checkboxes": bool(g.get("selectionCheckboxes")),
                "row_groups": bool(g.get("rowGroups")),
                "full_width_rows": bool(g.get("fullWidthRows")),
                "cell_links": int(g.get("cellLinks") or 0),
                "cell_buttons": int(g.get("cellButtons") or 0),
                "cell_inputs": int(g.get("cellInputs") or 0),
                "api_reachable": api is not None,
                "api_expr": api_expr,
            })
    ag_present = any((r.get("ag") or {}).get("present") for r in raws)

    other = Counter()
    for r in raws:
        for k, v in (r.get("otherGrids") or {}).items():
            if k in OTHER_GRIDS:
                other[k] += int(v or 0)

    # ---------------- test ids / data attributes
    interactive = sum(int(r.get("interactive") or 0) for r in raws)
    ikinds = Counter()
    for r in raws:
        ikinds.update({k: int(v) for k, v in (r.get("interactiveKinds") or {}).items()})
    agg: dict[str, dict] = {}
    for r in raws:
        for name, d in (r.get("data") or {}).items():
            a = agg.setdefault(name, {"pages": 0, "elements": 0, "interactive": 0, "values": []})
            a["pages"] += 1
            a["elements"] += int(d.get("elements") or 0)
            a["interactive"] += int(d.get("interactive") or 0)
            a["values"].extend(d.get("values") or [])
    attrs: dict[str, dict] = {}
    redacted = 0
    vue_scoped: dict | None = None
    suggested_test_ids: list[str] = []
    custom_n = 0
    for name in sorted(agg, key=lambda n: (-agg[n]["interactive"], -agg[n]["elements"], n)):
        a = agg[name]
        kind = C.classify_data_attr(name, a["values"], a["interactive"])
        if kind == "other":
            continue  # plain data-* noise is not reported
        vals = [v for v in a["values"] if v is not None]
        entry = {
            "kind": kind,
            "pages": a["pages"],
            "elements": a["elements"],
            "interactive": a["interactive"],
            "interactive_coverage_pct": pct(a["interactive"], interactive),
            "value_shapes": dict(Counter(C.classify_value(v) for v in vals if v != "")),
            "values_with_digits_pct": pct(sum(1 for v in vals if re.search(r"\d", v)), len(vals)),
            "unique_value_pct": pct(len(set(vals)), len(vals)),
        }
        if kind == "vue-scoped":
            if vue_scoped is None:
                vue_scoped = dict(entry, value_shapes={}, pages=0, elements=0, interactive=0)
            for k in ("pages", "elements", "interactive"):
                vue_scoped[k] += entry[k]
            vue_scoped["interactive_coverage_pct"] = pct(vue_scoped["interactive"], interactive)
            continue
        key = name.lower()
        if not (is_safe_attr_name(key, deny) and C.data_attr_name_is_generic(key, deny)):
            custom_n += 1
            redacted += 1
            key = f"data-<custom-{custom_n}>"
        elif kind in ("test-id", "identifier-like") and a["interactive"] and key not in cfg.test_id_attrs:
            suggested_test_ids.append(key)
        if len(attrs) < 150:
            attrs[key] = entry
    if vue_scoped:
        attrs["data-v-<hash>"] = vue_scoped

    def iw(field_name: str) -> float:
        return pct(sum(int(r.get(field_name) or 0) for r in raws), interactive)

    # ---------------- ids
    id_shapes = Counter()
    id_unstable_samples: list[str] = []
    stable_rejected = 0
    unstable_covered = 0
    elements = sum(int(r.get("elements") or 0) for r in raws)
    with_id = sum(int(r.get("withId") or 0) for r in raws)
    for r in raws:
        for i in r.get("ids") or []:
            s = C.classify_id(i)
            id_shapes[s] += 1
            cov = C.covered(i, cfg.unstable_id_patterns) and not C.covered(i, cfg.stable_id_allow)
            if s in UNSTABLE_ID_SHAPES:
                if cov:
                    unstable_covered += 1
                else:
                    id_unstable_samples.append(i)
            elif cov:
                stable_rejected += 1
    id_sugg = C.suggest_patterns(id_unstable_samples, cfg.unstable_id_patterns, deny)

    # ---------------- classes
    cls_shapes = Counter()
    cls_unstable_samples: list[str] = []
    cls_cov = 0
    distinct: set[str] = set()
    for r in raws:
        distinct.update((r.get("classes") or {}).keys())
    for c in distinct:
        s = C.classify_class(c)
        cls_shapes[s] += 1
        if s in UNSTABLE_CLASS_SHAPES:
            if C.covered(c, cfg.unstable_class_patterns):
                cls_cov += 1
            else:
                cls_unstable_samples.append(c)
    cls_sugg = C.suggest_patterns(cls_unstable_samples, cfg.unstable_class_patterns, deny)

    def patt(items):
        out = []
        for rx, n in items:
            if is_safe_regex(rx, deny):
                body = rx[1:-1]
                lit = bool(re.match(r"[a-z]", body))
                out.append({"pattern": rx, "matches": n, "literal_prefix": lit})
        return out

    # ---------------- a11y
    lab_tot = sum(int((r.get("labels") or {}).get("total") or 0) for r in raws)
    lab = Counter()
    btn = Counter()
    btn_tot = sum(int((r.get("buttons") or {}).get("total") or 0) for r in raws)
    for r in raws:
        lab.update({k: v for k, v in (r.get("labels") or {}).items() if k != "total"})
        btn.update({k: v for k, v in (r.get("buttons") or {}).items() if k != "total"})

    # ---------------- spinners
    sig_counts = Counter()
    word_counts = Counter()
    selectors: list[str] = []
    transient = 0
    for r in raws:
        for sig in r.get("transient") or []:
            transient += 1
            for kind, word in sig:
                if kind == "class-word":
                    sel = C.spinner_selector(kind, word)
                    if sel:
                        word_counts[word.lower()] += 1
                    else:
                        kind = "custom"
                sig_counts[kind] += 1
                sel = C.spinner_selector(kind, word)
                if sel and sel not in selectors:
                    selectors.append(sel)
    new_selectors = [s for s in selectors if s not in cfg.spinner_selectors]

    # ---------------- structure
    def tot(k: str) -> int:
        return sum(int(r.get(k) or 0) for r in raws)

    structure = {
        "elements_total": elements, "elements_in_shadow": tot("inShadow"), "shadow_roots_open": tot("shadowOpen"),
        "custom_elements": tot("customEls"), "iframes": tot("iframes"), "iframes_same_origin": tot("iframesSameOrigin"),
        "iframes_visible": tot("iframesVisible"), "framesets": tot("framesets"), "dialogs": tot("dialogs"),
        "dialogs_open": tot("dialogsOpen"), "dialogs_modal": tot("dialogsModal"), "forms": tot("forms"),
    }

    # ---------------- stability (reloaded pages)
    id_keep = {s: [0, 0] for s in ID_SHAPES}
    cls_keep = {s: [0, 0] for s in CLASS_SHAPES}
    stable_ids_changed = stable_cls_changed = 0
    reloaded = [p for p in pages if p.reload]
    for p in reloaded:
        second_ids = set(p.reload["ids"])
        for i in set(p.raw.get("ids") or []):
            s = C.classify_id(i)
            id_keep[s][1] += 1
            if i in second_ids:
                id_keep[s][0] += 1
            elif s not in UNSTABLE_ID_SHAPES:
                stable_ids_changed += 1
        second_cls = set(p.reload["classes"])
        for c in (p.raw.get("classes") or {}):
            s = C.classify_class(c)
            cls_keep[s][1] += 1
            if c in second_cls:
                cls_keep[s][0] += 1
            elif s not in UNSTABLE_CLASS_SHAPES and s not in ("state",):
                stable_cls_changed += 1

    # ---------------- pages
    page_rows = []
    for p in pages:
        r = p.raw
        row = {
            "index": p.index,
            "elements": int(r.get("elements") or 0),
            "interactive": int(r.get("interactive") or 0),
            "ag_grids": len((r.get("ag") or {}).get("grids") or []),
            "with_id_pct": pct(int(r.get("withId") or 0), int(r.get("elements") or 0)),
            "test_id_coverage_pct": pct(int(r.get("interactiveWithTestId") or 0), int(r.get("interactive") or 0)),
            "ready_ms": int(p.ready_ms),
            "ready_blocker": p.ready_blocker,
        }
        if include_path_shapes:
            row["path_shape"] = p.public_shape
        page_rows.append(row)

    ready_ms = [p.ready_ms for p in pages] or [0]
    profile: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "probe": {
            "pages_visited": len(pages),
            "max_pages": max_pages,
            "page_limit_reached": stats.page_limit_reached,
            "elapsed_s": round(stats.elapsed_s, 2),
            "storage_state_used": stats.storage_state_used,
            "pages_with_password_field": sum(1 for r in raws if r.get("passwordFields")),
            "start_page_has_password_field": bool(raws and raws[0].get("passwordFields")),
            "links": {"seen": stats.links_seen, "crawled": stats.crawled,
                      "skipped": {k: stats.skipped[k] for k in LINK_SKIPS if stats.skipped[k]}},
            "ready": {
                "median_ms": int(statistics.median(ready_ms)),
                "max_ms": int(max(ready_ms)),
                "timeouts": sum(1 for p in pages if p.ready_blocker != "none"),
                "blocked_by": dict(Counter(p.ready_blocker for p in pages if p.ready_blocker != "none")),
            },
        },
        "frameworks": fw,
        "ag_grid": {
            "present": bool(ag_present),
            "version": ag_version,
            "pages_with_grids": pages_with_grids,
            "grids_total": len(grids),
            "themes": sorted(themes),
            "api_names": api_names,
            "grids": grids,
        },
        "other_grids": {k: other[k] for k in OTHER_GRIDS if other[k]},
        "test_ids": {
            "interactive_elements": interactive,
            "interactive_by_kind": dict(ikinds),
            "interactive_with_any_test_id_pct": iw("interactiveWithTestId"),
            "interactive_with_configured_test_id_pct": iw("interactiveWithCfgTestId"),
            "redacted_attribute_names": redacted,
            "attributes": attrs,
        },
        "ids": {
            "elements": elements,
            "with_id": with_id,
            "with_id_pct": pct(with_id, elements),
            "shapes": {k: id_shapes[k] for k in ID_SHAPES if id_shapes[k]},
            "unstable_shaped": sum(id_shapes[k] for k in UNSTABLE_ID_SHAPES),
            "unstable_covered_by_config": unstable_covered,
            "unstable_not_covered": len(id_unstable_samples),
            "stable_shaped_but_rejected_by_config": stable_rejected,
            "suggested_patterns": patt(id_sugg),
        },
        "classes": {
            "distinct": len(distinct),
            "shapes": {k: cls_shapes[k] for k in CLASS_SHAPES if cls_shapes[k]},
            "unstable_shaped": sum(cls_shapes[k] for k in UNSTABLE_CLASS_SHAPES),
            "unstable_covered_by_config": cls_cov,
            "unstable_not_covered": len(cls_unstable_samples),
            "suggested_patterns": patt(cls_sugg),
        },
        "a11y": {
            "inputs": {"total": lab_tot, "pct": {k: pct(v, lab_tot) for k, v in sorted(lab.items())}},
            "buttons": {"total": btn_tot, "pct": {k: pct(v, btn_tot) for k, v in sorted(btn.items())}},
        },
        "spinners": {
            "transient_seen": transient,
            "signals": dict(sig_counts),
            "class_words": dict(word_counts),
            "still_visible_after_ready": tot("spinnersStill"),
            "suggested_selectors": selectors,
        },
        "structure": structure,
        "stability": {
            "pages_reloaded": len(reloaded),
            "id_persist_pct": {k: pct(a, b) for k, (a, b) in id_keep.items() if b},
            "class_persist_pct": {k: pct(a, b) for k, (a, b) in cls_keep.items() if b},
            "stable_shaped_ids_changed": stable_ids_changed,
            "stable_shaped_classes_changed": stable_cls_changed,
        },
        "pages": page_rows,
    }
    if include_path_shapes:
        profile["path_shapes"] = sorted({p.public_shape for p in pages})

    suggested: dict[str, Any] = {}
    if suggested_test_ids:
        suggested["test_id_attrs"] = suggested_test_ids
    ids_p = [x["pattern"] for x in profile["ids"]["suggested_patterns"]]
    if ids_p:
        suggested["unstable_id_patterns"] = ids_p
    cls_p = [x["pattern"] for x in profile["classes"]["suggested_patterns"]]
    if cls_p:
        suggested["unstable_class_patterns"] = cls_p
    if new_selectors:
        suggested["spinner_selectors"] = new_selectors
    g_out = {}
    seen_expr: set[str] = set()
    for g in grids:
        if g["api_expr"] and g["api_expr"] not in seen_expr:
            seen_expr.add(g["api_expr"])
            g_out[f"grid_{len(g_out) + 1}"] = {"kind": "ag-grid", "api_expr": g["api_expr"]}
    if g_out:
        suggested["grids"] = g_out
    return profile, suggested
