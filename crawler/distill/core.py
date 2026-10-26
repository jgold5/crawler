"""distill(): framework repo -> redacted framework-profile bundle. Offline, deterministic, fail-closed."""
from __future__ import annotations

import os
import re
import shutil
from collections import Counter
from pathlib import Path

import javalang
import yaml

from .check import ALLOWED_TOP, check
from .features import FeatureWriter, parse_feature, raw_step_texts
from .idents import Anonymizer
from .javasrc import read_source, scan, string_values, stub_source
from .jmodel import Policy, Scope, Unit, common_prefix, index_unit, make_scope
from .locators import LocatorCollector
from .profile import conventions, render_md, test_id_attrs, wait_and_grid_stats
from .redact import EMAIL_RX, URL_RX, PhraseRedactor, Renamer, TagRedactor, hosts_in, registrable_label
from .skeleton import Renderer, render_tokens
from .stepdefs import Catalog, Inference, collect, count_usage, mapping_yaml
from .vocab import GENERIC_STEMS, PACKAGE_GENERIC, SAFE_IMPORT_PREFIXES, SAFE_WORDS

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
    def __init__(self, problems: list[str]):
        self.problems = problems
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


def _walk(src: Path, exclude: set[Path]) -> tuple[list[Path], list[Path], Counter]:
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
            if f in ("module-info.java", "package-info.java"):
                other["java module/package info"] += 1
            elif f.endswith(".java"):
                java.append(p)
            elif f.endswith(".feature"):
                feats.append(p)
            else:
                other[_category(p)] += 1
    return java, feats, other


def _auto_terms(texts: list[str], prefix: str) -> list[str]:
    labels = set()
    for t in texts:
        for m in URL_RX.finditer(t):
            host = re.sub(r"^[a-zA-Z][\w+.-]*://", "", m.group(0)).split("/")[0].split(":")[0].split("@")[-1]
            lab = registrable_label(host)
            if lab:
                labels.add(lab)
        for m in EMAIL_RX.finditer(t):
            lab = registrable_label(m.group(0).split("@", 1)[1])
            if lab:
                labels.add(lab)
        for h in hosts_in(t):
            lab = registrable_label(h)
            if lab:
                labels.add(lab)
    for seg in prefix.split("."):
        if seg and seg.lower() not in PACKAGE_GENERIC:
            labels.add(seg.lower())
    safe = " ".join(SAFE_IMPORT_PREFIXES).lower()
    out = []
    for lab in sorted(labels):
        lab = lab.lower()
        if len(lab) < 4 or lab in PUBLIC_LABELS or lab in SAFE_WORDS or lab in GENERIC_STEMS or lab in safe \
                or not re.fullmatch(r"[a-z0-9-]+", lab) or lab.isdigit():
            continue
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
    if entries <= (ALLOWED_TOP | {"skeleton", "features"}) and ("PROFILE.md" in entries or "profile.yaml" in entries):
        shutil.rmtree(out)
        return
    raise FileExistsError(f"{out} exists and does not look like a previous framework-profile bundle; "
                          "choose another --out")


def distill(src: Path, out: Path, denylist: list[str] = (), rename_packages_to: str = "com.example",
            keep_identifiers: bool = False) -> Path:
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
        terms = _build(src, tmp, list(denylist or []), rename_packages_to, keep_identifiers, {out, tmp})
        problems = check(tmp, terms)
        if problems:
            raise DistillLeakError(problems)
        _prepare_out(out)
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out


def _build(src: Path, dst: Path, user_terms: list[str], rename_to: str, keep: bool, exclude: set[Path]) -> list[str]:
    stats: Counter = Counter()
    java_files, feature_files, not_read = _walk(src, exclude)
    files = {"java_parsed": 0, "java_stub_fallback": 0, "java_token_fallback": 0, "java_skipped": 0,
             "features_parsed": 0, "features_skipped": 0}

    # ---- read + parse Java (each file independently; failures never stop the run)
    units: list[Unit] = []
    texts: list[str] = []
    for p in java_files:
        text = read_source(p)
        if text is None:
            files["java_skipped"] += 1
            continue
        texts.append(text)
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
        feature_docs.append(doc)

    # ---- naming context
    pkgs = [u.scope.package for u in units if u.scope.package]
    prefix = common_prefix(pkgs)
    terms = user_terms + [t for t in _auto_terms(texts, prefix) if t not in {x.lower() for x in user_terms}]
    del texts
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

    # ---- features
    fw = FeatureWriter(phrase, tags, stats)
    step_texts: list[str] = []
    for doc in feature_docs:
        step_texts += raw_step_texts(doc)
        text = fw.render(doc)
        files["features_parsed"] += 1
        d = dst / "features"
        d.mkdir(exist_ok=True)
        (d / f"feature-{files['features_parsed']:03d}.feature").write_text(text, encoding="utf-8")

    # ---- locators
    lc = LocatorCollector(policy, stats)
    ag_refs = 0
    for u in units:
        try:
            lc.unit(u)
        except Exception:  # noqa: BLE001
            stats["files with locator extraction errors"] += 1
        ag_refs += sum(1 for v in u.raw_strings if re.search(r"\bag-[a-z]", v))
        if u.mode == "ast" and u.cu is not None:
            for _, n in u.cu.filter(javalang.tree.Literal):
                if isinstance(n.value, str) and re.search(r"\bag-[a-z]", n.value):
                    ag_refs += 1
    loc_items = lc.items()
    loc_summary = lc.summary()

    # ---- step definitions
    defs = collect([u for u in units if u.cu is not None])
    count_usage(defs, step_texts)
    del step_texts
    inf = Inference([c for u in units for c in u.classes])
    cat = Catalog(defs, inf, policy, phrase)
    cat.build()

    # ---- profile
    java, naming, facts = conventions(units, policy)
    waits, grid = wait_and_grid_stats(units, policy, loc_items, ag_refs)
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
                 "test_id_attributes": test_id_attrs(loc_summary), "waits": waits, "grid": grid}
    profile = {"java": java, "stats": stats_out, "cucumber": cucumber, "naming": naming, "files": files,
               "facts": facts, "keep_identifiers": keep}
    dump = dict(sort_keys=False, allow_unicode=False, width=120, default_flow_style=False)
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
    md = {**profile, "locator_summary": loc_summary, "waits": waits, "grid": grid, "cucumber": cucumber,
          "removed": removed, "review": sorted(set(cat.review))}
    (dst / "PROFILE.md").write_text(render_md(md), encoding="utf-8")
    return terms
