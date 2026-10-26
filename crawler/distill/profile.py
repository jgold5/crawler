"""Detected conventions (profile.yaml) and the human summary (PROFILE.md)."""
from __future__ import annotations

import re
from collections import Counter

import javalang

from .idents import split_words
from .jmodel import ClassInfo, Policy, Unit
from .skeleton import FINDBY_ANNOTATIONS, STEP_ANNOTATIONS
from .vocab import ROLE_WORDS, TEST_ID_ATTRS, VERBS

T = javalang.tree
SUFFIXES = ["PageObject", "Page", "Screen", "View", "PO", "Section", "Component", "Dialog", "Modal", "Panel", "Form"]
STEP_SUFFIXES = ["StepDefinitions", "StepDefs", "Stepdefs", "Steps", "Glue", "Definitions"]
WAIT_WORDS = {"wait", "await", "until", "visible", "clickable", "ready", "loaded", "spinner", "loader", "present",
              "stale", "invisible", "disappear"}
GRID_WORDS = {"grid", "cell", "cells", "row", "rows", "column", "columns"}


def _is_upper(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", name))


def _locator_members(ci: ClassInfo) -> tuple[int, int, int, list[str], list[str]]:
    findby = by_fields = by_methods = 0
    fb_names, by_names = [], []
    for f in ci.fields.values():
        anns = {a.name.rsplit(".", 1)[-1] for a in f.annotations}
        if anns & FINDBY_ANNOTATIONS:
            findby += 1
            fb_names.append(f.name)
        elif f.type_name in ("By", "org.openqa.selenium.By"):
            by_fields += 1
            by_names.append(f.name)
    for m in ci.methods:
        if m.return_type in ("By", "org.openqa.selenium.By"):
            by_methods += 1
    return findby, by_fields, by_methods, fb_names, by_names


def _has_steps(ci: ClassInfo) -> bool:
    return any(a.name.rsplit(".", 1)[-1] in STEP_ANNOTATIONS for m in ci.methods for a in m.annotations)


def _resolve(simple: str, ci: ClassInfo, by_simple: dict[str, list[ClassInfo]]) -> str:
    simple = simple.split("<")[0]
    if "." in simple and simple.split(".")[0][:1].islower():
        return simple
    fq = ci.scope.explicit.get(simple)
    if fq:
        return fq
    cands = by_simple.get(simple, [])
    same = [c for c in cands if c.package == ci.package]
    if same or cands:
        return (same or cands)[0].fqcn
    return f"{ci.package}.{simple}" if ci.package else simple


def conventions(units: list[Unit], policy: Policy) -> tuple[dict, dict, dict]:
    """-> (java, naming, facts) — java uses exactly JavaDef's keys."""
    classes = [c for u in units for c in u.classes]
    by_simple: dict[str, list[ClassInfo]] = {}
    for c in classes:
        by_simple.setdefault(c.name, []).append(c)
    supers = {c.extends.split("<")[0].split(".")[-1] for c in classes if c.extends}
    pages = []
    for c in classes:
        if c.kind != "class" or _has_steps(c):
            continue
        fb, bf, bm, _, _ = _locator_members(c)
        named = any(c.name.endswith(s) for s in SUFFIXES)
        if (fb + bf + bm) > 0 or (named and not c.abstract and c.name not in supers):
            pages.append(c)
    concrete = [c for c in pages if not c.abstract and c.name not in supers] or pages
    suffix = Counter()
    for c in concrete:
        for s in SUFFIXES:
            if c.name.endswith(s) and len(c.name) > len(s):
                suffix[s] += 1
                break
    class_suffix = sorted(suffix.items(), key=lambda kv: (-kv[1], SUFFIXES.index(kv[0])))[0][0] if suffix else "Page"
    fb = bf = bm = 0
    fb_names, by_names = [], []
    for c in pages:
        a, b, m, n1, n2 = _locator_members(c)
        fb, bf, bm = fb + a, bf + b, bm + m
        fb_names += n1
        by_names += n2
    if fb >= bf and fb >= bm and fb > 0:
        style, names = "findby_fields", fb_names
    elif bf >= bm and bf > 0:
        style, names = "by_constants", by_names
    elif bm > 0:
        style, names = "by_methods", []
    else:
        style, names = "by_constants", []
    upper = sum(1 for n in names if _is_upper(n))
    constant_case = "UPPER_SNAKE" if names and upper * 2 >= len(names) else "camel"
    bases = Counter(_resolve(c.extends, c, by_simple) for c in concrete if c.extends)
    base_class = None
    if bases:
        fq = sorted(bases.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        base_class = policy.qualified(fq)
    ctor = Counter()
    for c in concrete:
        for m in c.methods:
            if m.is_ctor and "private" not in (m.node.modifiers or set()):
                ctor[", ".join(f"{policy.type_name(t.split('<')[0], c.scope)} {policy.local(n)}"
                               for t, n in m.params)] += 1
    constructor_arg = sorted(ctor.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if ctor else None
    pk = Counter(c.package for c in concrete)
    package = policy.pkg(sorted(pk.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]) if pk else policy.rename_to
    imp = Counter()
    unit_of = {id(c): u for u in units for c in u.classes}
    for c in concrete:
        u = unit_of.get(id(c))
        if u is None or u.cu is None:
            continue
        seen = set()
        for i in u.cu.imports or []:
            if i.static:
                continue
            seen.add(policy.qualified(i.path + (".*" if i.wildcard else "")))
        for s in seen:
            imp[s] += 1
    need = max(1, (len(concrete) + 1) // 2)
    imports = sorted(s for s, n in imp.items() if n >= need and s != base_class)
    java = {"package": package, "class_suffix": class_suffix, "locator_style": style,
            "constant_case": constant_case, "base_class": base_class, "constructor_arg": constructor_arg or None,
            "imports": imports, "helper_package": None}

    # naming
    fcase = Counter("UPPER_SNAKE" if _is_upper(n) else "camel" if re.fullmatch(r"[a-z][A-Za-z0-9]*", n) else "other"
                    for n in fb_names + by_names)
    roles = Counter()
    for n in fb_names + by_names:
        w = split_words(n)
        if w and w[-1].lower() in ROLE_WORDS:
            roles[w[-1].capitalize()] += 1
        else:
            roles["<other>"] += 1
    verbs = Counter()
    fluent = total_methods = 0
    for c in concrete:
        for m in c.methods:
            if m.is_ctor or "public" not in (m.node.modifiers or set()):
                continue
            total_methods += 1
            w = split_words(m.name)
            verbs[w[0].lower() if w and w[0].lower() in VERBS else "<other>"] += 1
            if m.return_type and m.return_type.split("<")[0] in by_simple:
                fluent += 1
    step_classes = [c for c in classes if _has_steps(c)]
    ssuf = Counter()
    for c in step_classes:
        ssuf[next((s for s in STEP_SUFFIXES if c.name.endswith(s)), "<other>")] += 1
    naming = {
        "locator_field_case": dict(fcase.most_common()),
        "locator_field_roles": dict(roles.most_common(15)),
        "page_method_verbs": dict(verbs.most_common(15)),
        "page_methods_returning_page_objects": f"{fluent}/{total_methods}",
        "step_class_suffix": dict(ssuf.most_common()),
    }
    facts = {"page_classes": len(concrete), "base_classes": len(supers & {c.name for c in classes}),
             "step_classes": len(step_classes), "classes": len(classes)}
    return java, naming, facts


def wait_and_grid_stats(units: list[Unit], policy: Policy, locs: list[dict], raw_ag_refs: int,
                        js_calls: Counter | None = None, js_objects: set | None = None) -> tuple[dict, dict]:
    ec = Counter()
    explicit = sleeps = implicit = 0
    wait_helpers, grid_helpers = set(), set()
    def words(n):
        return {w.lower() for w in split_words(n)}
    for u in units:
        for ci in u.classes:
            if _has_steps(ci):
                continue
            if words(ci.name) & GRID_WORDS:
                grid_helpers.add(policy.type_name(ci.name, ci.scope))
            for m in ci.methods:
                if not m.is_ctor and words(m.name) & WAIT_WORDS:
                    wait_helpers.add(policy.member(m.name, "method"))
                if not m.is_ctor and words(m.name) & GRID_WORDS:
                    grid_helpers.add(policy.member(m.name, "method"))
        if u.cu is None or u.mode != "ast":
            continue
        for _, n in u.cu:
            if isinstance(n, T.MethodInvocation):
                if n.qualifier == "ExpectedConditions":
                    ec[n.member] += 1
                elif n.member == "sleep" and n.qualifier in ("Thread", "TimeUnit.SECONDS", "TimeUnit.MILLISECONDS"):
                    sleeps += 1
                elif n.member == "implicitlyWait":
                    implicit += 1
            elif isinstance(n, T.ClassCreator) and n.type is not None and n.type.name in ("WebDriverWait",
                                                                                         "FluentWait"):
                explicit += 1
    waits = {"helpers": sorted(wait_helpers), "expected_conditions": dict(ec.most_common()),
             "explicit_wait_objects": explicit, "thread_sleep_calls": sleeps, "implicit_wait_calls": implicit}

    def count(pat):
        return sum(i["count"] for i in locs if re.search(pat, i["shape"]))
    text_row = any("col-id" in i["shape"] and re.search(r"text\(\)|normalize-space|\.\s*=", i["shape"])
                   for i in locs if i["kind"] == "xpath")
    helper_classes = {c.fqcn for u in units for c in u.classes
                      if not _has_steps(c) and {w.lower() for w in split_words(c.name)} & GRID_WORDS}
    grid = {"ag_grid": bool(count(r"col-id") or raw_ag_refs or js_calls),
            "col_id_locators": count(r"col-id"), "row_id_locators": count(r"row-id"),
            "row_index_locators": count(r"row-index|aria-rowindex"),
            "role_grid_locators": count(r"role='(?:grid|row|gridcell|columnheader|treegrid)'"),
            "ag_class_literals": raw_ag_refs,
            "grid_helper_classes": len(helper_classes),
            "grid_helpers": sorted(grid_helpers),
            "uses_text_for_row_match": bool(text_row),
            "js_api_calls": dict(sorted((js_calls or Counter()).items(), key=lambda kv: (-kv[1], kv[0]))),
            "js_api_objects": sorted(js_objects or set())}
    return waits, grid


def test_id_attrs(summary: dict) -> dict:
    return {k: v for k, v in summary.get("attribute_names", {}).items()
            if k in TEST_ID_ATTRS or k.startswith("data-")}


def render_md(p: dict) -> str:
    j = p["java"]
    L: list[str] = []
    a = L.append
    a("# Framework profile")
    a("")
    a("Generated offline by `crawler distill`. Regenerated from parse trees; no source text was copied.")
    a("")
    a("## Before you share this bundle")
    a("")
    a("1. Run `crawler distill --check <this folder> --deny \"<your terms>\"` and get `clean`.")
    a("2. Skim `skeleton/`: class, method and field names were anonymized"
      + (" (except: --keep-identifiers was used, so names are as written; read them all)."
         if p["keep_identifiers"] else "; check that no business words slipped through."))
    a("3. Read the step phrases listed under *Review these step phrases* below, and `steps.yaml`.")
    a("4. Open `features/` and `step-mappings.draft.yaml`: unquoted words in step text are kept.")
    a("5. Search the folder for your company, product, customer and project names one more time.")
    if p.get("pages_md"):
        a("6. Skim `pages/` and `pages-schema.yaml`: only generic schema words and placeholders should remain.")
    a("")
    a("## Overview")
    a("")
    f = p["files"]
    a(f"- Java files: {f['java_parsed']} parsed, {f['java_stub_fallback']} via Java 17 stub fallback, "
      f"{f['java_token_fallback']} signatures-only fallback, {f['java_skipped']} skipped (parse error)")
    a(f"- Feature files: {f['features_parsed']} parsed, {f['features_skipped']} skipped (parse error)")
    a(f"- Not read (counted only): {f['not_read_total']} files "
      + ("(" + ", ".join(f"{k}: {v}" for k, v in f["not_read"].items()) + ")" if f["not_read"] else ""))
    a(f"- Classes: {p['facts']['classes']}, page objects: {p['facts']['page_classes']}, "
      f"step-definition classes: {p['facts']['step_classes']}")
    a("")
    a("## Conventions (profile.yaml `java:`)")
    a("")
    a("| key | value |")
    a("|---|---|")
    for k in ("package", "class_suffix", "locator_style", "constant_case", "base_class", "constructor_arg"):
        a(f"| {k} | `{j[k]}` |")
    a(f"| imports | {', '.join('`' + i + '`' for i in j['imports']) or '-'} |")
    a("")
    s = p["locator_summary"]
    a("## Locators (locators.yaml)")
    a("")
    a(f"- {s['total']} locators, {s['unique_shapes']} distinct shapes; length median {s['length_median']}, "
      f"p90 {s['length_p90']}")
    a("- By kind: " + (", ".join(f"{k} {v}" for k, v in s["by_kind"].items()) or "none"))
    a("- Flagged: " + (", ".join(f"{k} {v}%" for k, v in s["flagged_pct"].items() if v) or "none"))
    a("- Attribute names: " + (", ".join(f"{k} {v}" for k, v in s["attribute_names"].items()) or "none"))
    a("")
    a("Most common shapes:")
    a("")
    for t in s["top_shapes"]:
        a(f"- {t['count']} x {t['kind']}: `{t['shape']}`")
    a("")
    w, g = p["waits"], p["grid"]
    a("## Waits and grids")
    a("")
    a(f"- Wait helpers: {', '.join(w['helpers']) or 'none'}")
    a(f"- ExpectedConditions: {', '.join(f'{k} {v}' for k, v in w['expected_conditions'].items()) or 'none'}")
    a(f"- Explicit wait objects: {w['explicit_wait_objects']}, Thread.sleep calls: {w['thread_sleep_calls']}, "
      f"implicit waits: {w['implicit_wait_calls']}")
    a(f"- AG Grid: {g['ag_grid']} (col-id locators {g['col_id_locators']}, row-id {g['row_id_locators']}, "
      f"row-index {g['row_index_locators']}, text used to match rows: {g['uses_text_for_row_match']})")
    a(f"- Grid helper classes: {g['grid_helper_classes']}; helpers: {', '.join(g['grid_helpers']) or 'none'}")
    a(f"- Grid JS API calls: {', '.join(f'{k} {v}' for k, v in g['js_api_calls'].items()) or 'none'}"
      f" (via {', '.join(g['js_api_objects']) or '-'})")
    a("")
    c = p["cucumber"]
    a("## Cucumber (steps.yaml, step-mappings.draft.yaml)")
    a("")
    a(f"- {c['step_definitions']} step definitions: " + ", ".join(f"{k} {v}" for k, v in c["expressions"].items()))
    a("- Inferred actions: " + ", ".join(f"{k} {v}" for k, v in c["inferred_actions"].items()))
    a(f"- Draft mappings: {c['draft_mappings']} confident, {c['draft_todo']} left as TODO")
    a(f"- Unused step definitions (no matching feature step): {c['unused']}")
    a("")
    if p.get("pages_md"):
        L.extend(p["pages_md"])
    a("## What was removed")
    a("")
    a("Counts only; the removed values are not stored anywhere.")
    a("")
    for k, v in sorted(p["removed"].items()):
        if v:
            a(f"- {k}: {v}")
    a("")
    a("## Review these step phrases before sharing")
    a("")
    rev = p["review"]
    if not rev:
        a("None: every kept word is in the common-word list.")
    else:
        a(f"{len(rev)} phrase(s) keep words outside the common-word list (capitalized words at the start of a "
          "sentence are kept). Delete or edit any that reveal business details:")
        a("")
        for r in rev[:60]:
            a(f"- `{r}`")
        if len(rev) > 60:
            a(f"- ... and {len(rev) - 60} more in steps.yaml")
    a("")
    return "\n".join(L)
