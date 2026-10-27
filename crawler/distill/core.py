"""distill(): framework repo -> redacted framework-profile bundle. Offline, deterministic, fail-closed."""
from __future__ import annotations

import os
import re
import shutil
import warnings
from collections import Counter
from pathlib import Path

import javalang
import yaml

from .check import ALLOWED_TOP, check
from .features import FeatureWriter, parse_feature, raw_step_texts
from .idents import Anonymizer
from .javasrc import comment_texts, read_source, scan, string_values, stub_source
from .jmodel import Policy, Scope, Unit, common_prefix, index_unit, make_scope
from .locators import LocatorCollector
from . import pages as pages_mod
from .profile import conventions, render_md, test_id_attrs, wait_and_grid_stats
from .redact import (EMAIL_RX, URL_RX, PhraseRedactor, Renamer, TagRedactor, hostlike_in, hosts_tld_in,
                     registrable_label, term_words)
from .skeleton import Renderer, render_tokens
from .stepdefs import Catalog, Inference, collect, count_usage, mapping_yaml
from . import vocab as V
from .vocab import (AG_GRID_API, GENERIC_STEMS, HOST_GENERIC, PACKAGE_GENERIC, SAFE_IMPORT_PREFIXES, SAFE_WORDS,
                    TAG_WORDS)

SKIP_DIRS = {"target", "build", "out", "bin", "node_modules", "dist", "__pycache__", "venv", "logs",
             "test-output", "allure-results", "reports"}
CONFIG_EXT = {".properties", ".env", ".yaml", ".yml", ".json", ".xml", ".conf", ".ini", ".toml", ".cfg"}
CRED_EXT = {".pem", ".key", ".p12", ".pfx", ".jks", ".crt", ".cer", ".keystore", ".der"}
PUBLIC_LABELS = {"google", "github", "githubusercontent", "selenium", "seleniumhq", "w3", "w3schools", "apache",
                 "oracle", "cucumber", "junit", "mozilla", "microsoft", "maven", "example", "localhost",
                 "stackoverflow", "baeldung", "openqa", "testng", "java", "jenkins", "gitlab", "atlassian",
                 "amazonaws", "amazon", "gstatic", "googleapis", "npmjs", "ag-grid", "aggrid", "chromium",
                 "mvnrepository", "sonatype", "apple", "android", "youtube", "wikipedia", "json", "yaml"}


class DistillLeakError(RuntimeError):
    def __init__(self, problems: list[str], details: list[str] | None = None):
        self.problems = problems
        self.details = details or []  # with values; for the local terminal only
        head = "\n  ".join(problems[:20])
        more = f"\n  ... {len(problems) - 20} more" if len(problems) > 20 else ""
        super().__init__(f"distill check failed; no bundle was written. Problems:\n  {head}{more}")


def _category(p: Path) -> str:
    name = p.name.lower()
    if p.suffix.lower() in CRED_EXT or any(w in name for w in ("credential", "secret", "token", "passwd")):
        return "credentials"
    if p.suffix.lower() in CONFIG_EXT or name.startswith(".env"):
        return "config"
    return "other"


def _walk(src: Path, exclude: set[Path], consumed: set[Path] = frozenset()) -> tuple[list[Path], list[Path], Counter]:
    java, feats, other = [], [], Counter()
    for root, dirs, files in os.walk(src, followlinks=False):
        rp = Path(root)
        keep = []
        for d in sorted(dirs):
            full = rp / d
            if d.startswith(".") or d.lower() in SKIP_DIRS or full.resolve() in exclude or full.is_symlink():
                other["skipped directories"] += 1
                continue
            keep.append(d)
        dirs[:] = keep
        for f in sorted(files):
            p = rp / f
            if p.is_symlink():
                other["symlinks"] += 1
                continue
            if consumed and p.resolve() in consumed:
                continue  # selected page-definition file: read by the pages adapter
            if f in ("module-info.java", "package-info.java"):
                other["java module/package info"] += 1
            elif f.endswith(".java"):
                java.append(p)
            elif f.endswith(".feature"):
                feats.append(p)
            else:
                other[_category(p)] += 1
    return java, feats, other


_CORPUS: frozenset[str] | None = None


def _corpus() -> frozenset[str]:
    """Words the generator itself may write (vocabularies + fixed text in the writers). An auto-detected denylist
    term that is a substring of one of these would make check() fail on the bundle's own boilerplate."""
    global _CORPUS
    if _CORPUS is None:
        import io
        import tokenize
        words: set[str] = set()
        for name in dir(V):
            val = getattr(V, name)
            if isinstance(val, frozenset):
                for w in val:
                    words.update(x.lower() for x in re.findall(r"[A-Za-z]+", w))
        here = Path(__file__).parent
        skip = {tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.NL, tokenize.ENCODING}
        for mod in ("core", "profile", "pages", "stepdefs", "locators", "features", "skeleton", "check"):
            try:
                src = (here / f"{mod}.py").read_text(encoding="utf-8")
                prev = tokenize.NEWLINE
                for t in tokenize.generate_tokens(io.StringIO(src).readline):
                    kind = tokenize.tok_name.get(t.type, "")
                    if kind in ("STRING", "FSTRING_MIDDLE") and not (kind == "STRING" and prev in skip):
                        words.update(x.lower() for x in re.findall(r"[A-Za-z]+", t.string))  # not docstrings
                    if t.type not in (tokenize.COMMENT, tokenize.NL):
                        prev = t.type
            except (OSError, tokenize.TokenError, SyntaxError):
                continue
        _CORPUS = frozenset(words)
    return _CORPUS


def _host_labels(host: str) -> set[str]:
    """Every non-TLD label of a host, plus the hyphen parts (>= 5 chars) of each label: uat-brandx.corpy-internal.se
    -> uat-brandx, corpy-internal, brandx, corpy (generic infrastructure words are filtered later)."""
    labels = [x for x in host.lower().split(".") if x]
    if len(labels) < 2:
        return set()
    out = set()
    lab = registrable_label(host)
    if lab:
        out.add(lab)
    for x in labels[:-1]:
        out.add(x)
        out.update(p for p in x.split("-") if len(p) >= 5)
    return out


def _auto_terms(texts: list[str], prefix: str, host_texts: list[str] = ()) -> list[str]:
    """Denylist terms detected from the sources: host / email labels and the first-party package prefix.

    `texts` (whole files) are scanned for URLs, emails and well-known-TLD hosts; `host_texts` (string literals,
    comments, feature and page-file text: never Java code, where `driver.get` looks like a host) also for
    TLD-agnostic host names (portal.<co>bank.bank, uat-x.<co>-internal.se)."""
    labels = set()
    for t in texts:
        for m in URL_RX.finditer(t):
            host = re.sub(r"^[a-zA-Z][\w+.-]*://", "", m.group(0)).split("/")[0].split(":")[0].split("@")[-1]
            labels |= _host_labels(host)
        for m in EMAIL_RX.finditer(t):
            labels |= _host_labels(m.group(0).split("@", 1)[1])
        for h in hosts_tld_in(t):
            labels |= _host_labels(h)
    for t in host_texts:
        for h in hostlike_in(t):
            labels |= _host_labels(h)
    for seg in prefix.split("."):
        if seg and seg.lower() not in PACKAGE_GENERIC:
            labels.add(seg.lower())
    safe = " ".join(SAFE_IMPORT_PREFIXES).lower()
    generic = PUBLIC_LABELS | SAFE_WORDS | GENERIC_STEMS | HOST_GENERIC | TAG_WORDS | PACKAGE_GENERIC
    corpus = _corpus()
    out = []
    for lab in sorted(labels):
        lab = lab.lower()
        canon = "".join(term_words(lab))
        if len(lab) < 4 or lab in generic or lab in safe or not re.fullmatch(r"[a-z0-9-]+", lab) \
                or lab.isdigit() or not canon or all(w in generic or w.isdigit() for w in term_words(lab)):
            continue
        if any(canon in w for w in corpus):
            continue                      # would match the bundle's own fixed text
        out.append(lab)
    return out


def _prepare_out(out: Path) -> None:
    if not out.exists():
        return
    if not out.is_dir():
        raise FileExistsError(f"{out} exists and is not a directory")
    entries = {p.name for p in out.iterdir()}
    if not entries:
        out.rmdir()
        return
    if entries <= (ALLOWED_TOP | {"skeleton", "features", "pages"}) and ("PROFILE.md" in entries or "profile.yaml" in entries):
        shutil.rmtree(out)
        return
    raise FileExistsError(f"{out} exists and does not look like a previous framework-profile bundle; "
                          "choose another --out")


def distill(src: Path, out: Path, denylist: list[str] = (), rename_packages_to: str = "com.example",
            keep_identifiers: bool = False, pages: list[str] = ()) -> Path:
    """pages: glob patterns (relative to src, or absolute) of page-definition files (.yaml/.yml/.json) to read.
    Off by default; matched files whose names look like credentials/secrets/.env/.properties are never read."""
    src = Path(src).resolve()
    out = Path(out).resolve()
    rename_packages_to = rename_packages_to or "com.example"
    if not src.is_dir():
        raise NotADirectoryError(f"{src} is not a directory")
    if out == src or out in src.parents:
        raise ValueError("--out must not be the source folder or one of its parents")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*", rename_packages_to or ""):
        raise ValueError(f"bad package prefix {rename_packages_to!r}")
    tmp = out.parent / f".{out.name}.distill-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        terms = _build(src, tmp, list(denylist or []), rename_packages_to, keep_identifiers, {out, tmp},
                       list(pages or []))
        problems = check(tmp, terms)
        if problems:
            from .check import explain
            raise DistillLeakError(problems, explain(tmp, terms))
        _prepare_out(out)
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out


def _build(src: Path, dst: Path, user_terms: list[str], rename_to: str, keep: bool, exclude: set[Path],
           page_globs: list[str] = ()) -> list[str]:
    stats: Counter = Counter()
    page_sel, page_counts, page_warns = pages_mod.select(src, page_globs, exclude) if page_globs else ([], Counter(), [])
    for w in page_warns:
        warnings.warn(w, pages_mod.DistillWarning, stacklevel=3)
    java_files, feature_files, not_read = _walk(src, exclude, {p.resolve() for _, p in page_sel})
    files = {"java_parsed": 0, "java_stub_fallback": 0, "java_token_fallback": 0, "java_skipped": 0,
             "features_parsed": 0, "features_skipped": 0}

    # ---- read + parse Java (each file independently; failures never stop the run)
    units: list[Unit] = []
    texts: list[str] = []
    host_texts: list[str] = []
    for p in java_files:
        text = read_source(p)
        if text is None:
            files["java_skipped"] += 1
            continue
        texts.append(text)
        host_texts.extend(comment_texts(text))
        rel = p.relative_to(src).as_posix()
        cleaned, masked, ncomments, blocks = scan(text)
        stats["comments removed"] += ncomments
        try:
            cu = javalang.parse.parse(text)
            u = Unit(rel, cu, make_scope(cu))
        except Exception:  # noqa: BLE001
            stub = stub_source(cleaned, masked)
            try:
                cu = javalang.parse.parse(stub)
                u = Unit(rel, cu, make_scope(cu), mode="stub")
            except Exception:  # noqa: BLE001
                m = re.search(r"^\s*package\s+([\w.]+)\s*;", cleaned, re.M)
                u = Unit(rel, None, Scope(package=m.group(1) if m else ""), mode="tokens", stub_text=stub)
        u.raw_strings = string_values(cleaned) + blocks
        host_texts.extend(u.raw_strings)
        try:
            if u.cu is not None:
                index_unit(u)
        except Exception:  # noqa: BLE001
            u.classes = []
        units.append(u)

    feature_docs = []
    for p in feature_files:
        text = read_source(p)
        doc = parse_feature(text) if text is not None else None
        if doc is None or not doc.get("feature"):
            files["features_skipped"] += 1
            continue
        texts.append(text)
        host_texts.append(text)
        feature_docs.append(doc)

    page_docs, page_texts = pages_mod.load(page_sel, page_counts) if page_sel else ([], [])
    texts += page_texts
    host_texts += page_texts
    del page_texts

    # ---- naming context
    pkgs = [u.scope.package for u in units if u.scope.package]
    prefix = common_prefix(pkgs)
    terms = user_terms + [t for t in _auto_terms(texts, prefix, host_texts)
                          if t not in {x.lower() for x in user_terms}]
    del texts, host_texts
    rename = Renamer(terms)
    anon = Anonymizer(rename, keep=keep)
    phrase = PhraseRedactor(rename)
    tags = TagRedactor(phrase)
    fp_types, fp_methods, fp_fields = set(), set(), set()
    for u in units:
        for ci in u.classes:
            fp_types.add(ci.name)
            fp_methods.update(m.name for m in ci.methods if not m.is_ctor)
            fp_fields.update(ci.fields)
            if ci.kind == "enum" and ci.node.body:
                fp_fields.update(c.name for c in ci.node.body.constants or [])
        if u.mode == "tokens":
            fp_types.update(re.findall(r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)", u.stub_text))
    policy = Policy(rename, anon, prefix, rename_to, set(pkgs), fp_types, fp_methods, fp_fields)

    # ---- skeletons
    dst.mkdir(parents=True)
    renderer = Renderer(policy, phrase, stats)
    written: set[str] = set()

    def write_java(pkg: str, name: str, text: str) -> None:
        d = dst / "skeleton" / Path(*pkg.split("."))
        base = re.sub(r"[^\w$]", "_", name) or "Type0"
        rel = f"{pkg}/{base}"
        k = 1
        while rel in written:
            k += 1
            rel = f"{pkg}/{base}_{k}"
        written.add(rel)
        d.mkdir(parents=True, exist_ok=True)
        (d / (rel.rsplit("/", 1)[1] + ".java")).write_text(text, encoding="utf-8")

    for u in units:
        if u.mode == "tokens":
            r = render_tokens(u.stub_text, policy, stats)
            if r is None:
                files["java_skipped"] += 1
                continue
            text, name = r
            m = re.search(r"^package ([\w.]+);", text, re.M)
            files["java_token_fallback"] += 1
            write_java(m.group(1) if m else policy.rename_to, name, text)
            continue
        try:
            text, name, pkg = renderer.unit(u.cu, u.scope)
        except Exception:  # noqa: BLE001 - emit nothing from a file we can't render
            files["java_skipped"] += 1
            continue
        files["java_parsed" if u.mode == "ast" else "java_stub_fallback"] += 1
        if u.mode == "stub":
            text = "// distill: Java 17 fallback, bodies not analyzed\n" + text
        write_java(pkg, name, text)

    # ---- step definitions
    step_texts: list[str] = []
    for doc in feature_docs:
        step_texts += raw_step_texts(doc)
    defs = collect([u for u in units if u.cu is not None])
    count_usage(defs, step_texts)
    del step_texts
    inf = Inference([c for u in units for c in u.classes])
    cat = Catalog(defs, inf, policy, phrase)
    cat.build()

    # ---- features
    fw = FeatureWriter(phrase, tags, stats)
    for doc in feature_docs:
        text = fw.render(doc)
        files["features_parsed"] += 1
        d = dst / "features"
        d.mkdir(exist_ok=True)
        (d / f"feature-{files['features_parsed']:03d}.feature").write_text(text, encoding="utf-8")

    # ---- locators
    lc = LocatorCollector(policy, stats)
    ag_refs = 0
    js_calls: Counter = Counter()
    js_objects: set[str] = set()
    for u in units:
        try:
            lc.unit(u)
        except Exception:  # noqa: BLE001
            stats["files with locator extraction errors"] += 1
        for v in u.raw_strings:
            if re.search(r"\bag-[a-z]", v):
                ag_refs += 1
            calls = [c for c in re.findall(r"\.\s*([A-Za-z_$][\w$]*)\s*\(", v) if c in AG_GRID_API]
            js_calls.update(calls)
            if calls:
                js_objects.update(policy.member(o, "field") for o in re.findall(r"\bwindow\.([A-Za-z_$][\w$]*)", v))
    dump = dict(sort_keys=False, allow_unicode=False, width=120, default_flow_style=False)
    page_md = None
    if page_globs:  # ---- page-definition files (opt-in)
        plc = LocatorCollector(policy, Counter())
        adapter = pages_mod.PageAdapter(rename.denied, (lc, plc))
        outs = adapter.run(page_docs)
        if page_docs:
            (dst / "pages").mkdir()
        for i, (doc, o) in enumerate(zip(page_docs, outs), 1):
            (dst / "pages" / f"Page{i}.yaml").write_text(pages_mod.dump_page(i, len(page_docs), doc, o),
                                                         encoding="utf-8")
        page_schema = adapter.schema(page_docs, page_counts)
        (dst / "pages-schema.yaml").write_text(
            "# Schema inferred from the page-definition files (key paths with names collapsed to <name>).\n"
            + yaml.safe_dump(page_schema, **dump), encoding="utf-8")
        page_md = pages_mod.md_section(page_schema, plc.summary() if plc.entries else None)
        files["pages_read"] = len(page_docs)
        files["pages_not_read"] = sum(page_counts.values())
    loc_items = lc.items()
    loc_summary = lc.summary()


    # ---- profile
    java, naming, facts = conventions(units, policy)
    waits, grid = wait_and_grid_stats(units, policy, loc_items, ag_refs, js_calls, js_objects)
    exprs = Counter(e["expression"] for e in cat.entries)
    kw = Counter(e["keyword"] for e in cat.entries)
    acts = Counter(e["action"] for e in cat.entries)
    ptypes = Counter(p["type"] for e in cat.entries for p in e["params"])
    hooks = Counter()
    for u in units:
        for ci in u.classes:
            for m in ci.methods:
                for a in m.annotations:
                    s = a.name.rsplit(".", 1)[-1]
                    if s in ("Before", "After", "BeforeStep", "AfterStep", "BeforeAll", "AfterAll"):
                        hooks[s] += 1
    examples = [e["pattern"] for e in sorted(cat.entries, key=lambda e: (-e["usage"], e["id"]))[:10]]
    cucumber = {"step_definitions": len(cat.entries), "keywords": dict(kw.most_common()),
                "expressions": dict(exprs.most_common()), "parameter_types": dict(ptypes.most_common()),
                "hooks": dict(hooks.most_common()), "inferred_actions": dict(acts.most_common()),
                "draft_mappings": len(cat.mappings), "draft_todo": len(cat.todo),
                "unused": sum(1 for e in cat.entries if e["usage"] == 0), "examples": examples}
    removed = dict(stats)
    removed.update({f"step phrases: {k}": v for k, v in phrase.stats.items()})
    removed.update({f"tags: {k}": v for k, v in tags.stats.items()})
    removed["identifiers with a denylisted term renamed"] = rename.hits
    removed["identifier stems anonymized"] = anon.count
    removed["imports generalized (unknown third-party)"] = policy.generalized_imports
    removed["denylist terms (incl. auto-detected)"] = len(rename.terms)
    removed["auto-detected denylist terms (hosts, emails, package)"] = len(terms) - len(user_terms)
    files["not_read"] = dict(sorted(not_read.items()))
    files["not_read_total"] = sum(v for k, v in not_read.items() if k not in ("skipped directories",))
    stats_out = {"locators": {k: loc_summary[k] for k in ("total", "by_kind", "length_median", "length_p90")},
                 "test_id_attributes": test_id_attrs(loc_summary),
                 "attribute_names": loc_summary["attribute_names"], "waits": waits, "grid": grid}
    naming = {"identifiers": "kept" if keep else "anonymized", "package_prefix": policy.rename_to, **naming}
    profile = {"java": java, "stats": stats_out, "cucumber": cucumber, "naming": naming, "files": files,
               "facts": facts, "keep_identifiers": keep}
    (dst / "profile.yaml").write_text(
        "# Detected conventions. `java:` uses exactly the keys of crawler.config.JavaDef.\n"
        + yaml.safe_dump({k: profile[k] for k in ("java", "stats", "cucumber", "naming", "files")}, **dump),
        encoding="utf-8")
    (dst / "locators.yaml").write_text(
        "# Every locator found, as a redacted shape (values replaced by typed placeholders).\n"
        + yaml.safe_dump({"summary": loc_summary, "locators": loc_items}, **dump), encoding="utf-8")
    (dst / "steps.yaml").write_text(
        "# Every step definition, redacted, with usage counts and the action inferred from its body.\n"
        + yaml.safe_dump({"steps": cat.entries}, **dump), encoding="utf-8")
    (dst / "step-mappings.draft.yaml").write_text(mapping_yaml(cat), encoding="utf-8")
    feature_review = {"steps": fw.steps_total, "kept": sum(fw.review.values()),
                      "examples": [t for t, _ in sorted(fw.review.items(), key=lambda kv: (-kv[1], kv[0]))]}
    md = {**profile, "locator_summary": loc_summary, "waits": waits, "grid": grid, "cucumber": cucumber,
          "removed": removed, "review": sorted(set(cat.review)), "pages_md": page_md,
          "feature_review": feature_review}
    (dst / "PROFILE.md").write_text(render_md(md), encoding="utf-8")
    return terms
