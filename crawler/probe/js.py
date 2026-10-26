"""In-page scripts for the probe (kept as Python strings so they ship with the package).

INIT_JS runs at document start in the main frame: it tracks fetch/XHR in flight, the time of the
last DOM mutation, and spinner-like elements that appear (so we can tell later which ones went away).
COLLECT_JS returns RAW page facts to Python, which classifies them; raw strings never reach the output.
"""

INIT_JS = r"""
(() => {
  if (window.top !== window || window.__probe) return;
  const now = () => performance.now();
  const P = { lastMut: now(), inflight: 0, lastNet: now(), watch: [], seen: new WeakSet(), animChecks: 0 };
  try { Object.defineProperty(window, '__probe', { value: P, enumerable: false, configurable: true }); } catch (e) { return; }
  const netStart = () => { P.inflight++; P.lastNet = now(); };
  const netEnd = () => { P.inflight = Math.max(0, P.inflight - 1); P.lastNet = now(); };
  try {
    const of = window.fetch;
    if (typeof of === 'function') {
      window.fetch = function () {
        netStart(); let r;
        try { r = of.apply(this, arguments); } catch (e) { netEnd(); throw e; }
        Promise.resolve(r).then(netEnd, netEnd); return r;
      };
    }
    const XS = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function () {
      netStart(); this.addEventListener('loadend', netEnd, { once: true });
      try { return XS.apply(this, arguments); } catch (e) { netEnd(); throw e; }
    };
  } catch (e) { /* ignore */ }

  const SAFE = ['spinner', 'loader', 'loading', 'progress', 'skeleton', 'overlay', 'busy', 'spin'];
  function classWords(tok) {
    const out = [];
    for (const w of tok.split(/[-_]+|(?<=[a-z])(?=[A-Z])/)) {
      const lw = w.toLowerCase();
      for (const s of SAFE) {
        if (lw === s || lw === s + 's' || (s === 'spin' && lw === 'spinning')) { out.push(w.substr(0, s.length)); break; }
      }
    }
    return out;
  }
  function sig(el) {
    const out = [];
    if (!el.getAttribute) return out;
    if (el.getAttribute('role') === 'progressbar') out.push(['role-progressbar', null]);
    if (el.getAttribute('aria-busy') === 'true') out.push(['aria-busy', null]);
    const cls = el.getAttribute('class') || '';
    for (const tok of cls.split(/\s+/)) {
      if (!tok) continue;
      if (/^ag-overlay-loading/.test(tok)) { out.push(['ag-overlay-loading', null]); continue; }
      if (/^ag-/.test(tok)) continue;
      for (const w of classWords(tok)) out.push(['class-word', w]);
    }
    return out;
  }
  function consider(el, busyOnly) {
    if (P.watch.length >= 300 || P.seen.has(el)) return;
    let s = busyOnly ? [['aria-busy', null]] : sig(el);
    if (!s.length && !busyOnly && P.animChecks < 300 && el.childElementCount <= 5) {
      P.animChecks++;
      try {
        const cs = getComputedStyle(el);
        const svg = el.querySelector && el.querySelector('svg, svg *');
        const anim = (cs.animationName && cs.animationName !== 'none') ||
          (svg && getComputedStyle(svg).animationName !== 'none');
        if (anim) s = [['animated-custom', null]];
      } catch (e) { /* ignore */ }
    }
    if (!s.length) return;
    P.seen.add(el);
    P.watch.push({ el, sig: s, busy: busyOnly || s.some(x => x[0] === 'aria-busy') && s.length === 1 });
  }
  const CAND = "[role=progressbar],[aria-busy=true],[class*=pinner],[class*=oader],[class*=oading],[class*=rogress]," +
    "[class*=keleton],[class*=verlay],[class*=usy],[class*=Spin],[class*=spin]";
  const mo = new MutationObserver(muts => {
    P.lastMut = now();
    for (const m of muts) {
      if (m.type === 'childList') {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          consider(n, false);
          if (P.watch.length < 300 && n.firstElementChild) {
            try { for (const c of n.querySelectorAll(CAND)) consider(c, false); } catch (e) { /* ignore */ }
          }
        }
      } else if (m.type === 'attributes' && m.attributeName === 'aria-busy' && m.target.getAttribute('aria-busy') === 'true') {
        consider(m.target, true);
      }
    }
  });
  mo.observe(document, { childList: true, subtree: true, attributes: true, characterData: true });
})();
"""

_VIS = r"""
const vis = el => {
  try { if (el.checkVisibility) return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }); } catch (e) {}
  return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
};
"""

READY_JS = r"""
(a) => {
""" + _VIS + r"""
  if (document.readyState !== 'complete') return false;
  const P = window.__probe; const t = performance.now();
  if (P) {
    if (P.inflight > 0 || t - P.lastNet < a.quiet) return false;
    if (t - P.lastMut < a.quiet) return false;
  }
  if (a.sel) {
    try { for (const el of document.querySelectorAll(a.sel)) if (vis(el)) return false; } catch (e) { /* bad selector */ }
  }
  return true;
}
"""

REASON_JS = r"""
(a) => {
""" + _VIS + r"""
  if (document.readyState !== 'complete') return 'readystate';
  const P = window.__probe; const t = performance.now();
  if (P && (P.inflight > 0 || t - P.lastNet < a.quiet)) return 'network';
  if (a.sel) {
    try { for (const el of document.querySelectorAll(a.sel)) if (vis(el)) return 'spinner'; } catch (e) {}
  }
  if (P && t - P.lastMut < a.quiet) return 'mutations';
  return 'none';
}
"""

IDCLASS_JS = r"""
() => {
  const ids = [], classes = new Set();
  for (const el of document.querySelectorAll('*')) {
    if (el.id && ids.length < 5000) ids.push(el.id);
    const c = el.getAttribute('class');
    if (c && classes.size < 5000) for (const t of c.split(/\s+/)) if (t) classes.add(t);
  }
  return { ids, classes: [...classes] };
}
"""

COLLECT_JS = r"""
(cfg) => {
""" + _VIS + r"""
  const sentinels = new Set();
  const addS = (s) => {
    if (s == null || sentinels.size >= cfg.maxStrings) return;
    s = String(s).replace(/\s+/g, ' ').trim();
    if (s.length >= 3 && s.length <= 300) sentinels.add(s);
  };
  const inc = (o, k, n) => { o[k] = (o[k] || 0) + (n == null ? 1 : n); };

  // ---- walk every element, including open shadow roots
  const all = []; let shadowOpen = 0, inShadow = 0, customEls = 0;
  const walk = (root, shadow) => {
    for (const el of root.querySelectorAll('*')) {
      all.push(el); if (shadow) inShadow++;
      if (el.tagName.includes('-')) customEls++;
      if (el.shadowRoot) { shadowOpen++; walk(el.shadowRoot, true); }
    }
  };
  walk(document, false);

  const ROLE_I = new Set(['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio',
    'option', 'switch', 'textbox', 'combobox', 'searchbox', 'slider', 'spinbutton', 'treeitem']);
  const ikind = (el) => {
    const t = el.tagName;
    if (t === 'BUTTON') return 'button';
    if (t === 'INPUT') {
      const ty = (el.getAttribute('type') || 'text').toLowerCase();
      if (ty === 'hidden') return null;
      return ['button', 'submit', 'reset', 'image'].includes(ty) ? 'button' : 'input';
    }
    if (t === 'SELECT') return 'select';
    if (t === 'TEXTAREA') return 'textarea';
    if ((t === 'A' || t === 'a' || t === 'AREA') && el.hasAttribute('href')) return 'link';
    const role = el.getAttribute('role');
    if (role === 'gridcell' || (el.classList && el.classList.contains('ag-cell'))) return 'gridcell';
    if (role && ROLE_I.has(role)) return 'role';
    if (el.getAttribute('contenteditable') === 'true' || el.getAttribute('contenteditable') === '') return 'editable';
    return null;
  };

  const SKIP_ATTR = new Set(['style', 'd', 'points', 'transform', 'srcset', 'viewBox', 'class']);
  const ids = []; const classes = {}; let withId = 0, withClass = 0;
  const data = {}; const ik = {}; let interactive = 0;
  const kinds = new Map();
  for (const el of all) {
    const k = ikind(el); kinds.set(el, k);
    if (k) { inc(ik, k); interactive++; }
    if (el.id) { withId++; if (ids.length < 5000) ids.push(el.id); }
    const cl = el.getAttribute('class');
    if (cl) {
      withClass++;
      for (const c of cl.split(/\s+/)) if (c) { if (classes[c] || Object.keys(classes).length < 5000) inc(classes, c); }
    }
    for (const a of el.attributes) {
      const n = a.name;
      if (n.startsWith('data-')) {
        let d = data[n];
        if (!d) { if (Object.keys(data).length >= 300) continue; d = data[n] = { elements: 0, interactive: 0, values: [] }; }
        d.elements++; if (k) d.interactive++;
        if (d.values.length < 300) d.values.push(a.value);
      }
      if (!SKIP_ATTR.has(n)) addS(a.value);
    }
    if ((el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && el.value) addS(el.value);
  }
  for (const c of Object.keys(classes)) addS(c);
  // text nodes
  const tw = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n && sentinels.size < cfg.maxStrings; n = tw.nextNode()) {
    const p = n.parentElement && n.parentElement.tagName;
    if (p === 'SCRIPT' || p === 'STYLE' || p === 'NOSCRIPT' || p === 'TEMPLATE') continue;
    addS(n.nodeValue);
    for (const w of (n.nodeValue || '').split(/\s+/)) addS(w);
  }
  addS(document.title);
  addS(location.hostname); addS(location.pathname); addS(location.search); addS(location.hash);
  for (const s of location.pathname.split('/')) addS(decodeURIComponent(s));

  // ---- form fields: how are they labelled?
  const labels = { total: 0 };
  const FIELD_ROLES = ['textbox', 'combobox', 'searchbox', 'spinbutton', 'slider', 'checkbox', 'radio', 'switch'];
  const byId = (root, id) => (root.getElementById ? root.getElementById(id) : document.getElementById(id));
  for (const el of all) {
    const t = el.tagName; let field = false;
    if (t === 'INPUT') field = !['hidden', 'submit', 'button', 'reset', 'image'].includes((el.getAttribute('type') || 'text').toLowerCase());
    else if (t === 'SELECT' || t === 'TEXTAREA') field = true;
    else field = FIELD_ROLES.includes(el.getAttribute('role'));
    if (!field) continue;
    labels.total++;
    const root = el.getRootNode();
    let any = false;
    if (el.id && root.querySelector) {
      try { if (root.querySelector('label[for="' + CSS.escape(el.id) + '"]')) { inc(labels, 'label-for'); any = true; } } catch (e) {}
    }
    if (el.closest('label')) { inc(labels, 'wrapping-label'); any = true; }
    if ((el.getAttribute('aria-label') || '').trim()) { inc(labels, 'aria-label'); any = true; }
    const lb = (el.getAttribute('aria-labelledby') || '').trim().split(/\s+/).filter(Boolean);
    if (lb.length && lb.some(i => byId(root, i))) { inc(labels, 'aria-labelledby'); any = true; }
    if (!any) {
      if ((el.getAttribute('title') || '').trim()) inc(labels, 'title-only');
      else if ((el.getAttribute('placeholder') || '').trim()) inc(labels, 'placeholder-only');
      else inc(labels, 'none');
    }
  }
  // ---- buttons: how are they named?
  const buttons = { total: 0 };
  for (const el of all) {
    const t = el.tagName;
    const ty = (el.getAttribute('type') || '').toLowerCase();
    const isBtn = t === 'BUTTON' || el.getAttribute('role') === 'button' || (t === 'INPUT' && ['button', 'submit', 'reset'].includes(ty));
    if (!isBtn) continue;
    buttons.total++;
    const text = t === 'INPUT' ? (el.value || '') : (el.textContent || '');
    if (text.trim()) inc(buttons, 'text');
    else if ((el.getAttribute('aria-label') || '').trim()) inc(buttons, 'aria-label');
    else if ((el.getAttribute('aria-labelledby') || '').trim()) inc(buttons, 'aria-labelledby');
    else if ((el.getAttribute('title') || '').trim()) inc(buttons, 'title');
    else if (el.querySelector && el.querySelector('svg, img, i, [class*=icon], [class*=Icon]')) inc(buttons, 'icon-only');
    else inc(buttons, 'empty');
  }

  // ---- frameworks
  const fw = {};
  const put = (k, v) => { fw[k] = v == null ? '' : String(v); };
  const W = window;
  try {
    let react = false;
    for (const el of all.slice(0, 3000)) {
      if (el._reactRootContainer) { react = true; break; }
      if (Object.keys(el).some(k => k.startsWith('__reactFiber') || k.startsWith('__reactContainer') || k.startsWith('__reactInternalInstance'))) { react = true; break; }
    }
    if (react || W.React || document.querySelector('[data-reactroot]')) {
      let v = W.React && W.React.version;
      const hook = W.__REACT_DEVTOOLS_GLOBAL_HOOK__;
      if (!v && hook && hook.renderers && hook.renderers.forEach) hook.renderers.forEach(r => { if (!v && r.version) v = r.version; });
      put('react', v);
    }
    if (W.next && (W.next.version || W.__NEXT_DATA__)) put('nextjs', W.next.version);
    else if (W.__NEXT_DATA__) put('nextjs', null);
    const ngv = document.querySelector('[ng-version]');
    if (ngv) put('angular', ngv.getAttribute('ng-version'));
    if (W.angular && W.angular.version) put('angularjs', W.angular.version.full);
    else if (document.querySelector('.ng-scope, [ng-app], [data-ng-app]')) put('angularjs', null);
    let vue = W.Vue && W.Vue.version;
    let vueFound = !!W.Vue;
    for (const el of all.slice(0, 3000)) {
      if (el.__vue_app__) { vueFound = true; vue = vue || el.__vue_app__.version; break; }
      if (el.__vue__) { vueFound = true; const c = el.__vue__.constructor; vue = vue || (c && (c.version || (c.super && c.super.version))); break; }
    }
    if (vueFound || document.querySelector('[data-v-app]')) put('vue', vue);
    if (W.__NUXT__ || W.$nuxt) put('nuxt', null);
    if (W.jQuery && W.jQuery.fn) put('jquery', W.jQuery.fn.jquery);
    if (W.Ember) put('ember', W.Ember.VERSION);
    if (W.Ext) put('extjs', (W.Ext.getVersion && W.Ext.getVersion() && W.Ext.getVersion().version) || W.Ext.version);
    if (W.Backbone) put('backbone', W.Backbone.VERSION);
    if (W.ko) put('knockout', W.ko.version);
    if (W.dojo) put('dojo', W.dojo.version && W.dojo.version.toString());
    if (W.kendo) put('kendo', W.kendo.version);
    if (W.DevExpress) put('devextreme', W.DevExpress.VERSION);
    if (W.PrimeFaces) put('primefaces', W.PrimeFaces.VERSION);
    if (W.jsf || W.faces) put('jsf', (W.jsf && W.jsf.specversion) || null);
    if (Object.getOwnPropertyNames(W).some(k => k.startsWith('__gwt'))) put('gwt', null);
    const bsv = (W.bootstrap && W.bootstrap.Tooltip && W.bootstrap.Tooltip.VERSION) ||
      (W.jQuery && W.jQuery.fn && W.jQuery.fn.tooltip && W.jQuery.fn.tooltip.Constructor && W.jQuery.fn.tooltip.Constructor.VERSION);
    if (bsv) put('bootstrap', bsv);
    const clsKeys = Object.keys(classes);
    if (clsKeys.some(c => /^Mui[A-Z]/.test(c))) put('mui', null);
    if (clsKeys.some(c => /^mat-|^mdc-/.test(c)) || all.some(el => el.tagName.startsWith('MAT-'))) put('angular-material', null);
    if (clsKeys.some(c => /^svelte-[a-z0-9]+$/.test(c)) || W.__svelte) put('svelte', W.__svelte && W.__svelte.v && [...W.__svelte.v][0]);
    if (W.litElementVersions || W.litHtmlVersions) put('lit', (W.litElementVersions || W.litHtmlVersions || [])[0]);
    if (W.Polymer) put('polymer', W.Polymer.version);
    if (W.$A && W.$A.get) put('salesforce-lightning', null);
    if (W.Wicket) put('wicket', null);
    if (W.zk) put('zk', W.zk.version);
    if (W.Vaadin || all.some(el => el.tagName.startsWith('VAADIN-'))) put('vaadin', null);
    if (W.Blazor) put('blazor', null);
    if (W.htmx) put('htmx', W.htmx.version);
    if (W.Alpine) put('alpine', W.Alpine.version);
    if (W._$HY) put('solid', null);
    if (W.preact) put('preact', null);
  } catch (e) { /* ignore */ }

  // ---- other grids
  const og = {};
  const cnt = (sel) => { try { return document.querySelectorAll(sel).length; } catch (e) { return 0; } };
  og['html-table'] = all.filter(el => el.tagName === 'TABLE' && !el.closest('.ag-root-wrapper')).length;
  og['role-grid-non-ag'] = all.filter(el => /^(grid|treegrid)$/.test(el.getAttribute('role') || '') && !el.closest('.ag-root-wrapper')).length;
  og['kendo-grid'] = cnt('.k-grid'); og['devextreme-grid'] = cnt('.dx-datagrid'); og['handsontable'] = cnt('.handsontable.ht_master');
  og['slickgrid'] = cnt('.slick-viewport'); og['tabulator'] = cnt('.tabulator'); og['mui-datagrid'] = cnt('.MuiDataGrid-root');
  og['ui-grid'] = cnt('.ui-grid'); og['primeng-table'] = cnt('.p-datatable'); og['ext-grid'] = cnt('.x-grid');
  og['vaadin-grid'] = cnt('vaadin-grid'); og['jqgrid'] = cnt('.ui-jqgrid'); og['datatables'] = cnt('.dataTables_wrapper');

  // ---- AG Grid
  const ag = { present: false, version: null, grids: [], apis: [] };
  const roots = all.filter(el => el.classList && el.classList.contains('ag-root-wrapper'));
  const vEl = document.querySelector('style[data-ag-css-version]');
  ag.version = (vEl && vEl.getAttribute('data-ag-css-version')) || (W.agGrid && (W.agGrid.VERSION || W.agGrid.version)) || null;
  ag.present = roots.length > 0 || !!W.agGrid || !!vEl;
  for (const r of roots) {
    const g = {};
    const themes = new Set();
    for (let e = r; e && e.nodeType === 1; e = e.parentElement) {
      for (const c of e.classList) if (/^ag-theme-/.test(c)) themes.add(c);
      if (e.classList.contains('ag-styled-root')) g.styledRoot = true;
    }
    g.themes = [...themes];
    const hcells = [...r.querySelectorAll('.ag-header-cell[col-id]')];
    const colIds = new Set(), left = new Set(), right = new Set();
    for (const h of hcells) {
      const id = h.getAttribute('col-id'); colIds.add(id);
      if (h.closest('[class*="pinned-left"]')) left.add(id);
      else if (h.closest('[class*="pinned-right"]')) right.add(id);
    }
    // older versions: pinned cells but header pinned containers named differently
    g.colIds = [...colIds].slice(0, 300);
    g.pinnedLeft = left.size; g.pinnedRight = right.size;
    g.pinnedCellsLeft = r.querySelectorAll('.ag-cell-last-left-pinned').length > 0;
    g.pinnedCellsRight = r.querySelectorAll('.ag-cell-first-right-pinned').length > 0;
    g.groupHeaders = new Set([...r.querySelectorAll('.ag-header-group-cell[col-id]')].map(h => h.getAttribute('col-id'))).size;
    const rows = [...r.querySelectorAll('.ag-row[row-index]')].filter(x => /^\d+$/.test(x.getAttribute('row-index')));
    const rowIdx = new Set(), rowIds = new Set();
    for (const x of rows) { rowIdx.add(x.getAttribute('row-index')); const id = x.getAttribute('row-id'); if (id != null && rowIds.size < 300) rowIds.add(id); }
    g.renderedRows = rowIdx.size; g.rowIds = [...rowIds];
    const gridEl = r.querySelector('[role=grid],[role=treegrid]');
    g.ariaRowCount = gridEl ? parseInt(gridEl.getAttribute('aria-rowcount') || '-1', 10) : -1;
    g.ariaColCount = gridEl ? parseInt(gridEl.getAttribute('aria-colcount') || '-1', 10) : -1;
    g.headerRows = new Set([...r.querySelectorAll('.ag-header-row')].map(h => h.getAttribute('aria-rowindex') || h.className)).size;
    g.layout = r.classList.contains('ag-layout-auto-height') ? 'autoHeight' : r.classList.contains('ag-layout-print') ? 'print' : r.classList.contains('ag-layout-normal') ? 'normal' : 'unknown';
    g.floatingFilter = !!r.querySelector('.ag-floating-filter, .ag-header-row-filter, .ag-header-row-column-filter');
    g.selectionCheckboxes = !!r.querySelector('.ag-selection-checkbox, .ag-header-select-all');
    g.rowGroups = !!r.querySelector('.ag-row-group');
    g.fullWidthRows = !!r.querySelector('.ag-full-width-row');
    g.cellLinks = r.querySelectorAll('.ag-cell a[href]').length;
    g.cellButtons = r.querySelectorAll('.ag-cell button, .ag-cell [role=button]').length;
    g.cellInputs = r.querySelectorAll('.ag-cell input, .ag-cell select').length;
    g.editableCellHints = r.querySelectorAll('.ag-cell-editable, .ag-cell[aria-readonly="false"]').length;
    const ov = r.querySelector('.ag-overlay-loading-wrapper, .ag-overlay-loading-center');
    g.loadingOverlayVisible = !!(ov && vis(ov));
    g.gridIdAttr = r.getAttribute('grid-id');
    g.visible = vis(r);
    ag.grids.push(g);
  }
  // grid APIs reachable from window (own data properties only; getters are never invoked)
  const isApi = v => v && typeof v === 'object' && (typeof v.getDisplayedRowCount === 'function' || typeof v.forEachNode === 'function');
  for (const n of Object.getOwnPropertyNames(W)) {
    let v, d;
    try { d = Object.getOwnPropertyDescriptor(W, n); } catch (e) { continue; }
    if (!d || !('value' in d)) continue;
    v = d.value;
    let api = null, sub = false;
    try {
      if (isApi(v)) api = v;
      else if (v && typeof v === 'object' && !(v instanceof Node) && v !== W) {
        const dd = Object.getOwnPropertyDescriptor(v, 'api');
        if (dd && 'value' in dd && isApi(dd.value)) { api = dd.value; sub = true; }
      }
    } catch (e) { continue; }
    if (!api) continue;
    const info = { name: n, sub };
    const call = (f) => { try { return f(); } catch (e) { return undefined; } };
    info.gridId = call(() => api.getGridId && String(api.getGridId()));
    info.destroyed = call(() => api.isDestroyed && api.isDestroyed()) === true;
    info.displayed = call(() => api.getDisplayedRowCount());
    info.rowModel = call(() => api.getGridOption ? api.getGridOption('rowModelType') : (api.getModel && api.getModel().getType()));
    info.getRowId = call(() => api.getGridOption ? typeof api.getGridOption('getRowId') === 'function' : undefined);
    const cols = call(() => api.getColumns ? api.getColumns() : (api.getAllColumns ? api.getAllColumns() : null)) || [];
    info.columns = cols.length;
    info.editable = cols.filter(c => call(() => !!c.getColDef().editable)).length;
    info.pinnedLeft = cols.filter(c => call(() => c.getPinned()) === 'left').length;
    info.pinnedRight = cols.filter(c => call(() => c.getPinned()) === 'right').length;
    ag.apis.push(info);
    if (ag.apis.length >= 20) break;
  }
  if (ag.apis.length) ag.present = true;

  // ---- dialogs, frames
  const dialogs = all.filter(el => el.tagName === 'DIALOG' || /^(dialog|alertdialog)$/.test(el.getAttribute('role') || ''));
  const iframes = all.filter(el => el.tagName === 'IFRAME' || el.tagName === 'FRAME');
  let iframeSame = 0;
  for (const f of iframes) { try { if (f.contentDocument) iframeSame++; } catch (e) {} }

  // ---- links (text is inspected here, never returned)
  const LOGOUT = /log\s*-?\s*out|sign\s*-?\s*out|log\s*off|sign\s*off|logoff|signoff/i;
  const links = [];
  for (const a of all) {
    const t = a.tagName;
    if (!((t === 'A' || t === 'a' || t === 'AREA') && a.hasAttribute('href'))) continue;
    if (links.length >= 2000) break;
    const raw = a.getAttribute('href') || '';
    let abs = null; try { abs = new URL(raw, document.baseURI).href; } catch (e) {}
    const txt = (a.textContent || '') + ' ' + (a.getAttribute('aria-label') || '') + ' ' + (a.getAttribute('title') || '');
    links.push({ href: abs, logout: LOGOUT.test(txt) || LOGOUT.test(raw), download: a.hasAttribute('download'),
                 method: (a.getAttribute('data-method') || '').toLowerCase() || null });
  }

  // ---- transient spinners
  const P = window.__probe; const transient = []; let spinnersStill = 0;
  if (P) for (const w of P.watch) {
    const gone = !w.el.isConnected || !vis(w.el) || (w.busy && w.el.getAttribute('aria-busy') !== 'true');
    if (gone) transient.push(w.sig); else spinnersStill++;
  }

  return {
    elements: all.length, inShadow, shadowOpen, customEls, withId, withClass,
    ids, classes, data, interactive, interactiveKinds: ik, labels, buttons, frameworks: fw, otherGrids: og, ag,
    dialogs: dialogs.length, dialogsOpen: dialogs.filter(vis).length,
    dialogsModal: dialogs.filter(d => d.getAttribute('aria-modal') === 'true' || d.tagName === 'DIALOG').length,
    iframes: iframes.length, iframesSameOrigin: iframeSame, iframesVisible: iframes.filter(vis).length,
    framesets: all.filter(el => el.tagName === 'FRAMESET').length,
    forms: all.filter(el => el.tagName === 'FORM').length,
    passwordFields: all.filter(el => el.tagName === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() === 'password' && vis(el)).length,
    links, transient, spinnersStill, sentinels: [...sentinels],
  };
}
"""
