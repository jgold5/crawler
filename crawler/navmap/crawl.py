"""Driving the live app for `crawler navmap`: one Explorer per run (a fresh browser context each).

Run 1 finds the tab bar (static candidates, each confirmed by CLICKING its items: a bar is one whose items open a
dropdown) and then, like every later run, goes through it:

* pass A, per tab: check the tab's locators and the repository's entries against it, click it, and wait until
  either a dropdown is there and stable (its options are new visible clickables, there is no spinner, and the same
  count + labels were seen across two looks >= 250 ms apart, all bounded by the timeout: lazy options are waited
  for), or the content iframe changed (a direct link), or nothing at all happened (nothing new after the app
  settled + a short grace); then close the dropdown (Escape; a reload if it stays open);
* pass B, per option: re-open its tab, check the option's locators and the entries AFTER the tab is open (and,
  for the report, what the entries matched BEFORE it was opened), click it, wait for the content iframe to be
  replaced/changed and the app to settle, and identify the page from the iframe.

No fixed sleeps: waits are ``wait_for_function`` polls and ``wait_for_event`` calls bounded by the timeout.
"""
from __future__ import annotations

import re
import secrets
import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..author.quality import FLAG_ABS, FLAG_ID, FLAG_POS, _generated, fragility
from ..author.slots import Cand, static_cands
from ..emit.xpath import xpath_literal
from ..frames import FrameError, frame_chain, frame_for, iframe_candidates
from ..locators import generate
from ..resolver import STABLE_MS, is_transient
from .js import NAV_JS
from .model import Bar, NavElem, PageHit

# never clicked while exploring (a sign-out ends the borrowed session; the others change data)
UNSAFE = re.compile(r"(?i)\b(?:sign|log)\s*-?\s*(?:out|off)\b|\blog(?:out|off)\b|\bdelete\b|\bremove\b|\bexit\b|\bquit\b")
NOTHING_MS = 1500        # the app settled and nothing new appeared for this long: the click opened nothing
MIN_BAR_SCORE = 4
MAX_PROBE_BARS = 4
MAX_PROBE_ITEMS = 3
BAD_FLAGS = {FLAG_POS, FLAG_ABS, FLAG_ID}
_BODY_READY_JS = "() => document.readyState !== 'loading' && !!document.body"
_DOC_JS = "() => (window.__crawlerReady && window.__crawlerReady.id) || document.__crawlerNavDoc || " \
          "(document.__crawlerNavDoc = Math.random().toString(36).slice(2))"
_CALL = "([f, a]) => window.__crawlerNav[f](a)"
_SETTLED = "([t, ms]) => !!window.__crawlerNav && window.__crawlerNav.settled([t, ms])"
_CLOSED = "t => !!window.__crawlerNav && window.__crawlerNav.closed(t)"


class NavError(Exception):
    pass


def unsafe(label: str) -> bool:
    return bool(UNSAFE.search(label or ""))


def _norm(s: str) -> str:
    return " ".join((s or "").split()).casefold()


class Opened:
    """What one click did: ``what`` is dropdown | page | nothing | top-level."""

    def __init__(self, what: str, tok: str, ms: int, labels=None, existed: int = 0, frame=None, url: str = ""):
        self.what, self.tok, self.ms = what, tok, ms
        self.labels = list(labels or [])
        self.existed = existed
        self.frame = frame
        self.url = url


def _ensure(frame) -> None:
    frame.evaluate(NAV_JS)


def call(frame, fn: str, arg=None):
    _ensure(frame)
    return frame.evaluate(_CALL, [fn, arg])


def call_handles(frame, fn: str, arg=None) -> list:
    _ensure(frame)
    h = frame.evaluate_handle(_CALL, [fn, arg])
    try:
        out = []
        for _k, v in sorted(h.get_properties().items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 1 << 30):
            el = v.as_element()
            if el is not None:
                out.append(el)
        return out
    finally:
        try:
            h.dispose()
        except PlaywrightError:
            pass


def call_handle(frame, fn: str, arg=None):
    _ensure(frame)
    h = frame.evaluate_handle(_CALL, [fn, arg])
    el = h.as_element()
    if el is None:
        h.dispose()
    return el


def nav_check(frame, items: list[tuple[str, str]], el, others=None) -> list[dict]:
    """[(kind, locator)] -> [{n, same, any, exact, other}]: ``same``: exactly one match, the element itself or a node
    that contains it / is inside it without containing another tab/option of ``others`` (clicking it does the
    same); ``other``: exactly one match, another of ``others``. A transient error is raised."""
    if not items:
        return []
    try:
        return call(frame, "check", [[list(i) for i in items], el, list(others or [])])
    except PlaywrightError as e:
        if is_transient(e):
            raise
        msg = str(e).split("\n")[0].strip()
        return [{"n": -1, "same": False, "any": False, "near": False, "exact": False, "error": msg} for _ in items]


def counts(frame, items: list[tuple[str, str]]) -> list[int]:
    return [r.get("n", -1) for r in nav_check(frame, items, None, [])]


# ---------------------------------------------------------------------------------------------- locator building

def _stable_class(token: str, cfg) -> bool:
    if len(token) < 3 or token.startswith(("fa-", "mat-focus", "mdc-", "cdk-", "ng-")):
        return False
    return not any(re.search(p, token) for p in cfg.unstable_class_patterns or [])


def anchor_steps(anchors: list[dict], cfg) -> list[tuple[str, str, bool, str]]:
    """XPath steps selecting a container, best first: (step, separator to its items, class-anchored?, strategy).
    Test ids, a stable id, role, aria-label, then stable class tokens; ancestors up to 3 levels."""
    out: list[tuple[str, str, bool, str]] = []
    for a in anchors:
        tag, attrs = a["tag"], a.get("attrs") or {}
        if not re.fullmatch(r"[a-z][a-z0-9-]*", tag or ""):
            continue
        sep = "/" if a.get("depth") == 0 and a.get("direct") else "//"
        preds: list[tuple[str, bool, str]] = []
        for t in cfg.test_id_attrs or []:
            if attrs.get(t):
                preds.append((f"@{t}={xpath_literal(attrs[t])}", False, "test-id"))
        if attrs.get("id") and not _generated(attrs["id"], cfg):
            preds.append((f"@id={xpath_literal(attrs['id'])}", False, "id"))
        if attrs.get("role") and attrs["role"] not in ("presentation", "none"):
            preds.append((f"@role={xpath_literal(attrs['role'])}", False, "role"))
        if attrs.get("aria-label"):
            preds.append((f"@aria-label={xpath_literal(attrs['aria-label'])}", False, "aria-label"))
        for tok in (attrs.get("class") or "").split():
            if _stable_class(tok, cfg):
                preds.append((f"contains(@class,{xpath_literal(tok)})", True, "class"))
        if tag in ("nav", "header") and not preds:
            preds.append(("", False, "landmark"))
        for p, is_cls, strat in preds:
            out.append((f"//{tag}[{p}]" if p else f"//{tag}", sep, is_cls, strat))
    rank = {"test-id": 0, "id": 1, "role": 2, "aria-label": 3, "class": 4, "landmark": 5}
    return sorted(out, key=lambda a: rank.get(a[3], 9))      # stable sort: the nearest container first per kind


def own_step(info: dict) -> str | None:
    """The element's own step anchored on its text: ``li[normalize-space()='Positions']``."""
    tag, text = info.get("tag") or "", info.get("text") or ""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", tag) or not text or len(text) > 60:
        return None
    return f"{tag}[normalize-space()={xpath_literal(text)}]"


def anchored_cands(frame, el, anchors: list[dict], cfg, others) -> list[Cand]:
    """Container-anchored candidates (the menu or bar container + the element's label), verified now."""
    info = call(frame, "own", el)
    step = own_step(info)
    if step is None:
        return []
    locs: list[tuple[str, bool, str]] = []
    for anchor, sep, is_cls, strat in anchor_steps(anchors, cfg):
        locs.append((f"{anchor}{sep}{step}", is_cls, strat))
    if not locs:
        return []
    res = nav_check(frame, [("xpath", x) for x, _c, _s in locs], el, others)
    out = []
    for (x, is_cls, strat), r in zip(locs, res):
        if not r.get("same") or not r.get("exact"):
            continue
        flags = fragility(x, "xpath", cfg, stable_classes=True)
        out.append(Cand(x, f"anchored:{strat}", 0.0, flags))
    return out


def clean(cands: list[Cand]) -> list[Cand]:
    """No positions, absolute paths or generated ids; duplicates dropped."""
    seen, out = set(), []
    for c in cands:
        if c.loc in seen or set(c.flags) & BAD_FLAGS:
            continue
        seen.add(c.loc)
        out.append(c)
    return out


# ---------------------------------------------------------------------------------------------- the explorer

class Explorer:
    """One run: a page in a fresh context. ``nm`` is the NavMapper (config, repository, shared observations)."""

    def __init__(self, nm, run: int, page, tracker):
        self.nm, self.run, self.page, self.tracker = nm, run, page, tracker
        self.cfg = nm.cfg
        self.timeout = int(nm.cfg.timeout_ms)

    # ------------------------------------------------------------------ basics
    def start(self) -> None:
        """The start page, settled in every frame (a shell inside a frame loads its content iframe late)."""
        self.page.goto(self.cfg.base_url, wait_until="domcontentloaded")
        seen = -1
        for _ in range(4):           # frames attached while settling get their own settle
            frames = [f for f in self.page.frames if not f.is_detached()]
            if len(frames) == seen:
                break
            seen = len(frames)
            rep = self.tracker.wait(frames)
            if not rep.ok:
                self.nm.note_ready(self.run, "start page", rep)
                break

    def settle(self, *frames) -> None:
        rep = self.tracker.wait([self.page.main_frame] + [f for f in frames if f is not None])
        if not rep.ok:
            self.nm.note_ready(self.run, "after a click", rep)

    def frame_at(self, path: list[str]):
        f = self.page.main_frame
        for xp in path or []:
            f = frame_for(f, xp, self.timeout)
        return f

    def frame_path(self, frame) -> list[str] | None:
        """Iframe XPaths from the top document down to ``frame`` (None: some iframe has no verified locator)."""
        out = []
        for f in frame_chain(frame)[1:]:
            try:
                cands = clean(static_cands(iframe_candidates(self.page, f, self.cfg), self.cfg))
            except (PlaywrightError, FrameError):
                return None
            if not cands:
                return None
            out.append(cands[0].loc)
        return out

    def _doc(self, frame):
        try:
            return frame.evaluate(_DOC_JS)
        except PlaywrightError:
            return None

    def snapshot(self) -> dict:
        return {id(f): (f, f.url) for f in self.page.frames}

    def changed_frame(self, before: dict, exclude=()):
        """The largest visible frame that is new or navigated since ``before`` (not about:blank)."""
        best, area = None, 0.0
        for f in self.page.frames:
            if f is self.page.main_frame or f in exclude:
                continue
            try:
                if f.is_detached():
                    continue
                old = before.get(id(f))
                if old is not None and old[0] is f and old[1] == f.url:
                    continue
                if not f.url or f.url.startswith("about:"):
                    continue
                el = f.frame_element()
                box = el.bounding_box()
            except PlaywrightError:
                continue
            if not box or box["width"] * box["height"] < 5000:
                continue
            if box["width"] * box["height"] > area:
                best, area = f, box["width"] * box["height"]
        return best

    def wait_content(self, before: dict, top_url: str, deadline: float, exclude=()):
        """After a click: the content frame that changed (None: none did before the deadline), or "top-level"."""
        while True:
            if self.page.url != top_url:
                return "top-level"
            f = self.changed_frame(before, exclude)
            if f is not None:
                left = max(1.0, (deadline - time.monotonic()) * 1000)
                try:
                    f.wait_for_url(lambda u: not u.startswith("about:"), wait_until="domcontentloaded", timeout=left)
                    f.wait_for_function(_BODY_READY_JS, timeout=left)
                    if not f.is_detached():
                        return f
                except PlaywrightError as e:
                    if not is_transient(e) and time.monotonic() >= deadline:
                        return None
                continue
            left = (deadline - time.monotonic()) * 1000
            if left <= 0:
                return None
            try:
                self.page.wait_for_event("framenavigated", timeout=max(1.0, min(left, 250.0)))
            except PlaywrightTimeoutError:
                pass

    # ------------------------------------------------------------------ one click
    def open(self, frame, handle, *, budget_ms: int | None = None) -> Opened:
        """Click ``handle`` (a tab) and wait for what it does (see the module doc)."""
        tok = secrets.token_hex(4)
        call(frame, "mark", tok)
        doc = self._doc(frame)
        before = self.snapshot()
        top_url = self.page.url
        t0 = time.monotonic()
        deadline = t0 + (budget_ms or self.timeout) / 1000.0
        handle.click(timeout=self.timeout)
        rep = self.tracker.wait([self.page.main_frame, frame], max(1, int((deadline - time.monotonic()) * 1000)))
        if not rep.ok:
            self.nm.note_ready(self.run, "after a tab click", rep)
        quiet_from = time.monotonic()
        exclude = (frame,) + tuple(f for f in self.page.frames if f is not frame and _contains(f, frame))
        while True:
            ms = round((time.monotonic() - t0) * 1000)
            if self.page.url != top_url and frame is self.page.main_frame:
                return Opened("top-level", tok, ms, url=self.page.url)
            if self._doc(frame) not in (doc, None):
                return Opened("top-level", tok, ms, url=frame.url)
            f = self.changed_frame(before, exclude)
            if f is not None:
                got = self.wait_content(before, top_url, deadline, exclude)
                if got == "top-level":
                    return Opened("top-level", tok, ms, url=self.page.url)
                return Opened("page", tok, round((time.monotonic() - t0) * 1000), frame=got)
            left = (deadline - time.monotonic()) * 1000
            if left <= 0:
                return Opened("nothing", tok, ms)
            try:
                v = frame.wait_for_function(_SETTLED, arg=[tok, STABLE_MS], polling=100,
                                            timeout=max(1.0, min(left, 400.0))).json_value()
                if isinstance(v, dict) and v.get("n"):
                    st = call(frame, "state", tok)
                    return Opened("dropdown", tok, round((time.monotonic() - t0) * 1000), st["labels"],
                                  st.get("existed", 0))
                if isinstance(v, dict) and v.get("iframe"):
                    try:
                        self.page.wait_for_event("framenavigated", timeout=max(1.0, min(left, 250.0)))
                    except PlaywrightTimeoutError:
                        pass
                    continue
            except PlaywrightTimeoutError:
                pass
            except PlaywrightError as e:
                if not is_transient(e):
                    raise
                continue
            try:
                something = bool(call(frame, "something", tok))
            except PlaywrightError:
                something = True
            if not something and (time.monotonic() - quiet_from) * 1000 >= NOTHING_MS:
                return Opened("nothing", tok, round((time.monotonic() - t0) * 1000))

    def close(self, frame, tok: str) -> bool:
        """Close an open dropdown (Escape; reload the start page if it stays open). False: reloaded."""
        try:
            if call(frame, "closed", tok):
                return True
            self.page.keyboard.press("Escape")
            frame.wait_for_function(_CLOSED, arg=tok, polling=100, timeout=min(1500, self.timeout))
            return True
        except PlaywrightError:
            pass
        self.start()
        return False

    # ------------------------------------------------------------------ the tab bar
    def bars_in(self, frame) -> list[dict]:
        try:
            return call(frame, "bars")
        except PlaywrightError:
            return []

    def find_bar(self, frame, labels: list[str]) -> int | None:
        """The bar (index) in ``frame`` holding every label of ``labels`` (the most of them, at least)."""
        want = {_norm(x) for x in labels}
        best, score = None, 0
        for b in self.bars_in(frame):
            have = {_norm(x) for x in b["labels"]}
            k = len(want & have)
            if k > score or (k == score and best is not None and len(have) < len(best["labels"])):
                best, score = b, k
        if best is None or score < max(1, min(len(want), 2)):
            return None
        return best["idx"]

    def detect(self, want: list[str] | None) -> list[Bar]:
        """Bar candidates, the top document first, then each frame (shallow first); a bar is confirmed when one of
        its items opens a dropdown. Stops at the first document with a confirmed bar."""
        main = self.page.main_frame
        levels = [[(main, [])]]
        probed: list[Bar] = []
        for depth in range(4):
            if depth >= len(levels):
                break
            confirmed = False
            for frame, path in levels[depth]:
                bars = self._candidates(frame, path, want)
                for b in bars:
                    self.probe(b)
                probed += bars
                confirmed = confirmed or any(b.confirmed for b in bars)
            if confirmed:
                break
            # next level: the frames one level down (found again from a fresh start page)
            self.start()
            nxt = []
            for f in self.page.frames:
                try:
                    if f.is_detached() or f is main or len(frame_chain(f)) - 1 != depth + 1 or f.url.startswith("about:"):
                        continue
                except PlaywrightError:
                    continue
                p = self.frame_path(f)
                if p is not None:
                    nxt.append((f, p))
            levels.append(nxt)
        return probed

    def _candidates(self, frame, path, want) -> list[Bar]:
        where = "top document" if not path else f"frame {path[-1]}"
        out = []
        for c in self.bars_in(frame):
            if c["score"] < MIN_BAR_SCORE:
                continue
            if want and not {_norm(w) for w in want} <= {_norm(x) for x in c["labels"]}:
                continue
            out.append(Bar(list(path), c["idx"], c["labels"], c["score"], c["evidence"], c["top"], c["tag"],
                           c["triggers"], c["carets"], where=where))
        return out[:MAX_PROBE_BARS]

    def probe(self, b: Bar) -> None:
        order = sorted(range(len(b.labels)), key=lambda i: (-(b.triggers[i] if i < len(b.triggers) else 0),
                                                            -(b.carets[i] if i < len(b.carets) else 0), i))
        tried = 0
        for i in order:
            label = b.labels[i]
            if unsafe(label):
                b.probes.append(f"{label}: not clicked (looks destructive)")
                continue
            if tried >= MAX_PROBE_ITEMS:
                break
            tried += 1
            try:
                frame = self.frame_at(b.frame_path)
                idx = self.find_bar(frame, b.labels)
                if idx is None:
                    b.probes.append(f"{label}: the bar was gone")
                    break
                h = self._item(frame, idx, label)
                if h is None:
                    b.probes.append(f"{label}: not found")
                    continue
                o = self.open(frame, h)
            except (PlaywrightError, FrameError) as e:
                b.probes.append(f"{label}: {str(e).splitlines()[0]}")
                self.start()
                continue
            if o.what == "dropdown":
                b.probes.append(f"{label}: opened a dropdown ({', '.join(o.labels)}) in {o.ms} ms")
                b.confirmed = True
                self.close(frame, o.tok)
                break
            b.probes.append({"page": f"{label}: loaded a page (no dropdown)", "nothing": f"{label}: opened nothing",
                             "top-level": f"{label}: left the app page ({o.url})"}[o.what])
            if o.what == "top-level":
                self.start()

    def _item(self, frame, idx: int, label: str):
        _ensure(frame)
        h = frame.evaluate_handle("([i, l]) => window.__crawlerNav.item(i, l)", [idx, label])
        el = h.as_element()
        if el is None:
            h.dispose()
        return el

    def _items(self, frame, idx: int) -> list:
        return call_handles(frame, "itemsOf", idx)

    # ------------------------------------------------------------------ exploring
    def locate_bar(self):
        bar = self.nm.bar
        frame = self.frame_at(bar.frame_path)
        idx = self.find_bar(frame, bar.labels)
        if idx is None:
            # the bar may render after the start page's own XHR: wait for the app, then look once more
            self.settle(frame)
            idx = self.find_bar(frame, bar.labels)
        if idx is None:
            raise NavError(f"run {self.run}: the tab bar ({', '.join(bar.labels)}) is not on the start page")
        return frame, idx

    def tab_pass(self, tabs: list[str]) -> None:
        for tab in tabs:
            el = self.nm.elem("tab", tab, tab, self.run)
            if el is None:
                continue
            if unsafe(tab):
                el.skipped = "not clicked (looks destructive)"
                continue
            try:
                self._tab(el)
            except (PlaywrightError, FrameError, NavError) as e:
                el.notes.append(f"run {self.run}: {str(e).splitlines()[0]}")
                self.start()

    def _tab(self, el: NavElem) -> None:
        frame, idx = self.locate_bar()
        h = self._item(frame, idx, el.label)
        if h is None:
            el.notes.append(f"run {self.run}: tab not found")
            return
        others = self._items(frame, idx)
        if self.run == 1 or not el.cands:
            anchors = call(frame, "barAnchors", idx)
            el.cands = clean(anchored_cands(frame, h, anchors, self.cfg, others)
                             + static_cands(generate(frame, h, self.cfg), self.cfg))
        el.checks[self.run] = nav_check(frame, [("xpath", c.loc) for c in el.cands], h, others)
        el.entries[self.run] = self.nm.check_entries(frame, h, others, el.label)
        el.pre[self.run] = self.nm.count_entries(frame)       # before the tab is opened (for its options)
        o = self.open(frame, h)
        el.opens[self.run] = o.what
        el.open_ms[self.run] = o.ms
        if self.run == 1 and o.what != "dropdown":
            el.lazy = False
        if o.what == "dropdown":
            el.options[self.run] = o.labels
            if self.run == 1:
                el.lazy = o.existed == 0
            self.close(frame, o.tok)
        elif o.what == "page":
            self.at_page(el, o.frame, frame, o.ms)
        elif o.what == "top-level":
            el.notes.append(f"run {self.run}: clicking it left the start page ({o.url})")
            self.start()

    def option_pass(self, tabs: list[str]) -> None:
        for tab in tabs:
            t = self.nm.elems.get(("tab", tab, tab))
            if t is None or t.opens.get(self.run) != "dropdown":
                continue
            seen = set()
            for label in t.options.get(self.run, []):
                if label in seen:
                    t.notes.append(f"run {self.run}: two options are labelled {label!r}; only the first is mapped")
                    continue
                seen.add(label)
                el = self.nm.elem("option", tab, label, self.run)
                if el is None:
                    continue
                if unsafe(label):
                    el.skipped = "not clicked (looks destructive)"
                    continue
                try:
                    self._option(t, el)
                except (PlaywrightError, FrameError, NavError) as e:
                    el.notes.append(f"run {self.run}: {str(e).splitlines()[0]}")
                    self.start()

    def _open_tab(self, t: NavElem):
        """Re-find the bar and open tab ``t`` (once more after a fresh start if its dropdown does not open)."""
        for attempt in (1, 2):
            frame, idx = self.locate_bar()
            h = self._item(frame, idx, t.label)
            if h is None:
                raise NavError(f"tab {t.label!r} not found")
            pre = self.nm.count_entries(frame)
            o = self.open(frame, h)
            if o.what == "dropdown":
                return frame, o, pre
            if attempt == 1:
                self.start()
        raise NavError(f"tab {t.label!r} did not open its dropdown ({o.what})")

    def _option(self, t: NavElem, el: NavElem) -> None:
        frame, o, pre = self._open_tab(t)
        opts = call_handles(frame, "options", o.tok)
        labels = [call(frame, "label", x) for x in opts] if opts else []
        try:
            h = opts[labels.index(el.label)]
        except ValueError:
            el.notes.append(f"run {self.run}: not in the dropdown of {t.label!r} this time ({', '.join(labels)})")
            self.close(frame, o.tok)
            return
        if self.run == 1 or not el.cands:
            anchors = call(frame, "anchors", o.tok)
            el.cands = clean(anchored_cands(frame, h, anchors, self.cfg, opts)
                             + static_cands(generate(frame, h, self.cfg), self.cfg))
        el.checks[self.run] = nav_check(frame, [("xpath", c.loc) for c in el.cands], h, opts)
        el.entries[self.run] = self.nm.check_entries(frame, h, opts, el.label)
        el.pre[self.run] = pre
        before = self.snapshot()
        top_url = self.page.url
        t0 = time.monotonic()
        h.click(timeout=self.timeout)
        exclude = (frame,) + tuple(f for f in self.page.frames if f is not frame and _contains(f, frame))
        got = self.wait_content(before, top_url, t0 + self.timeout / 1000.0, exclude)
        ms = round((time.monotonic() - t0) * 1000)
        if got is None:
            el.opens[self.run] = "nothing"
            el.page_ms[self.run] = ms
            self.close(frame, o.tok)
            return
        if got == "top-level":
            el.opens[self.run] = "top-level"
            el.notes.append(f"run {self.run}: clicking it left the start page ({self.page.url})")
            self.start()
            return
        el.opens[self.run] = "page"
        self.at_page(el, got, frame, ms, t0)

    def at_page(self, el: NavElem, content, bar_frame, ms: int, t0: float | None = None) -> None:
        """The page a click opened: wait for it, identify it, check its iframe locators."""
        self.settle(bar_frame, content)
        el.page_ms[self.run] = round((time.monotonic() - t0) * 1000) if t0 is not None else ms
        try:
            frame_el = content.frame_element()
            parent = content.parent_frame
            hit = self.nm.identify(parent, frame_el, content)
            if self.run == 1 or not el.iframe_cands:
                el.iframe_cands = clean(static_cands(iframe_candidates(self.page, content, self.cfg), self.cfg))
            el.iframe_checks[self.run] = nav_check(parent, [("xpath", c.loc) for c in el.iframe_cands], frame_el, [])
            if hit.page_key and hit.page_key in self.nm.frame_entries:
                e = self.nm.frame_entries[hit.page_key]
                el.iframe_entry[self.run] = nav_check(parent, [(e.kind, e.value or "")], frame_el, [])[0]
        except (PlaywrightError, FrameError) as e:
            if not is_transient(e):
                raise
            hit = PageHit(None, f"the page's iframe changed while it was identified ({str(e).splitlines()[0]})")
        el.page[self.run] = hit


def _contains(outer, inner) -> bool:
    """``outer`` is an ancestor frame of ``inner``."""
    f = inner.parent_frame
    while f is not None:
        if f is outer:
            return True
        f = f.parent_frame
    return False


def frame_name_tokens(name: str, src: str) -> set[str]:
    words = set(re.split(r"[^A-Za-z0-9]+", name or ""))
    path = urlparse(src or "").path
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", path.rsplit("/", 1)[-1]) if path else ""
    words |= set(re.split(r"[^A-Za-z0-9]+", stem)) | {stem}
    return {re.sub(r"[^a-z0-9]", "", w.casefold()) for w in words if w}


__all__ = ["Explorer", "NavError", "nav_check", "unsafe", "anchor_steps", "clean", "frame_name_tokens"]
