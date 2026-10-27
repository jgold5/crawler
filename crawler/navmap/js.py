"""In-page helpers for `crawler navmap` (installed on demand in a document as ``window.__crawlerNav``).

* ``bars()``: tab-bar candidates of the document: a parent with 2+ visible, short-labelled, clickable children (a
  ``<ul>`` of ``<li>`` tabs with no ARIA at all, a ``role=tablist``/``menubar``, ``mat-tab-nav-bar`` links, a group
  of menu triggers...), scored by where it is (header/nav, near the top, horizontal) and by trigger evidence
  (``aria-haspopup``, ``mat-menu-trigger-for``, ``data-toggle=dropdown``, ``aria-expanded``, a caret).
  Nothing is decided here: the caller confirms a bar by clicking its items.
* ``mark(tok)`` / ``look(tok)``: what a click showed: every element is marked before the click; afterwards the
  NEW visible clickables (not marked, or hidden before) are the dropdown's options (leaf-most, spinners left out),
  with their panel (lowest common container), whether a spinner is still showing and whether a new iframe appeared.
* ``settled(tok, ms)``: a wait predicate: the options are there, no spinner, and the same count + labels for
  ``ms`` (two looks at least ``ms`` apart agree). Polled by ``wait_for_function``; no timers.
* ``check(items, el, others)``: does each locator select exactly the element (or a node that contains it / is
  inside it, without containing another tab/option): clicking it does what clicking the element does.
"""
from __future__ import annotations

NAV_JS = r"""(() => {
  if (window.__crawlerNav) return true;
  const CARET = /[▾▼⌄▴▲›»⯆∨˅↓▿]+/g;
  const EXPLICIT = 'a,button,[role=tab],[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox],[role=option],' +
    '[role=link],[role=button],[mat-menu-item],[mat-tab-link],[onclick]';
  const TRIGGER = '[aria-haspopup]:not([aria-haspopup=false]),[mat-menu-trigger-for],[matmenutriggerfor],' +
    '[data-toggle=dropdown],[data-bs-toggle=dropdown],[aria-expanded]';
  const SPIN = '[role=progressbar]:not([aria-valuenow]),.app-spinner,[aria-busy=true],mat-spinner,mat-progress-spinner,.spinner,.loading';
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const is = (el, sel) => { try { return el.matches(sel); } catch (e) { return false; } };
  const style = el => { try { return getComputedStyle(el); } catch (e) { return null; } };
  const visible = el => {
    if (!el || el.nodeType !== 1 || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = style(el);
    return !!s && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0;
  };
  const label = el => {
    let t = '';
    try { t = el.innerText; } catch (e) { t = ''; }
    t = norm((t || el.textContent || '').replace(CARET, ' '));
    return t || norm(el.getAttribute('aria-label') || el.getAttribute('title') || '');
  };
  const pointerOrigin = el => {
    const s = style(el);
    if (!s || s.cursor !== 'pointer') return false;
    const p = el.parentElement && style(el.parentElement);
    return !p || p.cursor !== 'pointer';
  };
  const tabindexed = el => el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1';
  const clickable = el => is(el, EXPLICIT) || pointerOrigin(el) || tabindexed(el);
  const shortLabel = t => !!t && t.length <= 40 && t.split(' ').length <= 5;
  const IN_OVERLAY = '[role=menu],[role=dialog],[role=listbox],[role=grid],[role=tooltip],.cdk-overlay-container,' +
    '.ag-root-wrapper,table,select,form';
  const target = item => {     // the element to click for a bar item: the item, or its one inner a/button
    if (is(item, EXPLICIT)) return item;
    const lab = label(item);
    const inner = [...item.querySelectorAll('a,button,[role=tab],[role=menuitem],[role=link],[role=button]')]
      .filter(e => visible(e) && label(e) === lab);
    return inner.length === 1 ? inner[0] : item;
  };
  const itemOk = c => {
    if (!visible(c) || is(c, SPIN)) return false;
    const t = label(c);
    if (!shortLabel(t)) return false;
    if (clickable(c)) return true;
    const inner = c.querySelector(EXPLICIT);
    return !!(inner && visible(inner)) || c.localName === 'li' && [...c.querySelectorAll('*')].some(pointerOrigin);
  };
  const st = { bars: [] };

  st.bars = function () {
    const out = [];
    const seen = new Set();
    const parents = new Set();
    for (const el of document.querySelectorAll('li,a,button,[role=tab],[role=menuitem],' + TRIGGER + ',[tabindex]')) {
      if (el.parentElement && visible(el)) parents.add(el.parentElement);
    }
    for (const p of document.querySelectorAll('ul,ol,nav,[role=tablist],[role=menubar]')) parents.add(p);
    for (const p of parents) {
      if (seen.has(p) || !visible(p) || p.closest(IN_OVERLAY)) continue;
      seen.add(p);
      const kids = [...p.children].filter(visible);
      const items = kids.filter(itemOk);
      if (items.length < 2 || items.length < 0.6 * kids.length) continue;
      const labels = items.map(label);
      if (new Set(labels).size < labels.length) continue;            // repeated labels: a list of data, not tabs
      const ev = [];
      let score = 0;
      const r = p.getBoundingClientRect();
      if (p.closest('[role=tablist],[role=menubar]')) { score += 3; ev.push('role=' + p.closest('[role=tablist],[role=menubar]').getAttribute('role')); }
      if (p.closest('nav,[role=navigation],[mat-tab-nav-bar],.mat-mdc-tab-nav-bar,.mat-tab-nav-bar')) { score += 2; ev.push('in nav'); }
      if (p.closest('header,[role=banner],mat-toolbar,.mat-toolbar,.navbar')) { score += 2; ev.push('in header'); }
      if (r.top < 160) { score += 2; ev.push('near the top'); }
      const tops = items.map(i => Math.round(i.getBoundingClientRect().top / 6));
      const horizontal = new Set(tops).size === 1;
      if (horizontal) { score += 2; ev.push('horizontal'); }
      const trig = items.filter(i => is(i, TRIGGER) || !!i.querySelector(TRIGGER)).length;
      if (trig) { score += 3; ev.push(trig + ' menu trigger(s)'); }
      const carets = items.filter(i => !!i.querySelector('svg,i[class*=caret],i[class*=chevron],[class*=caret],[class*=arrow]') ||
        CARET.test(i.textContent || '')).length;
      CARET.lastIndex = 0;
      if (carets) { score += 1; ev.push(carets + ' with a caret'); }
      if (items.every(i => i.localName === 'li')) { score += 1; ev.push('<' + p.localName + '> of <li>'); }
      if (items.length > 12) score -= 2;
      const links = items.filter(i => { const a = target(i); return a.localName === 'a' && /^https?:/i.test(a.getAttribute('href') || ''); });
      if (links.length === items.length) score -= 1;                 // plain hyperlinks (a link list)
      out.push({ el: p, items, labels, score, evidence: ev, top: Math.round(r.top), left: Math.round(r.left),
                 tag: p.localName, triggers: items.map(i => is(i, TRIGGER) || !!i.querySelector(TRIGGER) ? 1 : 0),
                 carets: items.map(i => (i.querySelector('svg,[class*=caret],[class*=chevron]') ? 1 : 0)) });
    }
    // a bar nested inside another bar's item (a menu under a tab) is not a bar of its own
    const res = out.filter(b => !out.some(o => o !== b && o.items.some(i => i.contains(b.el))));
    res.sort((a, b) => b.score - a.score || a.top - b.top);
    st.bars = res;
    return res.map((b, i) => ({ idx: i, labels: b.labels, score: b.score, evidence: b.evidence, top: b.top,
                                left: b.left, tag: b.tag, triggers: b.triggers, carets: b.carets }));
  };
  st.item = function (bar, lab) {              // the click target of the bar item labelled ``lab``
    const b = st.bars[bar];
    if (!b) return null;
    const it = b.items.find(i => i.isConnected && label(i) === lab);
    return it ? target(it) : null;
  };
  st.barEl = function (bar) { const b = st.bars[bar]; return b ? b.el : null; };
  st.itemsOf = function (bar) { const b = st.bars[bar]; return b ? b.items.filter(i => i.isConnected).map(target) : []; };

  st.mark = function (tok) {
    for (const el of document.querySelectorAll('*')) { el.__navTok = tok; el.__navVis = visible(el); }
    st.look = null;
    return true;
  };
  const fresh = (el, tok) => el.__navTok !== tok || !el.__navVis;
  st.scan = function (tok) {
    const news = [];
    let iframe = false, spinner = false, existed = 0;
    for (const el of document.querySelectorAll('body *')) {
      if (!fresh(el, tok) || !visible(el)) continue;
      news.push(el);
      if (el.localName === 'iframe' || el.localName === 'frame') iframe = true;
    }
    const newSet = new Set(news);
    let opts = news.filter(el => clickable(el) && !is(el, SPIN) && !el.closest(SPIN) && el.localName !== 'iframe' &&
                                 shortLabel(label(el)));
    // leaf-most: an option holding another option is the wrapper (li > a: the a)
    opts = opts.filter(el => !opts.some(o => o !== el && el.contains(o)));
    // a new clickable whose ancestor is also new and clickable with the same label: keep the inner one only
    const panel = (() => {
      if (!opts.length) return null;
      let p = opts[0].parentElement;
      while (p && !opts.every(o => p.contains(o))) p = p.parentElement;
      return p;
    })();
    for (const el of news) if (is(el, SPIN) && (!panel || panel.contains(el) || el.contains(panel))) spinner = true;
    if (!spinner && panel) spinner = !![...panel.querySelectorAll(SPIN)].some(visible);
    for (const o of opts) if (o.__navTok === tok) existed++;
    return { news: news.length, iframe, spinner, opts, panel, existed, newSet };
  };
  st.look = null;
  st.state = function (tok) {
    const s = st.scan(tok);
    const labels = s.opts.map(label);
    return { news: s.news, iframe: s.iframe, spinner: s.spinner, labels, existed: s.existed,
             panelTag: s.panel ? s.panel.localName : null };
  };
  st.settled = function ([tok, ms]) {
    const s = st.scan(tok);
    if (s.iframe && !s.opts.length) return { iframe: true };
    if (!s.opts.length || s.spinner) { st.look = null; return false; }
    const sig = s.opts.map(label).join('\u0001');
    const now = performance.now();
    if (!st.look || st.look.sig !== sig) { st.look = { sig, t: now }; return false; }
    return now - st.look.t >= ms ? { n: s.opts.length } : false;
  };
  st.something = function (tok) { const s = st.scan(tok); return s.news > 0 || s.iframe; };
  st.closed = function (tok) { return st.scan(tok).opts.length === 0; };
  st.options = function (tok) { return st.scan(tok).opts; };
  st.panel = function (tok) { return st.scan(tok).panel; };
  st.label = label;
  st.visible = visible;

  st.check = function ([items, el, others]) {
    others = (others || []).filter(Boolean);
    return items.map(it => {
      const nodes = [];
      try {
        if (it[0] === 'css') nodes.push(...document.querySelectorAll(it[1]));
        else {
          const r = document.evaluate(it[1], document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
          for (let i = 0; i < r.snapshotLength; i++) nodes.push(r.snapshotItem(i));
        }
      } catch (e) { return { n: -1, same: false, any: false, near: false, exact: false, error: String(e).split('\n')[0] }; }
      const rel = x => !!el && x.nodeType === 1 && (x === el || x.contains(el) || el.contains(x)) &&
                       !others.some(o => o !== el && x.contains(o) && !o.contains(el));
      const other = nodes.length === 1 && others.some(o => o !== el && (nodes[0] === o || o.contains(nodes[0]) ||
                                                                         nodes[0].contains(o)) && !rel(nodes[0]));
      return { n: nodes.length, same: nodes.length === 1 && rel(nodes[0]), any: nodes.some(rel), near: false,
               exact: nodes.length === 1 && nodes[0] === el, other };
    });
  };
  window.__crawlerNav = st;
  return true;
})()"""

ENSURE = NAV_JS

__all__ = ["NAV_JS"]
