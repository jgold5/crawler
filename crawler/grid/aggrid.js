/* crawler/grid/aggrid.js - in-page AG Grid engine (M1b).
 *
 * Installs window.__crawlerGrid (idempotent). Works on both AG Grid DOM layouts:
 *   "split"   (v31-v35): a logical row is split over .ag-pinned-left-cols-container,
 *             .ag-center-cols-container and .ag-pinned-right-cols-container; the parts share
 *             @row-index/@row-id; vertical scroller .ag-body-viewport, horizontal
 *             .ag-center-cols-viewport.
 *   "unified" (v36+): one div[@row-index] per row with nested .ag-grid-pinned-left-cells /
 *             .ag-grid-scrolling-cells / .ag-grid-pinned-right-cells; one scroller
 *             (.ag-grid-viewport, role=grid) for both axes with a sticky header inside it.
 * Scrollers are found from the live DOM (nearest scrollable ancestor), not hard-coded.
 * Waiting is frame-driven (requestAnimationFrame) and bounded by a deadline; no sleeps.
 */
(function () {
  "use strict";
  var VERSION = 8;
  if (window.__crawlerGrid && window.__crawlerGrid.VERSION === VERSION) return;

  // ------------------------------------------------------------------ utils
  // Mirrors XPath normalize-space(): only [ \t\r\n] collapse (NBSP is kept).
  function norm(s) { return String(s == null ? "" : s).replace(/[ \t\r\n]+/g, " ").replace(/^ | $/g, ""); }
  function lower(s) { return norm(s).toLowerCase(); }
  function lit(s) {  // same output as crawler.grid.xpath.xpath_literal / XPathLiteral.java
    s = String(s);
    if (s.indexOf("'") < 0) return "'" + s + "'";
    if (s.indexOf('"') < 0) return '"' + s + '"';
    var out = [];
    s.split("'").forEach(function (p, i) { if (i) out.push("\"'\""); if (p) out.push("'" + p + "'"); });
    return "concat(" + out.join(", ") + ")";
  }
  function now() { return performance.now(); }
  var frames = 0;   // frames waited (reported in timings, useful when tuning speed)
  function frame() { frames++; return new Promise(function (r) { requestAnimationFrame(function () { r(); }); }); }
  function xpAll(xp, ctx) {
    var r = document.evaluate(xp, ctx || document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    var out = [];
    for (var i = 0; i < r.snapshotLength; i++) out.push(r.snapshotItem(i));
    return out;
  }
  function xpSafe(xp) { try { return xpAll(xp); } catch (e) { return null; } }
  function visible(el) {
    if (!el || !el.isConnected || el.closest(".ag-hidden")) return false;
    if (!el.getClientRects().length) return false;
    var cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  function rx(list) {
    return (list || []).map(function (p) { try { return new RegExp(p); } catch (e) { return null; } }).filter(Boolean);
  }
  function any(res, s) { return res.some(function (r) { return r.test(s); }); }
  function uniq(arr) { return arr.filter(function (v, i) { return arr.indexOf(v) === i; }); }

  // ------------------------------------------------------------------ grids
  function allGrids() {
    return Array.prototype.filter.call(document.querySelectorAll(".ag-root-wrapper"), function (w) {
      return !(w.parentElement && w.parentElement.closest(".ag-root-wrapper"));   // skip detail grids
    });
  }
  function ariaGrid(w) { return w.querySelector("[role=grid],[role=treegrid]"); }
  // XPath predicate for the ARIA grid element. @aria-rowcount sits on it in every version, while
  // its role differs (treegrid in v31, grid in v32+), so prefer the attribute for upgrade safety.
  function gridPred(ge) {
    if (!ge) return "[@role='grid']";
    return ge.hasAttribute("aria-rowcount") ? "[@aria-rowcount]" : "[@role=" + lit(ge.getAttribute("role")) + "]";
  }
  function hostChain(w) {  // w and its ancestors that contain no other grid (max 12)
    var others = allGrids().filter(function (o) { return o !== w; });
    var chain = [];
    for (var e = w, i = 0; e && e !== document.body && e !== document.documentElement && i < 12; e = e.parentElement, i++) {
      if (others.some(function (o) { return e.contains(o); })) break;
      chain.push(e);
    }
    return chain;
  }
  var HEADING_XP = "preceding::*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6 or @role='heading'][1]";
  function headingFor(w) {  // nearest heading before the grid in document order (may share its section)
    return document.evaluate(HEADING_XP, w, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  }
  function labelOf(e) {
    var a = e.getAttribute("aria-label");
    if (a && norm(a)) return norm(a);
    var lb = e.getAttribute("aria-labelledby");
    if (lb) {
      var t = norm(lb.split(/\s+/).map(function (id) { var x = document.getElementById(id); return x ? x.textContent : ""; }).join(" "));
      if (t) return t;
    }
    return null;
  }
  function describe(w) {
    var chain = hostChain(w), label = null;
    var ge = ariaGrid(w);
    var cand = ge ? chain.concat([ge]) : chain;
    for (var i = 0; i < cand.length && !label; i++) label = labelOf(cand[i]);
    var h = headingFor(w);
    var id = null;
    for (var j = 0; j < chain.length && !id; j++) id = chain[j].id || null;
    return { label: label, heading: h ? norm(h.textContent) : null, id: id };
  }
  function gridTitle(d) {
    if (d.label) return "'" + d.label + "' (aria-label)";
    if (d.heading) return "'" + d.heading + "' (heading)";
    if (d.id) return "#" + d.id;
    return "(unnamed grid)";
  }
  function selectorHits(sel, w) {
    var els;
    try { els = document.querySelectorAll(sel); } catch (e) { return false; }
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el === w || w.contains(el)) return true;
      if (el.contains(w) && hostChain(w).indexOf(el) >= 0) return true;
    }
    return false;
  }
  function defFor(w, defs) {
    for (var i = 0; i < defs.length; i++) if (defs[i].selector && selectorHits(defs[i].selector, w)) return defs[i];
    return null;
  }
  function listing(grids) { return grids.map(function (w) { return gridTitle(describe(w)); }); }

  function findGrid(spec) {
    var grids = allGrids(), defs = spec.defs || [];
    if (!grids.length) return { retry: true, error: "No AG Grid on the page" };
    var q = spec.grid;
    if (q == null || q === "") {
      if (grids.length === 1) return { w: grids[0], def: defFor(grids[0], defs), how: "only grid" };
      return { error: grids.length + " grids on the page; name one with grid=...", alternatives: listing(grids) };
    }
    var alias = defs.filter(function (d) { return d.name === q; })[0] ||
                defs.filter(function (d) { return lower(d.name) === lower(q); })[0];
    if (alias && alias.selector) {
      var m = grids.filter(function (w) { return selectorHits(alias.selector, w); });
      if (m.length === 1) return { w: m[0], def: alias, how: "alias" };
      if (m.length > 1) return { error: "Grid alias '" + alias.name + "' (selector " + alias.selector + ") matches " + m.length + " grids", alternatives: listing(m) };
    }
    var descs = grids.map(describe);
    var tiers = [
      ["aria-label", function (d) { return d.label === norm(q); }],
      ["aria-label", function (d) { return d.label && lower(d.label) === lower(q); }],
      ["heading", function (d) { return d.heading === norm(q); }],
      ["heading", function (d) { return d.heading && lower(d.heading) === lower(q); }]
    ];
    for (var t = 0; t < tiers.length; t++) {
      var hits = grids.filter(function (w, i) { return tiers[t][1](descs[i]); });
      if (hits.length === 1) return { w: hits[0], def: (alias && !alias.selector) ? alias : defFor(hits[0], defs), how: tiers[t][0] };
      if (hits.length > 1) return { error: "Grid name '" + q + "' matches " + hits.length + " grids by " + tiers[t][0], alternatives: listing(hits) };
    }
    return { retry: true, error: "No grid named '" + q + "' (alias, aria-label or heading)", alternatives: listing(grids) };
  }

  // ------------------------------------------------------------------ rows / cells / sections
  function rowsOf(w) {
    return Array.prototype.filter.call(w.querySelectorAll("[row-index]"), function (r) {
      return /^\d+$/.test(r.getAttribute("row-index")) && r.closest(".ag-root-wrapper") === w;
    });
  }
  function rowIdx(r) { return parseInt(r.getAttribute("row-index"), 10); }
  function rowOf(el) { return el.closest("[row-index]"); }
  function cellsByCol(w, colId) {
    var sel = '[row-index] [col-id="' + String(colId).replace(/["\\]/g, "\\$&") + '"]';
    return Array.prototype.filter.call(w.querySelectorAll(sel), function (c) {
      return /^\d+$/.test(rowOf(c).getAttribute("row-index")) && c.closest(".ag-root-wrapper") === w;
    });
  }
  function cellAt(w, idx, colId) {
    var rows = rowsOf(w).filter(function (r) { return rowIdx(r) === idx; });
    for (var i = 0; i < rows.length; i++) {
      var cs = rows[i].querySelectorAll("[col-id]");
      for (var j = 0; j < cs.length; j++) if (cs[j].getAttribute("col-id") === colId && rowOf(cs[j]) === rows[i]) return cs[j];
    }
    return null;
  }
  var LEFT = ".ag-pinned-left-header,.ag-pinned-left-cols-container,.ag-grid-pinned-left-cells,.ag-pinned-left-floating-top,.ag-pinned-left-floating-bottom";
  var RIGHT = ".ag-pinned-right-header,.ag-pinned-right-cols-container,.ag-grid-pinned-right-cells,.ag-pinned-right-floating-top,.ag-pinned-right-floating-bottom";
  function sectionOf(el) { return el.closest(LEFT) ? "left" : el.closest(RIGHT) ? "right" : "center"; }
  function layoutOf(w) {
    if (w.querySelector(".ag-center-cols-container,.ag-center-cols-viewport")) return "split";
    if (w.querySelector(".ag-grid-scrolling-cells,.ag-grid-viewport")) return "unified";
    return "unknown";
  }
  function loading(w) {
    return Array.prototype.some.call(w.querySelectorAll(".ag-overlay-loading-wrapper,.ag-overlay-loading-center"), visible);
  }
  function noRows(w) {
    return Array.prototype.some.call(w.querySelectorAll(".ag-overlay-no-rows-wrapper,.ag-overlay-no-rows-center,.ag-overlay-no-matching-rows-wrapper,.ag-overlay-no-matching-rows-center"), visible);
  }

  // ------------------------------------------------------------------ scrolling
  function scrollable(e, axis) {
    var cs = getComputedStyle(e);
    var ov = axis === "y" ? cs.overflowY : cs.overflowX;
    if (!/(auto|scroll|overlay)/.test(ov)) return false;
    return axis === "y" ? e.scrollHeight > e.clientHeight + 1 : e.scrollWidth > e.clientWidth + 1;
  }
  function nearestScroller(start, w, axis) {
    for (var e = start; e && e !== w.parentElement; e = e.parentElement) if (scrollable(e, axis)) return e;
    return null;
  }
  function vScroller(w) {
    var r = rowsOf(w)[0];
    var s = r ? nearestScroller(r, w, "y") : null;
    if (s) return s;
    var c = w.querySelector(".ag-body-viewport,.ag-grid-viewport");
    return c && scrollable(c, "y") ? c : null;
  }
  function hScroller(w) {
    var cells = w.querySelectorAll("[row-index] [col-id], .ag-header-cell[col-id]");
    for (var i = 0; i < cells.length; i++) {
      if (sectionOf(cells[i]) !== "center") continue;
      var s = nearestScroller(cells[i], w, "x");
      if (s) return s;
    }
    var fb = w.querySelectorAll(".ag-center-cols-viewport,.ag-grid-viewport,.ag-body-horizontal-scroll-viewport");
    for (var j = 0; j < fb.length; j++) if (scrollable(fb[j], "x")) return fb[j];
    return null;
  }
  // Visible box for body rows inside the vertical scroller (below a sticky header if inside it).
  function bodyBox(w, sc) {
    var r = sc.getBoundingClientRect();
    var top = r.top, bottom = r.top + sc.clientHeight;
    var hdr = w.querySelector(".ag-header");
    if (hdr && sc.contains(hdr)) top = Math.max(top, hdr.getBoundingClientRect().bottom);
    return { top: top, bottom: bottom };
  }
  // Visible box for center (non-pinned) columns inside the horizontal scroller.
  function centerBox(w, sc) {
    var r = sc.getBoundingClientRect();
    var left = r.left, right = r.left + sc.clientWidth;
    var cells = w.querySelectorAll(".ag-header-cell[col-id], [row-index] [col-id]");
    for (var i = 0; i < cells.length; i++) {
      var sec = sectionOf(cells[i]);
      if (sec === "center") continue;
      var cr = cells[i].getBoundingClientRect();
      if (!cr.width) continue;
      if (sec === "left" && sc.contains(cells[i])) left = Math.max(left, cr.right);
      if (sec === "right" && sc.contains(cells[i])) right = Math.min(right, cr.left);
    }
    return { left: left, right: right };
  }
  function signature(w) {
    var rows = rowsOf(w), parts = [];
    for (var i = 0; i < rows.length; i++) parts.push(rows[i].getAttribute("row-index") + ":" + rows[i].querySelectorAll("[col-id]").length + ":" + rows[i].textContent.length);
    parts.push(w.querySelectorAll(".ag-header-cell[col-id]").length);
    return parts.join("|");
  }
  function covered(w, sc) {
    if (!sc) return true;
    var rows = rowsOf(w);
    if (!rows.length) return false;
    var box = bodyBox(w, sc), top = false, bottom = false, maxIdx = -1, maxBottom = -1e9;
    for (var i = 0; i < rows.length; i++) {
      var rr = rows[i].getBoundingClientRect();
      if (!rr.height) continue;
      if (rr.top <= box.top + 1 && rr.bottom > box.top) top = true;
      if (rr.top < box.bottom && rr.bottom >= box.bottom - 1) bottom = true;
      if (rowIdx(rows[i]) >= maxIdx) { maxIdx = rowIdx(rows[i]); maxBottom = Math.max(maxBottom, rr.bottom); }
    }
    var atEnd = sc.scrollTop >= sc.scrollHeight - sc.clientHeight - 1;
    if (!bottom && maxBottom < box.bottom - 1) bottom = atEnd || (sc.scrollTop === 0 && maxBottom > box.top);
    if (!top && sc.scrollTop === 0) top = true;
    return top && bottom;
  }
  // Running CSS transitions that move/fade whole rows (animateRows after sort/filter/transactions).
  // A row animating out keeps its new @row-index until AG Grid destroys it at the end of the
  // transition, so nothing found during an animation is trusted until it is over.
  var ROW_PROPS = /^(transform|top|opacity|height)$/;
  function rowAnimating(w) {
    if (!w.getAnimations || typeof CSSTransition === "undefined") return false;
    try {
      return w.getAnimations({ subtree: true }).some(function (a) {
        var t = a.effect && a.effect.target;
        return a instanceof CSSTransition && a.playState === "running" && ROW_PROPS.test(a.transitionProperty) &&
               !!t && t.hasAttribute && t.hasAttribute("row-index");
      });
    } catch (e) { return false; }
  }
  // Wait until the grid re-rendered after a scroll: rows cover the visible box and the DOM
  // signature is unchanged between two frames (AG Grid renders one frame after the scroll event).
  // `check` runs every frame and short-circuits as soon as it is truthy. Callers that conclude
  // "absent" do not trust a single settle: sweepRows revisits any row index it never saw.
  // `before` (signature taken before scrolling) lets it return one frame earlier, as soon as the
  // grid has visibly reacted and the rows cover the viewport.
  async function settle(w, sc, deadline, check, before) {
    var prev = null;
    for (var i = 0; i < 120 && now() < deadline; i++) {
      await frame();
      if (check) { var c = check(); if (c) return c; }
      var sig = signature(w);
      if ((sig === prev || (before != null && sig !== before)) && covered(w, sc) && !rowAnimating(w)) return null;
      prev = sig;
    }
    return check ? check() : null;
  }

  // ------------------------------------------------------------------ columns
  function headerCells(w) {
    return Array.prototype.filter.call(w.querySelectorAll(".ag-header-cell[col-id]"), function (h) {
      return !h.classList.contains("ag-floating-filter") && !h.classList.contains("ag-header-group-cell") &&
             h.closest(".ag-root-wrapper") === w;
    });
  }
  function headerText(h) {
    var t = h.querySelector(".ag-header-cell-text") || h.querySelector(".ag-header-cell-label") || h;
    return norm(t.textContent);
  }
  function harvest(w, map, sc) {
    var hs = headerCells(w), base = sc ? sc.getBoundingClientRect().left : 0;
    for (var i = 0; i < hs.length; i++) {
      var id = hs[i].getAttribute("col-id"), r = hs[i].getBoundingClientRect();
      var info = map.get(id) || { colId: id };
      info.header = headerText(hs[i]);
      info.index = parseInt(hs[i].getAttribute("aria-colindex") || "0", 10) || info.index || null;
      info.section = sectionOf(hs[i]);
      if (sc && info.section === "center" && r.width) { info.cx = r.left - base + sc.scrollLeft; info.width = r.width; }
      map.set(id, info);
    }
  }
  function colCount(w) { var g = ariaGrid(w); return g ? parseInt(g.getAttribute("aria-colcount") || "0", 10) : 0; }
  function apiColumns(api) {
    var cols = (api.getAllDisplayedColumns && api.getAllDisplayedColumns()) || (api.getColumns && api.getColumns()) || [];
    return cols.map(function (c, i) {
      var name = api.getDisplayNameForColumn ? api.getDisplayNameForColumn(c, "header") : (c.getColDef().headerName || c.getColId());
      var p = c.getPinned ? c.getPinned() : null;
      return { colId: c.getColId(), header: norm(name), index: i + 1, section: p === "left" ? "left" : p === "right" ? "right" : "center" };
    });
  }
  var colCache = new WeakMap();
  async function columns(w, api, deadline, timings) {
    var t0 = now();
    if (api) {
      try { var ac = apiColumns(api); if (ac.length) { timings.columns = Math.round(now() - t0); return ac; } } catch (e) { /* fall back to DOM */ }
    }
    var sc = hScroller(w), map = new Map();
    harvest(w, map, sc);
    var total = colCount(w), cached = colCache.get(w);
    if (cached && cached.total === total && cached.list.length === total &&
        Array.from(map.values()).every(function (c) { return cached.list.some(function (k) { return k.colId === c.colId && k.index === c.index; }); })) {
      timings.columns = Math.round(now() - t0);
      return cached.list;
    }
    if (sc && !(total && map.size >= total)) {
      var start = sc.scrollLeft;
      sc.scrollLeft = 0;
      await settle(w, null, deadline);
      harvest(w, map, sc);
      for (var guard = 0; guard < 200 && now() < deadline && !(total && map.size >= total); guard++) {
        var max = sc.scrollWidth - sc.clientWidth;
        if (sc.scrollLeft >= max - 1) break;
        var box = centerBox(w, sc);
        sc.scrollLeft = Math.min(max, sc.scrollLeft + Math.max(40, Math.floor((box.right - box.left) * 0.85)));
        await settle(w, null, deadline);
        harvest(w, map, sc);
      }
      sc.scrollLeft = start;
      await settle(w, null, deadline);
    }
    var list = Array.from(map.values()).sort(function (a, b) { return (a.index || 1e9) - (b.index || 1e9); });
    colCache.set(w, { total: total, list: list });
    timings.columns = Math.round(now() - t0);
    return list;
  }
  function matchColumn(cols, q) {
    q = norm(q);
    var m = /^#(\d+)$/.exec(q);
    if (m) {
      var n = parseInt(m[1], 10);
      var byIdx = cols.filter(function (c) { return c.index === n; });
      return byIdx.length ? { col: byIdx[0], how: "#" + n } : { none: true };
    }
    var tiers = [
      ["header", function (c) { return c.header === q; }],
      ["col-id", function (c) { return c.colId === q; }],
      ["header", function (c) { return lower(c.header) === lower(q); }],
      ["col-id", function (c) { return lower(c.colId) === lower(q); }]
    ];
    for (var t = 0; t < tiers.length; t++) {
      var hits = cols.filter(tiers[t][1]);
      if (hits.length === 1) return { col: hits[0], how: tiers[t][0] };
      if (hits.length > 1) return { ambiguous: hits };
    }
    return { none: true };
  }
  function colLabel(c) { return "'" + (c.header || c.colId) + "' (col-id " + c.colId + (c.index ? ", #" + c.index : "") + (c.section !== "center" ? ", pinned " + c.section : "") + ")"; }

  // ------------------------------------------------------------------ finding rows
  function keyMatches(w, keyCol, key) {
    var out = [], seen = {};
    cellsByCol(w, keyCol).forEach(function (c) {
      if (norm(c.textContent) !== key) return;
      var i = rowIdx(rowOf(c));
      if (!seen[i]) { seen[i] = 1; out.push(i); }
    });
    return out;
  }
  function validApi(api) {
    return !!api && typeof api.forEachNode === "function" && !(api.isDestroyed && api.isDestroyed());
  }
  // Angular dev/QA builds expose window.ng; ng.getComponent(<ag-grid-angular>) is the AgGridAngular
  // component and its .api is the grid API. Used automatically when no api_expr is configured.
  function angularApi(w) {
    var ng = window.ng, host = w.closest("ag-grid-angular");
    if (!host || !ng || typeof ng.getComponent !== "function") return null;
    try { var cmp = ng.getComponent(host); return cmp && validApi(cmp.api) ? cmp.api : null; } catch (e) { return null; }
  }
  // `apis` holds values Playwright evaluated for each GridDef.api_expr (CSP-safe); eval is a fallback.
  function getApi(w, def, notes, apis, src) {
    if (def && def.apiExpr) {
      var api = null;
      if (apis && Object.prototype.hasOwnProperty.call(apis, def.name)) api = apis[def.name];
      else {
        try { api = (0, eval)(def.apiExpr); } catch (e) { notes.push("api_expr " + def.apiExpr + " failed: " + e.message); }
      }
      if (validApi(api)) { src.api = "api_expr"; return api; }
      notes.push("api_expr " + def.apiExpr + " did not yield an AG Grid API; trying Angular, then scrolling");
    }
    var ng = angularApi(w);
    if (ng) { src.api = "angular"; return ng; }
    return null;
  }
  function apiValue(api, node, colId) {
    var v;
    try {
      if (api.getCellValue) v = api.getCellValue({ rowNode: node, colKey: colId, useFormatter: true });
      else if (api.getValue) v = api.getValue(colId, node);
    } catch (e) { v = undefined; }
    if (v === undefined && node.data) v = node.data[colId];
    return v == null ? "" : norm(String(v));
  }
  async function scrollToIndexDom(w, idx, deadline) {
    var sc = vScroller(w);
    var check = function () { return rowsOf(w).some(function (r) { return rowIdx(r) === idx; }) ? true : null; };
    if (check() || !sc) return check();
    for (var guard = 0; guard < 40 && now() < deadline; guard++) {
      var rows = rowsOf(w);
      if (!rows.length) { await settle(w, sc, deadline); continue; }
      var near = rows[0], best = 1e9;
      rows.forEach(function (r) { var d = Math.abs(rowIdx(r) - idx); if (d < best) { best = d; near = r; } });
      var rr = near.getBoundingClientRect(), box = bodyBox(w, sc);
      var h = rr.height || 25;
      var targetTop = rr.top + (idx - rowIdx(near)) * h;      // estimated viewport y of the row
      sc.scrollTop = Math.max(0, sc.scrollTop + (targetTop - box.top) - (box.bottom - box.top - h) / 2);
      if (await settle(w, sc, deadline, check)) return true;
    }
    return check();
  }
  // Sweep the vertical scroller from the top in rendered-span steps until `check` finds something.
  // `seen` collects row indices whose key cell was rendered with text; before reporting "absent",
  // every hole in [0, last row] is revisited, so a fast settle can never cause a false "not found".
  async function sweepRows(w, deadline, check, keyCol, stats) {
    var sc = vScroller(w), seen = {};
    var mark = function () {
      // a row counts as seen once its key cell exists and the row has rendered content
      // (a blank key is still a seen row; a row whose cells have not rendered yet is not)
      cellsByCol(w, keyCol).forEach(function (c) { if (norm(c.textContent) || norm(rowOf(c).textContent)) seen[rowIdx(rowOf(c))] = 1; });
    };
    var chk = function () { mark(); return check(); };
    var hit = chk();
    if (hit || !sc) return hit;
    sc.scrollTop = 0;
    hit = await settle(w, sc, deadline, chk);
    var maxSeen = -1, h = 0;
    for (var guard = 0; !hit && guard < 5000 && now() < deadline; guard++) {
      var rows = rowsOf(w);
      var base = sc.getBoundingClientRect().top - sc.scrollTop;
      var minTop = 1e12, maxBottom = -1e12;
      rows.forEach(function (r) {
        var rr = r.getBoundingClientRect();
        if (!rr.height) return;
        minTop = Math.min(minTop, rr.top - base); maxBottom = Math.max(maxBottom, rr.bottom - base);
        maxSeen = Math.max(maxSeen, rowIdx(r)); h = h || rr.height;
      });
      if (sc.scrollTop >= sc.scrollHeight - sc.clientHeight - 1) break;
      var span = maxBottom > minTop ? maxBottom - minTop : sc.clientHeight;
      var before = sc.scrollTop, sigBefore = signature(w);
      sc.scrollTop = before + Math.max(h || 20, span - (h || 20));
      if (sc.scrollTop <= before) break;
      stats.steps++;
      hit = await settle(w, sc, deadline, chk, sigBefore);
    }
    // completeness: revisit holes (indices never seen with a rendered key cell)
    rowsOf(w).forEach(function (r) { maxSeen = Math.max(maxSeen, rowIdx(r)); });
    for (var i = 0; !hit && i <= maxSeen && now() < deadline; i++) {
      if (seen[i]) continue;
      stats.revisits++;
      await scrollToIndexDom(w, i, deadline);
      hit = await settle(w, sc, deadline, chk);
      if (!seen[i]) stats.unseen.push(i);
      seen[i] = 1;   // visited; do not loop on rows whose key cell is empty
    }
    stats.rowsSeen = maxSeen + 1;
    return hit || chk();
  }
  // Does the displayed key text equal the API's (formatted) value for the rendered rows? Only then
  // is an API "no such row" trusted; otherwise a renderer changes the text and the DOM decides.
  function apiMatchesDom(w, api, keyCol) {
    if (!api.getDisplayedRowAtIndex) return false;
    var cells = cellsByCol(w, keyCol);
    return cells.length > 0 && cells.every(function (c) {
      var n = api.getDisplayedRowAtIndex(rowIdx(rowOf(c)));
      return !!n && apiValue(api, n, keyCol) === norm(c.textContent);
    });
  }
  async function findRow(w, spec, keyCol, api, deadline, notes, stats) {
    if (spec.index != null) {
      var idx = spec.index - 1;
      if (api) { try { api.ensureIndexVisible(idx, "middle"); stats.api = true; } catch (e) { notes.push("ensureIndexVisible failed: " + e.message); } }
      var ok = await scrollToIndexDom(w, idx, deadline);
      if (!ok) return { error: "Row #" + spec.index + " is not in the grid (rows rendered: " + rowsOf(w).length + ")" };
      return { idx: idx };
    }
    var key = norm(spec.keyValue);
    if (api) {
      var nodes = [], filteredOut = 0;
      try {
        api.forEachNodeAfterFilterAndSort(function (n) { if (!n.group && apiValue(api, n, keyCol) === key) nodes.push(n); });
        if (!nodes.length) api.forEachNode(function (n) { if (!n.group && apiValue(api, n, keyCol) === key) filteredOut++; });
      } catch (e) { notes.push("API row search failed: " + e.message); nodes = null; }
      if (nodes) {
        stats.api = true;
        if (nodes.length > 1) return { error: "Key " + key + " matches " + nodes.length + " rows in column " + keyCol + "; the key column is not unique", alternatives: nodes.map(function (n) { return "row-index " + n.rowIndex; }) };
        if (!nodes.length && filteredOut) return { error: "Row " + keyCol + "=" + key + " exists but is hidden by the grid's filter" };
        if (!nodes.length && apiMatchesDom(w, api, keyCol)) return { error: "No row with " + keyCol + " = '" + key + "'" };
        if (!nodes.length) { notes.push("key column text differs from the API value (cell renderer?); scanning the DOM"); nodes = null; }
      }
      if (nodes) {
        var i2 = nodes[0].rowIndex;
        if (i2 == null) return { error: "Row " + keyCol + "=" + key + " is not displayed (collapsed group or filtered)" };
        var vis = function () { var m = keyMatches(w, keyCol, key); return m.indexOf(i2) >= 0 ? i2 + 1 : null; };
        if (!vis()) {
          api.ensureIndexVisible(i2, "middle");
          var vsc = vScroller(w);
          await settle(w, vsc, deadline, vis);
        }
        if (vis()) return { idx: i2 };
        notes.push("API located the row but it did not render; falling back to scrolling");
      }
    }
    var found = await sweepRows(w, deadline, function () { var m = keyMatches(w, keyCol, key); return m.length ? m : null; }, keyCol, stats);
    if (!found) {
      var filtered = w.querySelector(".ag-header-cell-filtered,.ag-filter-active");
      if (now() >= deadline) return { error: "Timed out searching for row " + keyCol + " = '" + key + "' after " + (stats.steps + 1) + " viewport(s)" };
      return { error: "No row with " + keyCol + " = '" + key + "' (scanned all " + stats.rowsSeen + " rows in " + (stats.steps + 1) + " viewport(s))" + (filtered ? "; the grid has an active filter" : ""),
               alternatives: stats.unseen.length ? ["row indices whose key cell never rendered text: " + stats.unseen.slice(0, 10).join(", ")] : [] };
    }
    if (found.length > 1) return { error: "Key " + key + " matches " + found.length + " rendered rows in column " + keyCol + "; the key column is not unique", alternatives: found.map(function (i) { return "row-index " + i; }) };
    notes.push("key uniqueness checked among rendered rows only (no api_expr)");
    return { idx: found[0] };
  }
  async function ensureCell(w, idx, col, api, deadline, stats) {
    var check = function () { return cellAt(w, idx, col.colId); };
    if (check()) return check();
    if (api && api.ensureColumnVisible) { try { api.ensureColumnVisible(col.colId); stats.api = true; } catch (e) { /* ignore */ } }
    var sc = hScroller(w);
    var hit = await settle(w, null, deadline, check);
    if (hit || !sc) return hit;
    if (col.cx != null) {
      var box = centerBox(w, sc), base = sc.getBoundingClientRect().left;
      sc.scrollLeft = col.cx - (box.left - base) - Math.max(0, (box.right - box.left - (col.width || 0)) / 2);
      hit = await settle(w, null, deadline, check);
      if (hit) return hit;
    }
    sc.scrollLeft = 0;
    hit = await settle(w, null, deadline, check);
    for (var guard = 0; !hit && guard < 200 && now() < deadline; guard++) {
      var max = sc.scrollWidth - sc.clientWidth;
      if (sc.scrollLeft >= max - 1) break;
      var b = centerBox(w, sc);
      sc.scrollLeft = Math.min(max, sc.scrollLeft + Math.max(40, Math.floor((b.right - b.left) * 0.85)));
      stats.hsteps++;
      hit = await settle(w, null, deadline, check);
    }
    return hit || check();
  }

  // Scroll so that `el` (a cell or something inside one) is fully inside the visible grid body.
  async function reveal(el, timeoutMs) {
    var deadline = now() + (timeoutMs || 3000);
    var w = el.closest(".ag-root-wrapper");
    if (!w) { el.scrollIntoView({ block: "center", inline: "center" }); return true; }
    var cell = el.closest("[col-id]") || el;
    var row = rowOf(cell), idx = row ? rowIdx(row) : null, colId = cell.getAttribute("col-id");
    var cur = function () { return idx == null ? el : (el.isConnected ? el : cellAt(w, idx, colId)); };
    var vs = vScroller(w), hs = hScroller(w);
    // make the grid itself visible in the page first
    var gr = (ariaGrid(w) || w).getBoundingClientRect();
    if (gr.top < 0 || gr.bottom > innerHeight) (vs || w).scrollIntoView({ block: "nearest" });
    var c = cur(), r = c.getBoundingClientRect();
    if (vs) {
      var box = bodyBox(w, vs);
      if (r.top < box.top || r.bottom > box.bottom) vs.scrollTop += (r.top - box.top) - Math.max(0, (box.bottom - box.top - r.height) / 2);
    }
    if (hs && sectionOf(c) === "center") {
      var cb = centerBox(w, hs);
      if (r.left < cb.left || r.right > cb.right) hs.scrollLeft += (r.left - cb.left) - Math.max(0, (cb.right - cb.left - r.width) / 2);
    }
    await settle(w, vs, deadline, null);
    return !!cur();
  }

  // ------------------------------------------------------------------ locator templates
  function anchorsFor(w, js) {
    var out = [], ge = ariaGrid(w);
    var badId = rx(js.unstableIdPatterns), okId = rx(js.stableIdAllow), badText = rx(js.unstableTextPatterns);
    var badClass = rx(js.unstableClassPatterns);
    var stableId = function (id) { return any(okId, id) || !any(badId, id); };
    hostChain(w).forEach(function (e) {
      var tag = e.tagName.toLowerCase();
      (js.testIdAttrs || []).forEach(function (a) {
        var v = e.getAttribute(a);
        if (v && stableId(v)) out.push({ xp: "//" + tag + "[@" + a + "=" + lit(v) + "]", strategy: "test-id", rank: 0 });
      });
      if (e.id && stableId(e.id)) out.push({ xp: "//" + tag + "[@id=" + lit(e.id) + "]", strategy: "id", rank: 1 });
      var al = e.getAttribute("aria-label");
      if (al && norm(al) && !any(badText, al)) out.push({ xp: "//" + tag + "[@aria-label=" + lit(al) + "]", strategy: "aria-label", rank: 2 });
      // dev-set classes on the host (e.g. <ag-grid-angular class="orders-grid">); AG Grid's own
      // ag-* classes (themes, layout) are shared by every grid and never used
      Array.prototype.forEach.call(e.classList || [], function (cl) {
        // the class alone must identify this element (a shared class such as "grid-host" that is
        // only unique together with the tag name would break as soon as another grid appears)
        if (/^ag-/.test(cl) || any(badClass, cl) || document.getElementsByClassName(cl).length !== 1) return;
        out.push({ xp: "//" + tag + "[contains(concat(' ', normalize-space(@class), ' '), " + lit(" " + cl + " ") + ")]", strategy: "class", rank: 3 });
      });
    });
    var h = headingFor(w);
    if (h && ge) {
      var t = norm(h.textContent);
      var hx = /^h[1-6]$/i.test(h.tagName) ? "//" + h.tagName.toLowerCase() : "//*[@role='heading']";
      if (t && !any(badText, t)) out.push({ xp: hx + "[normalize-space()=" + lit(t) + "]/following::*" + gridPred(ge) + "[1]", strategy: "heading", rank: 3 });
    }
    return out.filter(function (a) {
      var m = xpSafe(a.xp);
      if (!m || m.length !== 1) return false;
      var el = m[0];
      if (!(el === ge || el.contains(ge || w))) return false;          // must contain all of this grid's rows
      return allGrids().filter(function (o) { return el.contains(o) || o.contains(el); }).length === 1;
    }).sort(function (a, b) { return a.rank - b.rank || a.xp.length - b.xp.length; });
  }
  var TOKEN = "{__KEY__}";
  // Template shapes. `*` steps: only AG rows carry @row-index and only cells/headers carry @col-id.
  //  key           G//*[@row-index]//*[@col-id=K][normalize-space()=P]                   (target is the key cell)
  //  row           G//*[@row-index][.//*[@col-id=K][normalize-space()=P]]//*[@col-id=C]   (key+target in one row element:
  //                always in the unified v36+ layout; in the split layout only within one pinned section)
  //  join          G//*[@row-index][@row-index=G//*[@col-id=K][normalize-space()=P]/ancestor::*/@row-index]//*[@col-id=C]
  //  join-ancestor same, inner part scoped by ancestor::*[@aria-rowcount] (the grid) instead of repeating G
  //  index         G//*[@row-index=P-1]//*[@col-id=C]                                     (fragile fallback)
  // join/join-ancestor/index work in both layouts: they join the split row parts by @row-index at runtime.
  function forms(G, gridStep, K, C, isIndex) {
    var c = "//*[@col-id=" + lit(C) + "]";
    if (isIndex) return [{ name: "index", xp: G + "//*[@row-index=" + TOKEN + "-1]" + c }];
    var k = "//*[@col-id=" + lit(K) + "][normalize-space()=" + TOKEN + "]";
    var out = [];
    if (K === C) out.push({ name: "key", xp: G + "//*[@row-index]" + k });
    out.push({ name: "row", xp: G + "//*[@row-index][." + k + "]" + c });
    // [@row-index] first: XPath engines evaluate the join's inner path per candidate node, so
    // filter to row elements before joining (O(rows) instead of O(all grid descendants)).
    out.push({ name: "join", xp: G + "//*[@row-index][@row-index=" + G + k + "/ancestor::*/@row-index]" + c });
    out.push({ name: "join-ancestor", xp: G + "//*[@row-index][@row-index=ancestor::*" + gridStep + k + "/ancestor::*/@row-index]" + c });
    // only offered when the app's getRowId returns the key itself (checked by the caller); ranked
    // after the key-text forms, so it is primary only when those cannot work (key cell not rendered
    // together with the target because of column virtualization).
    out.push({ name: "row-id", xp: G + "//*[@row-id=" + TOKEN + "]" + c, penalty: 1, needsRowIdKey: true });
    return out;
  }
  // true when every rendered row's row-id equals its key cell text (app getRowId returns the key)
  function rowIdIsKey(w, keyCol, api) {
    var cells = cellsByCol(w, keyCol);
    if (!cells.length) return false;
    var ok = cells.every(function (c) { return rowOf(c).getAttribute("row-id") === norm(c.textContent); });
    if (ok && api) {
      try { api.forEachNode(function (n) { if (ok && !n.group && String(n.id) !== apiValue(api, n, keyCol)) ok = false; }); } catch (e) { /* DOM check stands */ }
    }
    return ok;
  }
  function controlSteps(ctl, cell) {
    var tag = ctl.tagName.toLowerCase(), steps = [];
    var txt = norm(ctl.textContent), al = ctl.getAttribute("aria-label"), ti = ctl.getAttribute("title");
    if (txt) steps.push("//" + tag + "[normalize-space()=" + lit(txt) + "]");
    if (al) steps.push("//" + tag + "[@aria-label=" + lit(al) + "]");
    if (ti) steps.push("//" + tag + "[@title=" + lit(ti) + "]");
    if (tag === "input" && ctl.value) steps.push("//input[@value=" + lit(ctl.value) + "]");
    return steps.filter(function (s) {
      var m = xpAll("." + s, cell);
      return m.length === 1 && m[0] === ctl;
    }).sort(function (a, b) { return a.length - b.length; });
  }
  function buildTemplates(w, ctx) {
    var js = ctx.js || {}, anchors = anchorsFor(w, js), ge = ariaGrid(w);
    var role = gridPred(ge);
    var target = ctx.target, isIndex = ctx.isIndex;
    // proof rows: other rendered rows where both key and target cells are rendered
    var proofs = [];
    if (!isIndex) {
      var counts = {};
      cellsByCol(w, ctx.keyCol).forEach(function (c) { var t = norm(c.textContent); counts[t] = (counts[t] || 0) + 1; });
      cellsByCol(w, ctx.keyCol).forEach(function (c) {
        if (proofs.length >= 2) return;
        var t = norm(c.textContent), i = rowIdx(rowOf(c));
        if (!t || counts[t] !== 1 || t === ctx.key || i === ctx.idx) return;
        var tc = cellAt(w, i, ctx.colId);
        if (!tc) return;
        var tt = tc;
        if (ctx.controlStep) { var m = xpAll("." + ctx.controlStep, tc); if (m.length !== 1) return; tt = m[0]; }
        proofs.push({ key: t, el: tt });
      });
    } else {
      rowsOf(w).forEach(function (r) {
        var i = rowIdx(r);
        if (proofs.length >= 2 || i === ctx.idx) return;
        var tc = cellAt(w, i, ctx.colId);
        if (!tc) return;
        var tt = tc;
        if (ctx.controlStep) { var m = xpAll("." + ctx.controlStep, tc); if (m.length !== 1) return; tt = m[0]; }
        proofs.push({ key: String(i + 1), el: tt });
      });
    }
    var valid = [], tried = [], idKey = !isIndex && rowIdIsKey(w, ctx.keyCol, ctx.api);
    anchors.forEach(function (a) {
      if (valid.length >= 3) return;   // enough verified alternatives; skip weaker anchors
      forms(a.xp, role, ctx.keyCol, ctx.colId, isIndex).forEach(function (f) {
        if (f.needsRowIdKey && !idKey) return;
        var tpl = f.xp + (ctx.controlStep || "");
        var concrete = tpl.split(TOKEN).join(lit(ctx.paramValue));
        var m = xpSafe(concrete);
        var ok = !!m && m.length === 1 && m[0] === target;
        var proved = 0;
        if (ok) {
          proofs.forEach(function (p) {
            var pm = xpSafe(tpl.split(TOKEN).join(lit(p.key)));
            if (pm && pm.length === 1 && pm[0] === p.el) proved++; else ok = false;
          });
        }
        tried.push({ form: f.name, anchor: a.strategy, ok: ok, matched: m ? m.length : -1 });
        if (ok) valid.push({ template: tpl, xpath: concrete, form: f.name, anchor: a.strategy, rank: a.rank, penalty: f.penalty || 0, proofs: proved });
      });
    });
    valid.sort(function (a, b) { return a.rank - b.rank || a.penalty - b.penalty || a.template.length - b.template.length; });
    return { valid: valid, tried: tried, anchors: anchors.map(function (a) { return a.xp; }), proofRows: proofs.length };
  }

  // ------------------------------------------------------------------ controls inside cells
  var CONTROL_SEL = "button,a[href],[role=button],[role=link],input[type=button],input[type=submit]";
  function controlText(c) { return norm(c.textContent) || norm(c.getAttribute("aria-label")) || norm(c.getAttribute("title")) || norm(c.value); }
  function findControl(scope, text) {
    var els = Array.prototype.slice.call(scope.querySelectorAll(CONTROL_SEL));
    var ex = els.filter(function (c) { return controlText(c) === norm(text) || norm(c.getAttribute("aria-label")) === norm(text) || norm(c.getAttribute("title")) === norm(text); });
    if (!ex.length) ex = els.filter(function (c) { return lower(controlText(c)) === lower(text); });
    return ex;
  }

  // ------------------------------------------------------------------ main entry
  async function resolve(spec, apis) {
    var t0 = now(), deadline = t0 + (spec.timeoutMs || 10000), frames0 = frames;
    var timings = {}, notes = [], stats = { steps: 0, hsteps: 0, revisits: 0, unseen: [], rowsSeen: 0, api: false };
    var fail = function (error, alternatives, extra) {
      timings.frames = frames - frames0;
      var r = { ok: false, error: error, alternatives: alternatives || [], timings: timings, notes: notes };
      for (var k in extra || {}) r[k] = extra[k];
      return { result: r, el: null };
    };
    // 1. grid
    var g;
    for (;;) {
      g = findGrid(spec);
      if (g.w || !g.retry || now() > deadline) break;
      await frame();
    }
    if (!g.w) return fail(g.error, g.alternatives);
    var w = g.w, def = g.def, desc = describe(w);
    // 2. ready: loading overlay gone and rows (or the no-rows overlay) present
    var quiet = 0;
    while (now() < deadline) {
      if (!w.isConnected) return fail("Grid was removed from the page while waiting", [], { retry: true });
      if (!loading(w)) { if (rowsOf(w).length || noRows(w) || ++quiet > 30) break; } else quiet = 0;
      await frame();
    }
    if (loading(w)) return fail("Grid " + gridTitle(desc) + " still shows its loading overlay after " + spec.timeoutMs + " ms");
    timings.ready = Math.round(now() - t0);
    var apiSrc = { api: null };
    var api = getApi(w, def, notes, apis, apiSrc);
    // 3. columns
    var cols = await columns(w, api, deadline, timings);
    var colSpec = spec.column, control = spec.control || null, mc = matchColumn(cols, colSpec);
    if (mc.none && !control && colSpec.indexOf(">") > 0) {
      var parts = colSpec.split(">");
      var mc2 = matchColumn(cols, parts[0]);
      if (!mc2.none) { mc = mc2; control = norm(parts.slice(1).join(">")); colSpec = norm(parts[0]); }
    }
    if (mc.ambiguous) return fail("Column '" + colSpec + "' is ambiguous", mc.ambiguous.map(colLabel));
    var col = mc.col || null;
    if (!col && !control) control = norm(colSpec);   // maybe a control inside a cell ("Edit")
    // key column
    var keyCol = null, keyHow = "";
    var keySpec = spec.keyColumn || (def && def.keyColumn) || null;
    if (spec.index == null) {
      if (keySpec) {
        var mk = matchColumn(cols, keySpec);
        if (mk.ambiguous) return fail("Key column '" + keySpec + "' is ambiguous", mk.ambiguous.map(colLabel));
        if (mk.none) return fail("Key column '" + keySpec + "' not found in grid " + gridTitle(desc), cols.map(colLabel));
        keyCol = mk.col; keyHow = spec.keyColumn ? "row.key_column" : "grid key_column";
      } else {
        keyCol = cols.filter(function (c) { return c.section === "left"; })[0] || cols[0];
        keyHow = keyCol && keyCol.section === "left" ? "first pinned-left column" : "first column";
      }
      if (!keyCol) return fail("Grid " + gridTitle(desc) + " has no columns");
    }
    if (!col && !control) return fail("No column '" + colSpec + "' in grid " + gridTitle(desc), cols.map(colLabel));
    // 4.+5. row, then cell (or control inside a cell). Re-validated once the grid is quiet:
    // if the element was replaced (row animation, re-render) the lookup runs again.
    var t1 = now(), t2 = t1, idx = null, cell = null, ctl = null, col0 = col;
    for (var attempt = 0; attempt < 4; attempt++) {
      col = col0; cell = null; ctl = null;
      var fr = await findRow(w, spec, keyCol ? keyCol.colId : null, api, deadline, notes, stats);
      timings.row = Math.round(now() - t1);
      if (fr.error) return fail(fr.error, fr.alternatives || []);
      idx = fr.idx;
      t2 = now();
      if (col) {
        cell = await ensureCell(w, idx, col, api, deadline, stats);
        if (!cell) return fail("Row found (row-index " + idx + ") but column " + colLabel(col) + " did not render");
        if (control) {
          var cs = findControl(cell, control);
          if (cs.length !== 1) return fail(cs.length ? "'" + control + "' matches " + cs.length + " controls in that cell" : "No control '" + control + "' in column " + colLabel(col), cs.map(function (c) { return c.outerHTML.slice(0, 80); }));
          ctl = cs[0];
        }
      } else {
        // control text only: search the row's cells, sweeping columns if needed
        var findCtl = function () {
          var hits = [];
          rowsOf(w).filter(function (r) { return rowIdx(r) === idx; }).forEach(function (r) {
            findControl(r, control).forEach(function (c) { if (c.closest("[col-id]") && hits.indexOf(c) < 0) hits.push(c); });
          });
          return hits.length ? hits : null;
        };
        var hits = findCtl();
        if (!hits) {
          var hsc = hScroller(w);
          if (hsc) {
            hsc.scrollLeft = 0; hits = await settle(w, null, deadline, findCtl);
            for (var gd = 0; !hits && gd < 200 && now() < deadline; gd++) {
              var mx = hsc.scrollWidth - hsc.clientWidth;
              if (hsc.scrollLeft >= mx - 1) break;
              var bx = centerBox(w, hsc);
              hsc.scrollLeft = Math.min(mx, hsc.scrollLeft + Math.max(40, Math.floor((bx.right - bx.left) * 0.85)));
              hits = await settle(w, null, deadline, findCtl);
            }
          }
        }
        if (!hits) return fail("No column or in-row control named '" + colSpec + "' in grid " + gridTitle(desc), cols.map(colLabel));
        if (hits.length > 1) return fail("'" + control + "' matches " + hits.length + " controls in that row", hits.map(function (c) { return c.closest("[col-id]").getAttribute("col-id") + ": " + c.outerHTML.slice(0, 80); }));
        ctl = hits[0];
        cell = ctl.closest("[col-id]");
        var cid = cell.getAttribute("col-id");
        col = cols.filter(function (c) { return c.colId === cid; })[0] || { colId: cid, header: cid, section: sectionOf(cell) };
      }
      await settle(w, vScroller(w), deadline, null);
      var still = cell.isConnected && rowOf(cell) && rowIdx(rowOf(cell)) === idx && (!ctl || ctl.isConnected) &&
                  (spec.index != null || keyMatches(w, keyCol.colId, norm(spec.keyValue)).indexOf(idx) >= 0);
      if (still) break;
      notes.push("cell was re-rendered while resolving (row animation?); looked it up again");
      if (attempt === 3) return fail("Grid kept re-rendering the row while resolving it");
    }
    timings.cell = Math.round(now() - t2);
    var row = rowOf(cell);
    var base = {
      ok: true, grid: gridTitle(desc), gridName: desc.label || desc.heading || null, gridHow: g.how, gridDef: def ? def.name : null,
      layout: layoutOf(w), rowIndex: idx, rowId: row ? row.getAttribute("row-id") : null,
      colId: col.colId, colHeader: col.header || col.colId, colSection: col.section || sectionOf(cell),
      keyColId: keyCol ? keyCol.colId : null, keyHeader: keyCol ? (keyCol.header || keyCol.colId) : null, keyHow: keyHow,
      keyValue: spec.index == null ? norm(spec.keyValue) : null, index: spec.index,
      control: control, controlTag: ctl ? ctl.tagName.toLowerCase() : null,
      apiUsed: stats.api, apiSource: stats.api ? apiSrc.api : null, scrollSteps: stats.steps, hScrollSteps: stats.hsteps, revisits: stats.revisits, gridCount: allGrids().length,
      timings: timings, notes: notes
    };
    if (spec.noTemplates) { timings.total = Math.round(now() - t0); timings.frames = frames - frames0; return { result: base, el: ctl || cell }; }
    // 6. templates
    var t3 = now(), controlStep = null;
    if (ctl) {
      var steps = controlSteps(ctl, cell);
      if (!steps.length) return fail("Cannot build a stable locator for control '" + control + "' inside the cell");
      controlStep = steps[0];
    }
    var tb = buildTemplates(w, {
      js: spec.js, api: api, target: ctl || cell, keyCol: keyCol ? keyCol.colId : null, colId: col.colId,
      key: base.keyValue, idx: idx, isIndex: spec.index != null, controlStep: controlStep,
      paramValue: spec.index != null ? String(spec.index) : base.keyValue
    });
    timings.templates = Math.round(now() - t3);
    timings.total = Math.round(now() - t0);
    timings.frames = frames - frames0;
    if (!tb.valid.length) return fail("No verified grid locator for this cell", tb.tried.map(function (t) { return t.anchor + "/" + t.form + ": matched " + t.matched; }), { anchors: tb.anchors });
    base.templates = tb.valid; base.token = TOKEN; base.anchors = tb.anchors; base.proofRows = tb.proofRows;
    return { result: base, el: ctl || cell };
  }

  // ------------------------------------------------------------------ editing helpers (used by act_on_cell)
  function cellOf(el) { return el.closest("[col-id]"); }
  function editState(cell) {
    if (cell.classList.contains("ag-cell-inline-editing")) return "inline";
    if (cell.classList.contains("ag-cell-popup-editing")) return "popup";
    return null;
  }
  function editorRoot(cell) {
    var st = editState(cell);
    if (st === "inline") return cell;
    if (st === "popup") {
      var id = cell.getAttribute("aria-owns");
      var p = id ? document.getElementById(id) : null;
      if (p) return p;
      var ps = Array.prototype.filter.call(document.querySelectorAll(".ag-popup-editor"), visible);
      return ps[ps.length - 1] || null;
    }
    return null;
  }
  function editorKind(root) {
    if (!root) return null;
    if (root.querySelector("select")) return "native-select";
    if (root.querySelector("[role=combobox],.ag-picker-field-wrapper")) return "picker";
    if (root.querySelector("textarea,input:not([type=checkbox]):not([type=radio]):not([type=hidden])")) return "text";
    if (root.querySelector("input[type=checkbox],[role=checkbox]")) return "checkbox";
    return "unknown";
  }
  function checkboxOf(cell) { return cell.querySelector("input[type=checkbox],[role=checkbox]"); }
  function checkboxState(cb) {
    if (!cb) return null;
    if (cb.tagName === "INPUT") return { checked: cb.checked, indeterminate: !!cb.indeterminate, disabled: cb.disabled };
    return { checked: cb.getAttribute("aria-checked") === "true", indeterminate: cb.getAttribute("aria-checked") === "mixed", disabled: cb.getAttribute("aria-disabled") === "true" };
  }
  function cellInfo(el) {
    var cell = cellOf(el) || el;
    var root = editorRoot(cell);
    return {
      colId: cell.getAttribute("col-id"), rowIndex: rowOf(cell) ? rowIdx(rowOf(cell)) : null,
      editing: editState(cell), editor: editorKind(root), text: norm(cell.textContent),
      checkbox: checkboxState(checkboxOf(cell))
    };
  }
  function listOptions() {  // visible option lists of AG select/rich-select editors
    var lists = Array.prototype.filter.call(document.querySelectorAll(".ag-select-list,.ag-rich-select-list,.ag-rich-select,[role=listbox]"), visible);
    var opts = [];
    lists.forEach(function (l) {
      Array.prototype.forEach.call(l.querySelectorAll("[role=option],.ag-list-item,.ag-rich-select-row"), function (o) { if (visible(o) && opts.indexOf(o) < 0) opts.push(o); });
    });
    return opts;
  }
  function findOption(text) {
    var opts = listOptions();
    var ex = opts.filter(function (o) { return norm(o.textContent) === norm(text); });
    if (!ex.length) ex = opts.filter(function (o) { return lower(o.textContent) === lower(text); });
    return { option: ex.length === 1 ? ex[0] : null, count: ex.length, options: opts.map(function (o) { return norm(o.textContent); }) };
  }
  async function scrollOptions(text, timeoutMs) {   // virtual option lists: scroll until the option renders
    var deadline = now() + (timeoutMs || 2000);
    var lists = Array.prototype.filter.call(document.querySelectorAll(".ag-select-list,.ag-rich-select-list,[role=listbox]"), visible);
    for (var i = 0; i < lists.length; i++) {
      var sc = nearestScroller(lists[i].querySelector("[role=option],.ag-list-item") || lists[i], { parentElement: document.documentElement }, "y") || lists[i];
      sc.scrollTop = 0;
      for (var g = 0; g < 200 && now() < deadline; g++) {
        await frame(); await frame();
        if (findOption(text).option) return true;
        if (sc.scrollTop >= sc.scrollHeight - sc.clientHeight - 1) break;
        sc.scrollTop += Math.max(20, sc.clientHeight * 0.8);
      }
    }
    return !!findOption(text).option;
  }

  // ------------------------------------------------------------------ grid summary (crawler.author)
  // The grid as a whole: every column (scrolled-out ones included), the displayed row count and the
  // verified anchors of its root. `el` is the grid's .ag-root-wrapper.
  async function info(spec, apis) {
    var t0 = now(), deadline = t0 + (spec.timeoutMs || 10000), timings = {}, notes = [];
    var g;
    for (;;) {
      g = findGrid(spec);
      if (g.w || !g.retry || now() > deadline) break;
      await frame();
    }
    if (!g.w) return { result: { ok: false, error: g.error, alternatives: g.alternatives || [] }, el: null };
    var w = g.w, desc = describe(w), quiet = 0;
    while (now() < deadline) {
      if (!w.isConnected) return { result: { ok: false, retry: true, error: "Grid was removed from the page while waiting" }, el: null };
      if (!loading(w)) { if (rowsOf(w).length || noRows(w) || ++quiet > 30) break; } else quiet = 0;
      await frame();
    }
    var apiSrc = { api: null }, api = getApi(w, g.def, notes, apis, apiSrc);
    var cols = await columns(w, api, deadline, timings);
    var count = null;
    if (api && api.getDisplayedRowCount) { try { count = api.getDisplayedRowCount(); } catch (e) { count = null; } }
    if (count == null) {   // aria-rowcount counts the header rows too
      var ge = ariaGrid(w), rc = ge ? parseInt(ge.getAttribute("aria-rowcount") || "", 10) : NaN, hdr = {};
      Array.prototype.forEach.call(w.querySelectorAll(".ag-header [role=row][aria-rowindex]"), function (r) { hdr[r.getAttribute("aria-rowindex")] = 1; });
      if (!isNaN(rc) && rc >= 0) count = Math.max(0, rc - Object.keys(hdr).length);
    }
    var grids = allGrids();
    return { result: {
      ok: true, grid: gridTitle(desc), gridName: desc.label || desc.heading || null, gridHow: g.how,
      gridDef: g.def ? g.def.name : null, layout: layoutOf(w), apiSource: apiSrc.api,
      columns: cols.map(function (c) { return { colId: c.colId, header: c.header || "", index: c.index || null, section: c.section || "center" }; }),
      rowCount: count, gridCount: grids.length, grids: listing(grids), notes: notes,
      anchors: anchorsFor(w, spec.js || {}).map(function (a) { return { xpath: a.xp, strategy: a.strategy }; }),
      timings: { total: Math.round(now() - t0), columns: timings.columns }
    }, el: w };
  }
  // the grid's root: the wrapper, its ARIA grid, or a host/container holding no other grid
  function isRoot(el, w) { return el === w || el === ariaGrid(w) || hostChain(w).indexOf(el) >= 0; }
  function rootCheck(items, w) {
    return items.map(function (it) {
      var nodes;
      try { nodes = it[0] === "css" ? Array.prototype.slice.call(document.querySelectorAll(it[1])) : xpAll(it[1]); }
      catch (e) { return { n: -1, same: false, any: false }; }
      return { n: nodes.length, same: nodes.length === 1 && isRoot(nodes[0], w), any: nodes.some(function (x) { return isRoot(x, w); }) };
    });
  }

  window.__crawlerGrid = {
    VERSION: VERSION, resolve: resolve, reveal: reveal, cellInfo: cellInfo, editorRoot: editorRoot,
    editorKind: editorKind, findOption: findOption, scrollOptions: scrollOptions, lit: lit, norm: norm,
    info: info, rootCheck: rootCheck,
    listGrids: function () { return allGrids().map(function (w) { return gridTitle(describe(w)); }); }
  };
})();
