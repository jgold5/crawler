"""Render the validated profile as YAML and as a short human summary."""
from __future__ import annotations

from typing import Any

import yaml


class _Dumper(yaml.SafeDumper):
    pass


def _str(d: yaml.SafeDumper, s: str):
    return d.represent_scalar("tag:yaml.org,2002:str", s, style="'" if any(c in s for c in "\\^$[]{}*<>#:") else None)


_Dumper.add_representer(str, _str)


def to_yaml(data: dict, header: str) -> str:
    body = yaml.dump(data, Dumper=_Dumper, sort_keys=False, default_flow_style=False, allow_unicode=True, width=120)
    return "".join(f"# {line}\n" if line else "#\n" for line in header.splitlines()) + body


PROFILE_HEADER = """site-profile.yaml: redacted fingerprint written by `crawler probe`.
Contains only counts, booleans, enums, versions and generalized patterns (no text, values, names or URLs)."""

SUGGESTED_HEADER = """site.suggested.yaml: SiteConfig fragment suggested by `crawler probe`.
Merge into site.yaml. Pattern lists EXTEND the built-in defaults, so only additions are listed here.
grids: keys are placeholders; rename them and add `selector` / `key_column` yourself."""


def _yn(b: Any) -> str:
    return "yes" if b else "no"


def _top(d: dict, n: int = 6) -> str:
    items = sorted(d.items(), key=lambda kv: -kv[1])[:n]
    return ", ".join(f"{k} {v}" for k, v in items) if items else "none"


def to_markdown(p: dict, s: dict) -> str:
    pr, ag, ti, ids, cl, a11y, sp, st = (p["probe"], p["ag_grid"], p["test_ids"], p["ids"], p["classes"], p["a11y"],
                                         p["spinners"], p["structure"])
    L: list[str] = []
    add = L.append
    add("# Site profile (redacted)")
    add("")
    add(f"Probed {pr['pages_visited']} page shapes in {pr['elapsed_s']} s "
        f"(limit {pr['max_pages']}{', reached' if pr['page_limit_reached'] else ''}). "
        f"Links seen {pr['links']['seen']}, crawled {pr['links']['crawled']}, "
        f"skipped: {_top(pr['links']['skipped'], 9)}.")
    add(f"Ready wait: median {pr['ready']['median_ms']} ms, max {pr['ready']['max_ms']} ms, "
        f"timeouts {pr['ready']['timeouts']} ({_top(pr['ready']['blocked_by'])}).")
    if pr["start_page_has_password_field"]:
        add("")
        add("> Warning: the start page shows a password field. The session may be missing or expired; "
            "harvest auth again and re-run.")
    add("")
    add("## Frameworks")
    add("")
    if p["frameworks"]:
        for k, v in p["frameworks"].items():
            add(f"- {k}: version {v['version'] or 'unknown'} (pages {v['pages']})")
    else:
        add("- none detected")
    add("")
    add("## AG Grid")
    add("")
    add(f"- present: {_yn(ag['present'])}; version {ag['version'] or 'unknown'}; grids {ag['grids_total']} "
        f"on {ag['pages_with_grids']} page(s); themes {', '.join(ag['themes']) or 'none'}")
    add(f"- grid APIs reachable from window: {', '.join(ag['api_names']) or 'none'}")
    for i, g in enumerate(ag["grids"], 1):
        add(f"- grid {i} (page {g['page']}): {g['header_columns']} header cols of {g['total_columns'] or '?'} "
            f"(col virtualized {_yn(g['col_virtualized'])}), pinned L/R {g['pinned_left_cols']}/{g['pinned_right_cols']}, "
            f"rows rendered {g['rendered_rows']} of {g['total_rows'] if g['total_rows'] is not None else '?'} "
            f"(virtualized {_yn(g['row_virtualized'])}), row model {g['row_model']}, row-id shape {g['row_id_shape']}, "
            f"getRowId {('set' if g['get_row_id'] else 'not set') if g['get_row_id'] is not None else ('likely' if g['get_row_id_likely'] else 'unlikely')}, "
            f"editable cols {g['editable_columns'] if g['editable_columns'] is not None else '?'}, "
            f"api {g['api_expr'] or ('reachable' if g['api_reachable'] else 'not reachable')}")
    if p["other_grids"]:
        add(f"- other grids/tables: {_top(p['other_grids'], 12)}")
    add("")
    add("## Test ids")
    add("")
    add(f"- interactive elements {ti['interactive_elements']}; with any test-id-like attribute "
        f"{ti['interactive_with_any_test_id_pct']}%; with a configured test-id attribute "
        f"{ti['interactive_with_configured_test_id_pct']}%")
    for name, a in ti["attributes"].items():
        add(f"- `{name}` ({a['kind']}): {a['elements']} elements, interactive coverage {a['interactive_coverage_pct']}%, "
            f"values unique {a['unique_value_pct']}%, shapes {_top(a['value_shapes'], 4)}")
    if ti["redacted_attribute_names"]:
        add(f"- {ti['redacted_attribute_names']} attribute name(s) were not generic and are shown as `data-<custom-N>`.")
    add("")
    add("## Ids and classes")
    add("")
    add(f"- ids on {ids['with_id_pct']}% of elements; shapes: {_top(ids['shapes'], 9)}")
    add(f"- unstable-shaped ids {ids['unstable_shaped']}: covered by config {ids['unstable_covered_by_config']}, "
        f"not covered {ids['unstable_not_covered']}; stable-looking ids the config rejects "
        f"{ids['stable_shaped_but_rejected_by_config']}")
    for x in ids["suggested_patterns"]:
        add(f"  - suggest `{x['pattern']}` ({x['matches']} ids)")
    add(f"- classes: {cl['distinct']} distinct; shapes: {_top(cl['shapes'], 9)}")
    add(f"- unstable-shaped classes {cl['unstable_shaped']}: covered {cl['unstable_covered_by_config']}, "
        f"not covered {cl['unstable_not_covered']}")
    for x in cl["suggested_patterns"]:
        add(f"  - suggest `{x['pattern']}` ({x['matches']} classes)")
    stab = p["stability"]
    if stab["pages_reloaded"]:
        add(f"- reload check on {stab['pages_reloaded']} page(s): id persistence by shape "
            f"{_top(stab['id_persist_pct'], 9)}; stable-looking ids that changed {stab['stable_shaped_ids_changed']}, "
            f"stable-looking classes that changed {stab['stable_shaped_classes_changed']}")
    add("")
    add("## Labels and buttons")
    add("")
    add(f"- form fields {a11y['inputs']['total']}: {_top(a11y['inputs']['pct'], 7)} (percent)")
    add(f"- buttons {a11y['buttons']['total']}: {_top(a11y['buttons']['pct'], 6)} (percent)")
    add("")
    add("## Spinners")
    add("")
    add(f"- transient loading elements seen {sp['transient_seen']}; signals {_top(sp['signals'])}; "
        f"still visible after ready {sp['still_visible_after_ready']}")
    add(f"- selectors: {', '.join('`' + x + '`' for x in sp['suggested_selectors']) or 'none'}")
    add("")
    add("## Structure")
    add("")
    add(f"- iframes {st['iframes']} (same-origin {st['iframes_same_origin']}, visible {st['iframes_visible']}), "
        f"framesets {st['framesets']}, open shadow roots {st['shadow_roots_open']} "
        f"({st['elements_in_shadow']} elements inside), custom elements {st['custom_elements']}, "
        f"dialogs {st['dialogs']} (open {st['dialogs_open']}), forms {st['forms']}")
    add("")
    add("## Suggested config")
    add("")
    if s:
        for k, v in s.items():
            n = len(v)
            add(f"- `{k}`: {n} addition(s)")
    else:
        add("- no additions; the defaults already cover what was seen")
    add("")
    return "\n".join(L)
