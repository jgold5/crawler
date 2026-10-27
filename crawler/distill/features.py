"""Regenerate .feature files from the Gherkin AST with names, descriptions, comments and data removed."""
from __future__ import annotations

from collections import Counter

from gherkin.parser import Parser
from gherkin.token_scanner import TokenScanner

from .redact import PhraseRedactor, TagRedactor


def parse_feature(text: str):
    try:
        return Parser().parse(TokenScanner(text))
    except Exception:  # noqa: BLE001 - any parse error: the file is skipped
        return None


def raw_step_texts(doc) -> list[str]:
    """Original step texts (in memory only, for step-definition usage counts)."""
    out = []

    def steps_of(children):
        for ch in children:
            for key in ("background", "scenario"):
                if key in ch:
                    out.extend(s["text"] for s in ch[key].get("steps", []))
            if "rule" in ch:
                steps_of(ch["rule"].get("children", []))
    feat = (doc or {}).get("feature")
    if feat:
        steps_of(feat.get("children", []))
    return out


class FeatureWriter:
    def __init__(self, phrase: PhraseRedactor, tags: TagRedactor, stats: Counter):
        self.phrase = phrase
        self.tags = tags
        self.stats = stats
        self.n_feature = 0
        self.n_scenario = 0
        self.n_rule = 0
        self.n_examples = 0
        self.steps_total = 0
        self.review: Counter = Counter()   # redacted step text that keeps words outside the common-word list

    def _step_text(self, text: str) -> str:
        """Step text after the keyword, vocabulary-gated (PhraseRedactor mode "text")."""
        out = self.phrase(text, "text")
        self.steps_total += 1
        if self.phrase.unusual_words(out):
            self.review[out] += 1
        return out

    def render(self, doc) -> str:
        feat = doc.get("feature")
        self.stats["feature comments removed"] += len(doc.get("comments", []))
        if not feat:
            return ""
        lines: list[str] = []
        if feat.get("language", "en") != "en":
            lines.append(f"# language: {feat['language']}")
        self.n_feature += 1
        self._tags(lines, feat.get("tags"), "")
        lines.append(f"{feat['keyword']}: Feature {self.n_feature}")
        self._desc(feat)
        self._children(lines, feat.get("children", []), "  ")
        return "\n".join(lines).rstrip() + "\n"

    def _desc(self, node) -> None:
        if (node.get("description") or "").strip():
            self.stats["descriptions removed"] += 1

    def _tags(self, lines, tags, pad) -> None:
        if tags:
            self.stats["tags"] += len(tags)
            lines.append(pad + " ".join(self.tags(t["name"]) for t in tags))

    def _children(self, lines, children, pad) -> None:
        for ch in children:
            lines.append("")
            if "background" in ch:
                bg = ch["background"]
                self._desc(bg)
                if bg.get("name"):
                    self.stats["names replaced"] += 1
                lines.append(f"{pad}{bg['keyword']}:")
                self._steps(lines, bg.get("steps", []), pad + "  ")
            elif "scenario" in ch:
                sc = ch["scenario"]
                self.n_scenario += 1
                self.stats["names replaced"] += 1
                self._desc(sc)
                self._tags(lines, sc.get("tags"), pad)
                lines.append(f"{pad}{sc['keyword']}: Scenario {self.n_scenario}")
                self._steps(lines, sc.get("steps", []), pad + "  ")
                for ex in sc.get("examples", []):
                    lines.append("")
                    self._tags(lines, ex.get("tags"), pad + "  ")
                    self._desc(ex)
                    self.n_examples += 1
                    lines.append(f"{pad}  {ex['keyword']}: Examples {self.n_examples}")
                    header = ex.get("tableHeader")
                    if header:
                        rows = [[self.phrase.ph_inner(c["value"]) for c in header["cells"]]]
                        for r in ex.get("tableBody", []):
                            rows.append([self._cell(c["value"]) for c in r["cells"]])
                        lines += self._table(rows, pad + "    ")
            elif "rule" in ch:
                rule = ch["rule"]
                self.n_rule += 1
                self.stats["names replaced"] += 1
                self._desc(rule)
                self._tags(lines, rule.get("tags"), pad)
                lines.append(f"{pad}{rule['keyword']}: Rule {self.n_rule}")
                self._children(lines, rule.get("children", []), pad + "  ")

    def _cell(self, v: str) -> str:
        if v == "":
            return ""
        self.stats["table cells replaced"] += 1
        return "<value>"

    def _steps(self, lines, steps, pad) -> None:
        for st in steps:
            lines.append(f"{pad}{st['keyword'].strip()} {self._step_text(st['text'])}")
            if "dataTable" in st:
                rows = st["dataTable"]["rows"]
                out = []
                for i, r in enumerate(rows):
                    if i == 0:   # header cells: same vocabulary gate, word by word (no kept first word)
                        out.append([self.phrase(c["value"], "text") for c in r["cells"]])
                    else:
                        out.append([self._cell(c["value"]) for c in r["cells"]])
                lines += self._table(out, pad + "  ")
            if "docString" in st:
                ds = st["docString"]
                self.stats["doc strings removed"] += 1
                delim = ds.get("delimiter", '"""')
                lines += [pad + "  " + delim, pad + "  <doc>", pad + "  " + delim]

    @staticmethod
    def _table(rows, pad) -> list[str]:
        if not rows:
            return []
        w = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(max(len(r) for r in rows))]
        out = []
        for r in rows:
            cells = [(r[i] if i < len(r) else "").replace("|", "\\|").ljust(w[i]) for i in range(len(w))]
            out.append(pad + "| " + " | ".join(cells) + " |")
        return out
