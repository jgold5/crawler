/*
 * In-page crawler engine (M1a): locator candidate generation + verification, target resolution.
 *
 * Defines window.__crawler. The Python side replaces the BOOT placeholder at the bottom with
 * {"cfg": SiteConfig.js_config(), "key": "...", "version": "..."}. Evaluating this file is
 * idempotent: if an engine of the same version exists it is only re-configured.
 *
 * XPath semantics are mirrored exactly: text is the element's string-value (textContent) and
 * normalization only collapses [ \t\r\n] (XPath normalize-space keeps NBSP).
 */
(function (BOOT) {
  'use strict';
  var W = window;
  if (W.__crawler && W.__crawler.version === BOOT.version) {
    W.__crawler.configure(BOOT);
    return true;
  }

  var HTML_NS = 'http://www.w3.org/1999/xhtml';
  var MAX_CANDIDATES = 12;
  var THRESHOLD = 0.5;
  var TIE = 0.02;

  // ------------------------------------------------------------------ config
  var CFG = null;
  var RX = { id: [], idAllow: [], cls: [], text: [] };

  function compile(list) {
    var out = [];
    (list || []).forEach(function (p) {
      var flags = '';
      var m = /^\(\?([aiLmsux]+)\)/.exec(p);
      if (m) {
        if (m[1].indexOf('i') >= 0) flags += 'i';
        p = p.slice(m[0].length);
      }
      p = p.replace(/\(\?P</g, '(?<').replace(/\\Z/g, '$');
      try { out.push(new RegExp(p, flags)); } catch (e) { /* pattern not expressible in JS: ignore */ }
    });
    return out;
  }

  function configure(boot) {
    CFG = boot.cfg || {};
    RX.id = compile(CFG.unstableIdPatterns);
    RX.idAllow = compile(CFG.stableIdAllow);
    RX.cls = compile(CFG.unstableClassPatterns);
    RX.text = compile(CFG.unstableTextPatterns);
    api.key = boot.key;
    api.cfg = CFG;
  }

  function anyRx(rxs, s) {
    for (var i = 0; i < rxs.length; i++) if (rxs[i].test(s)) return true;
    return false;
  }
  function stableId(v) {
    if (!v || /\s/.test(v)) return false;
    if (anyRx(RX.idAllow, v)) return true;
    return !anyRx(RX.id, v);
  }
  function stableClass(c) { return !!c && !anyRx(RX.cls, c); }
  function stableText(t) { return !!t && !anyRx(RX.text, t); }

  // ------------------------------------------------------------------ text / literals
  function norm(s) {
    return (s == null ? '' : String(s)).replace(/[ \t\r\n]+/g, ' ').replace(/^ | $/g, '');
  }
  function text(el) { return norm(el.textContent); }

  /** Human-visible text: like textContent but skipping SVG (icon <title>s), script, style and [hidden] content. */
  function displayText(el) {
    if (!el.querySelector('svg,script,style,template,noscript,[hidden]')) return text(el);
    var out = '';
    (function walk(n) {
      for (var c = n.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 3) out += c.data;
        else if (c.nodeType === 1 && isHtml(c) && !/^(script|style|template|noscript)$/.test(c.localName) &&
                 !c.hasAttribute('hidden')) walk(c);
      }
    })(el);
    return norm(out);
  }

  var NBSP = '\u00a0';
  /**
   * Text-equality predicate on the context node. Text that contains NBSP gets an NBSP-insensitive form
   * (translate NBSP to space, then normalize), which also survives the NBSP being replaced by a plain space;
   * plain text keeps the short normalize-space() form.
   */
  function textEq(t) {
    if (t.indexOf(NBSP) < 0) return 'normalize-space()=' + lit(t);
    return 'normalize-space(translate(.,' + lit(NBSP) + ",' '))=" + lit(norm(t.split(NBSP).join(' ')));
  }

  /** XPath 1.0 string literal for s; concat() when s contains both quote kinds. */
  function lit(s) {
    s = String(s);
    if (s.indexOf("'") < 0) return "'" + s + "'";
    if (s.indexOf('"') < 0) return '"' + s + '"';
    var out = [];
    s.split("'").forEach(function (part, i) {
      if (i) out.push('"\'"');
      if (part) out.push("'" + part + "'");
    });
    return 'concat(' + out.join(',') + ')';
  }

  /** Canonical form for fuzzy matching: lowercase, punctuation -> space, collapsed. */
  function canon(s) {
    var raw = String(s == null ? '' : s).toLowerCase().replace(/[\s ]+/g, ' ').trim();
    var c = raw.replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
    return c || raw;
  }

  function humanize(s) {
    return norm(String(s || '')
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .replace(/[_\-.\[\]:#/]+/g, ' ')).toLowerCase();
  }

  /** Text up to the first unstable fragment, cut at a word boundary (for starts-with()). */
  function stablePrefix(t, maxLen) {
    var cut = t.length;
    RX.text.forEach(function (rx) {
      var m = rx.exec(t);
      if (m && m.index < cut) cut = m.index;
    });
    var p = t.slice(0, Math.min(cut, maxLen));
    if (p.length < t.length && /\S/.test(t.charAt(p.length)) && p.lastIndexOf(' ') > 0) {
      p = p.slice(0, p.lastIndexOf(' '));
    }
    p = p.replace(/[ \t\r\n]+$/, '').replace(/[\s(\[{:,;-]+$/, '');
    return p.length >= 3 && stableText(p) ? p : '';
  }

  // ------------------------------------------------------------------ xpath evaluation
  function snap(xp, ctx) {
    try {
      return document.evaluate(xp, ctx || document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    } catch (e) {
      return null;
    }
  }
  function count(xp) { var r = snap(xp); return r ? r.snapshotLength : -1; }
  function first(xp) { var r = snap(xp); return r && r.snapshotLength ? r.snapshotItem(0) : null; }
  function all(xp, ctx) {
    var r = snap(xp, ctx), out = [];
    if (r) for (var i = 0; i < r.snapshotLength; i++) out.push(r.snapshotItem(i));
    return out;
  }
  function uniqueIs(xp, el, ctx) {
    var r = snap(xp, ctx);
    return !!r && r.snapshotLength === 1 && r.snapshotItem(0) === el;
  }

  /**
   * Elements selected by //T[@A=INNER/@R], with exact XPath semantics (some INNER node's @R equals
   * the element's @A) but without the browser re-evaluating INNER for every candidate node, which
   * is quadratic (~90 ms for 300 inputs on a 2k-element page).
   */
  function refSelect(t, a, inner, r) {
    var vals = new Set();
    all(inner).forEach(function (n) { if (n.nodeType === 1 && n.hasAttribute(r)) vals.add(n.getAttribute(r)); });
    if (!vals.size) return [];
    var list = document.querySelectorAll((t === '*' ? '' : t) + '[' + a + ']');
    return Array.prototype.filter.call(list, function (e) { return vals.has(e.getAttribute(a)); });
  }
  function refUnique(t, a, inner, r, el) {
    var m = refSelect(t, a, inner, r);
    return m.length === 1 && m[0] === el;
  }

  // ------------------------------------------------------------------ element helpers
  function isHtml(el) { return !!el && el.nodeType === 1 && el.namespaceURI === HTML_NS; }
  function htmlTarget(el) {
    while (el && !isHtml(el)) el = el.parentElement || (el.parentNode && el.parentNode.host) || null;
    return el;
  }
  function tagOf(el) {
    var t = el.localName;
    return /^[a-z][a-z0-9-]*$/.test(t) ? t : '*';
  }
  function attr(el, a) { var v = el.getAttribute(a); return v == null ? '' : v; }
  function inputType(el) { return (attr(el, 'type') || 'text').toLowerCase(); }
  function role(el) { return attr(el, 'role').toLowerCase().split(/\s+/)[0] || ''; }

  var INPUT_BUTTON = { submit: 1, button: 1, reset: 1, image: 1 };
  var NOT_FILLABLE = { hidden: 1, submit: 1, button: 1, reset: 1, checkbox: 1, radio: 1, file: 1, image: 1 };
  var CLICK_ROLES = {
    button: 1, link: 1, tab: 1, menuitem: 1, menuitemcheckbox: 1, menuitemradio: 1, option: 1,
    checkbox: 1, radio: 1, switch: 1, treeitem: 1
  };
  var CLICK_TAGS = { button: 1, a: 1, summary: 1, label: 1, option: 1 };
  var FORM_TAGS = { input: 1, select: 1, textarea: 1 };
  var HEADING_TAGS = { h1: 1, h2: 1, h3: 1, h4: 1, h5: 1, h6: 1, legend: 1, caption: 1 };
  // Attributes that make an element clickable in Angular / AngularJS / Material markup (static attributes
  // stay in the DOM; `(click)` bindings do not, so icon-only clickables are also found by their cursor).
  var CLICK_ATTRS = ['onclick', 'ng-click', 'data-ng-click', 'routerlink', 'mat-icon-button', 'mat-button',
                     'mat-raised-button', 'mat-flat-button', 'mat-stroked-button', 'mat-fab', 'mat-mini-fab',
                     'mat-menu-item', 'mat-dialog-close'];

  function isClickable(el) {
    var t = el.localName;
    if (CLICK_TAGS[t]) return true;
    if (t === 'input') return !!INPUT_BUTTON[inputType(el)] || inputType(el) === 'checkbox' || inputType(el) === 'radio';
    if (CLICK_ROLES[role(el)]) return true;
    for (var i = 0; i < CLICK_ATTRS.length; i++) if (el.hasAttribute(CLICK_ATTRS[i])) return true;
    if (!FORM_TAGS[t] && el.hasAttribute('tabindex') && parseInt(attr(el, 'tabindex'), 10) >= 0) return true;
    return false;
  }
  function isHeading(el) { return !!HEADING_TAGS[el.localName] || role(el) === 'heading'; }

  // Title elements of containers that are not h1-h6 (Angular Material, Bootstrap).
  var TITLE_SEL = 'mat-card-title,.mat-mdc-card-title,.mat-card-title,mat-panel-title,.mat-expansion-panel-header-title,' +
                  '[mat-dialog-title],.mat-mdc-dialog-title,.mat-dialog-title,.panel-title,.card-title,.modal-title';
  // Containers a title names.
  var CONTAINER_SEL = '[role=dialog],[role=alertdialog],dialog,mat-dialog-container,.mat-mdc-dialog-container,mat-card,' +
                      '.mat-mdc-card,.mat-card,mat-expansion-panel,.mat-expansion-panel,.card,.panel,.modal-content,' +
                      'section,fieldset,[role=region],[role=tabpanel]';
  var DIALOG_SEL = '[role=dialog],[role=alertdialog],dialog,mat-dialog-container,.mat-mdc-dialog-container,.modal-content';
  var GRID_SEL = 'ag-grid-angular,ag-grid-vue,.ag-root-wrapper,.ag-root,[role=grid],[role=treegrid],table';
  var TOOLBAR_SEL = '[role=toolbar],mat-toolbar,.mat-toolbar,.toolbar,.btn-toolbar,.btn-group';

  function isA(el, sel) { try { return !!el.matches && el.matches(sel); } catch (e) { return false; } }
  function isTitle(el) { return isHeading(el) || (isHtml(el) && isA(el, TITLE_SEL)); }
  function closestOf(el, sel) { try { return el.closest(sel); } catch (e) { return null; } }

  /** The title element of container `a` (a heading/title inside it that belongs to it, not to a nested container). */
  function ownTitle(a) {
    var ts = a.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading],' + TITLE_SEL);
    for (var i = 0; i < ts.length && i < 40; i++) {
      var t = ts[i];
      var c = t.parentElement && closestOf(t.parentElement, CONTAINER_SEL);
      if ((c === a || !c || !a.contains(c)) && text(t)) return t;
    }
    return null;
  }

  // ------------------------------------------------------------------ icons (Font Awesome v4/v5/v6 + SVG mode)
  // FA classes that only style an icon (family, size, animation, layout): never a name, never in a locator.
  var FA_STYLE = /^(fa|fas|far|fal|fat|fad|fab|fak|fass|fasr|fasl|fast|fasds|svg-inline--fa)$/;
  var FA_MOD = new RegExp('^fa-(' + [
    'solid', 'regular', 'light', 'thin', 'duotone', 'brands', 'sharp', 'sharp-solid', 'sharp-regular', 'sharp-light',
    'sharp-thin', 'sharp-duotone-solid', 'classic', 'kit', 'kit-duotone', 'primary', 'secondary',
    '\\d+x', '2xs', 'xs', 'sm', 'lg', 'xl', '2xl', 'fw', 'width-auto', 'width-fixed', 'spin', 'pulse', 'spin-pulse',
    'spin-reverse', 'beat', 'fade', 'beat-fade', 'bounce', 'flip', 'shake', 'border', 'inverse', 'li', 'ul', 'stack',
    'stack-1x', 'stack-2x', 'pull-left', 'pull-right', 'pull-start', 'pull-end', 'rotate-90', 'rotate-180', 'rotate-270',
    'rotate-by', 'flip-horizontal', 'flip-vertical', 'flip-both', 'swap-opacity', 'layers', 'layers-text',
    'layers-counter', 'layers-bottom-left', 'layers-bottom-right', 'layers-top-left', 'layers-top-right', 'sr-only',
    'sr-only-focusable', 'icon'
  ].join('|') + ')$');
  function isFaNoise(c) { return FA_STYLE.test(c) || FA_MOD.test(c); }
  var ICON_SEL = '[class*="fa-"],svg[data-icon],mat-icon';
  var GLYPH_CLOSE = /^[×✕✖✗✘⨯╳]$/;

  /** Icon identity of node n: {name, cls, svg, mat, node} or null. */
  function iconOf(n) {
    if (!n || n.nodeType !== 1) return null;
    if (n.localName === 'svg' && n.hasAttribute('data-icon')) {
      return { name: attr(n, 'data-icon'), node: n, svg: true, prefix: attr(n, 'data-prefix') };
    }
    var cls = (n.getAttribute('class') || '').split(/[ \t\r\n]+/);
    for (var i = 0; i < cls.length; i++) {
      var c = cls[i];
      if (c.length > 3 && c.lastIndexOf('fa-', 0) === 0 && !isFaNoise(c) && /^fa-[a-z0-9-]+$/.test(c)) {
        return { name: c.slice(3), cls: c, node: n, svg: !isHtml(n) };
      }
    }
    if (n.localName === 'mat-icon') {
      var nm = attr(n, 'data-mat-icon-name') || attr(n, 'svgicon');
      if (nm) return { name: nm, node: n, mat: nm === attr(n, 'svgicon') ? 'svgicon' : 'data-mat-icon-name' };
      if (!n.children.length && /^[a-z0-9_]{2,40}$/.test(text(n))) return { name: text(n).replace(/_/g, '-'), node: n, mat: 'text' };
    }
    return null;
  }

  function pointer(el) { try { return getComputedStyle(el).cursor === 'pointer'; } catch (e) { return false; } }

  /** The element an icon names: nearest clickable ancestor-or-self (5 levels up), else a pointer-cursor icon itself. */
  function iconOwner(n) {
    var a = n;
    for (var d = 0; a && d < 5; d++, a = a.parentElement) {
      if (a === document.body || a === document.documentElement) break;
      if (isHtml(a) && isClickable(a)) return a;
    }
    var h = htmlTarget(n);
    if (h && h !== document.body && (pointer(h) || (h !== n && pointer(n)))) return h;
    return null;
  }

  /** Icons that name el (own class, or icons inside it whose owner is el), unique by name. */
  function iconsOf(el) {
    var out = [], seen = {};
    var list = [el];
    var inner = el.querySelectorAll(ICON_SEL);
    for (var i = 0; i < inner.length && i < 12; i++) list.push(inner[i]);
    for (var j = 0; j < list.length; j++) {
      var ic = iconOf(list[j]);
      if (!ic || seen[ic.name] || iconOwner(list[j]) !== el) continue;
      seen[ic.name] = 1;
      out.push(ic);
    }
    if (!out.length && isClickable(el) && GLYPH_CLOSE.test(displayText(el))) {
      out.push({ name: 'times', node: el, glyph: displayText(el) });
    }
    return out;
  }
  function iconLabel(ic) {
    if (ic.svg) return 'svg data-icon="' + ic.name + '"';
    if (ic.mat) return 'mat-icon "' + ic.name + '"';
    if (ic.glyph) return 'glyph "' + ic.glyph + '"';
    return ic.cls || 'fa-' + ic.name;
  }

  // English -> FA names across v4/v5/v6 (docs/FLOWS.md "Icon synonyms"). `*` is a prefix wildcard.
  var ICON_SYN = [
    [['refresh', 'reload'], ['refresh', 'sync', 'sync-alt', 'rotate', 'rotate-right', 'arrows-rotate', 'redo']],
    [['close', 'x', 'cancel'], ['close', 'times', 'xmark', 'times-circle', 'circle-xmark', 'remove']],
    [['delete', 'trash'], ['trash', 'trash-o', 'trash-alt', 'trash-can']],
    [['edit', 'pencil'], ['pencil', 'pencil-alt', 'pen', 'edit', 'pen-to-square']],
    [['add', 'new', 'plus'], ['plus', 'plus-circle', 'circle-plus', 'add']],
    [['search'], ['search', 'magnifying-glass']],
    [['filter'], ['filter', 'sliders']],
    [['download', 'export'], ['download', 'file-export', 'file-download']],
    [['upload', 'import'], ['upload', 'file-import', 'file-upload']],
    [['settings'], ['cog', 'gear', 'cogs', 'sliders-h']],
    [['more', 'menu'], ['ellipsis-v', 'ellipsis-h', 'ellipsis-vertical', 'bars']],
    [['expand', 'collapse'], ['chevron-*', 'caret-*', 'angle-*', 'plus-square', 'minus-square']],
    [['save'], ['save', 'floppy-disk']],
    [['copy'], ['copy', 'clone']],
    [['info', 'help'], ['info-circle', 'circle-info', 'question-circle', 'circle-question']]
  ];
  var ICON_NOISE = /(^| )(the|an?|icon|button|btn|link)(?= |$)/g;
  function faIn(list, name) {
    for (var i = 0; i < list.length; i++) {
      var f = list[i];
      if (f === name || (f.charAt(f.length - 1) === '*' && name.lastIndexOf(f.slice(0, -1), 0) === 0)) return true;
    }
    return false;
  }
  /** Synonym groups a canonical query names (by English word or by FA name). */
  function synGroups(q) {
    var dash = q.replace(/ /g, '-'), out = [];
    ICON_SYN.forEach(function (g) {
      if (g[0].indexOf(q) >= 0 || faIn(g[1], dash)) out.push(g);
    });
    return out;
  }
  /** English word for an FA name ("arrows-rotate" -> "refresh"), or the name humanized. */
  function iconWord(name) {
    for (var i = 0; i < ICON_SYN.length; i++) if (faIn(ICON_SYN[i][1], name)) return ICON_SYN[i][0][0];
    return name.replace(/-/g, ' ');
  }
  /** {m, tier}: m=1 with tier 'icon' for an FA-name or synonym match; else fuzzy word overlap. */
  function iconMatch(q, name) {
    var qq = q.replace(ICON_NOISE, ' ').replace(/ +/g, ' ').trim() || q;
    var n = canon(name.replace(/-/g, ' '));
    if (qq === n) return { m: 1, tier: 'icon' };
    var gs = synGroups(qq);
    for (var i = 0; i < gs.length; i++) if (faIn(gs[i][1], name)) return { m: 1, tier: 'icon' };
    return { m: match(qq, n), tier: 'fuzzy' };
  }

  // ------------------------------------------------------------------ tooltips (Angular Material, Bootstrap, PrimeNG)
  var TOOLTIP_ATTRS = ['mattooltip', 'ptooltip', 'ngbtooltip', 'uib-tooltip', 'data-original-title',
                       'data-bs-original-title', 'data-tooltip', 'tooltip'];
  function isTooltipMessage(r) {
    return /^cdk-describedby-message/.test(r.id) || role(r) === 'tooltip' ||
      !!closestOf(r, '.cdk-describedby-message-container,#cdk-describedby-message-container');
  }
  /** [{text, source: 'tooltip'|'description', el?, attr?}] */
  function tooltipsOf(el) {
    var out = [];
    attr(el, 'aria-describedby').split(/\s+/).filter(Boolean).forEach(function (id) {
      var r = document.getElementById(id);
      if (!r) return;
      var t = text(r);
      if (!t) return;
      if (isTooltipMessage(r)) out.push({ text: t, source: 'tooltip', el: r });
      else if (t.length <= 60) out.push({ text: t, source: 'description', el: r });
    });
    TOOLTIP_ATTRS.forEach(function (a) {
      var v = norm(attr(el, a));
      if (v) out.push({ text: v, source: 'tooltip', attr: a });
    });
    return out;
  }

  function visible(el) {
    if (!el || !el.isConnected) return false;
    if (el.checkVisibility) {
      if (!el.checkVisibility({ visibilityProperty: true, contentVisibilityAuto: true })) return false;
    } else {
      var cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
    }
    var rects = el.getClientRects();
    for (var i = 0; i < rects.length; i++) if (rects[i].width > 0 || rects[i].height > 0) return true;
    return false;
  }
  /** Visible, or a hidden native checkbox/radio whose label is visible (custom checkbox pattern). */
  function usable(el) {
    if (visible(el)) return true;
    if (el.localName === 'input' && (inputType(el) === 'checkbox' || inputType(el) === 'radio') && el.labels) {
      for (var i = 0; i < el.labels.length; i++) if (visible(el.labels[i])) return true;
    }
    return false;
  }

  /** Label text with nested controls removed, as segments (split at each control). */
  function labelSegments(l) {
    var segs = [], cur = '';
    (function walk(n) {
      for (var c = n.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 3) cur += c.data;
        else if (c.nodeType === 1) {
          if (/^(input|select|textarea|button|datalist|script|style)$/.test(c.localName)) { segs.push(cur); cur = ''; }
          else walk(c);
        }
      }
    })(l);
    segs.push(cur);
    return segs.map(norm).filter(Boolean);
  }
  function labelText(l) { return labelSegments(l).join(' '); }

  function labelledbyEls(el) {
    var ids = attr(el, 'aria-labelledby').split(/\s+/).filter(Boolean);
    return ids.map(function (id) { return document.getElementById(id); }).filter(Boolean);
  }
  function labelledbyText(el) {
    return norm(labelledbyEls(el).map(function (r) { return attr(r, 'aria-label') || text(r); }).join(' '));
  }

  /**
   * Closest preceding short text for an unlabeled field (within 3 ancestor levels).
   * Returns {text, el, contains} or null. `contains` means the text element wraps the field.
   */
  function nearText(el) {
    var node = el;
    for (var lvl = 0; lvl < 3 && node.parentElement && node.parentElement !== document.body; lvl++) {
      var anc = node.parentElement;
      node = anc;
      var walker = document.createTreeWalker(anc, NodeFilter.SHOW_TEXT);
      var best = null;
      for (var t = walker.nextNode(); t; t = walker.nextNode()) {
        if (!(el.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_PRECEDING)) break;
        var p = t.parentElement;
        if (!p || !norm(t.data) || /^(script|style|option|select|textarea|button)$/.test(p.localName)) continue;
        var lab = p.closest('label');
        if (lab && lab.control && lab.control !== el) continue;
        best = t;
      }
      if (!best) continue;
      // Another form control between the text and the field means the text belongs to it.
      var ctrls = anc.querySelectorAll('input:not([type=hidden]),select,textarea');
      for (var i = 0; i < ctrls.length; i++) {
        var c = ctrls[i];
        if (c === el) continue;
        if ((best.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) &&
            (c.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) return null;
      }
      var te = best.parentElement;
      var tx = text(te);
      if (!tx || tx.length > 40 || !stableText(tx) || !isHtml(te)) return null;
      return { text: tx, el: te, contains: te.contains(el) };
    }
    return null;
  }

  // ------------------------------------------------------------------ names
  var SOURCE_WEIGHT = {
    'label': 1.0, 'aria-label': 1.0, 'aria-labelledby': 1.0, 'text': 1.0, 'value': 1.0, 'label-text': 0.95,
    'tooltip': 0.95, 'placeholder': 0.9, 'title': 0.8, 'alt': 0.8, 'name': 0.8, 'icon': 0.8, 'near': 0.65,
    'testid': 0.7, 'description': 0.6, 'id': 0.6
  };
  var SOURCE_ALIAS = {
    label: ['label', 'aria-labelledby'], aria: ['aria-label', 'aria-labelledby'],
    'aria-label': ['aria-label'], 'aria-labelledby': ['aria-labelledby'],
    text: ['text', 'value', 'label-text'], value: ['value'], placeholder: ['placeholder'], title: ['title', 'alt', 'tooltip'],
    alt: ['alt'], name: ['name'], testid: ['testid'], 'test-id': ['testid'], id: ['id'], near: ['near'],
    tooltip: ['tooltip', 'title'], icon: ['icon'], description: ['description']
  };

  /**
   * All accessible names of el: [{name, source, weight}], best first.
   * opts.anyText: use text content as a name for any element (locate), not only clickables.
   */
  function names(el, opts) {
    opts = opts || {};
    var allow = null;
    if (opts.source) {
      allow = {};
      (SOURCE_ALIAS[String(opts.source).toLowerCase()] || [String(opts.source).toLowerCase()])
        .forEach(function (s) { allow[s] = 1; });
    }
    var out = [];
    function add(name, source, extra) {
      name = norm(name);
      if (!name || (allow && !allow[source])) return;
      var n = { name: name, source: source, weight: SOURCE_WEIGHT[source] };
      if (extra) for (var k in extra) n[k] = extra[k];
      out.push(n);
    }
    var t = el.localName, form = !!FORM_TAGS[t];
    var isBtnInput = t === 'input' && !!INPUT_BUTTON[inputType(el)];
    var icons = form ? [] : iconsOf(el);
    if (el.labels) for (var i = 0; i < el.labels.length; i++) add(labelText(el.labels[i]), 'label');
    add(attr(el, 'aria-label'), 'aria-label');
    if (el.hasAttribute('aria-labelledby')) add(labelledbyText(el), 'aria-labelledby');
    if (isBtnInput) add(el.value || attr(el, 'alt'), 'value');
    if (!form && (opts.anyText || isClickable(el) || icons.length)) {
      var tx = t === 'label' ? labelText(el) : displayText(el);
      var glyph = icons.length && icons[0].glyph;
      if (tx.length <= 300 && !glyph) add(tx, t === 'label' ? 'label-text' : 'text');
      if (!tx) {
        var img = el.querySelector('img[alt]');
        if (img) add(attr(img, 'alt'), 'alt');
      }
    }
    if (t === 'img' || t === 'area') add(attr(el, 'alt'), 'alt');
    add(attr(el, 'placeholder'), 'placeholder');
    tooltipsOf(el).forEach(function (tt) { add(tt.text, tt.source); });
    add(attr(el, 'title'), 'title');
    icons.forEach(function (ic) {
      // an icon's own title attr / svg <title> (FA SVG mode renders title="" as <title>)
      if (ic.node !== el && isHtml(ic.node)) add(attr(ic.node, 'title'), 'title');
      if (ic.svg) { var st = ic.node.querySelector('title'); if (st) add(text(st), 'title'); }
      add(ic.name, 'icon', { icon: ic });
    });
    if (attr(el, 'name') && t !== 'a') add(humanize(attr(el, 'name')), 'name');
    (CFG.testIdAttrs || []).forEach(function (a) { if (el.hasAttribute(a)) add(humanize(attr(el, a)), 'testid'); });
    if (el.id && stableId(el.id)) add(humanize(el.id), 'id');
    var labelled = out.some(function (n) { return n.weight >= 1 || n.source === 'placeholder'; });
    var fieldLike = form || el.isContentEditable || /^(textbox|searchbox|combobox|spinbutton)$/.test(role(el));
    if ((fieldLike || opts.near) && !isBtnInput && !labelled && (!allow || allow.near)) {
      var nt = nearText(el);
      if (nt) add(nt.text, 'near');
    }
    out.sort(function (a, b) { return b.weight - a.weight; });
    return out;
  }

  /** Best single human name for el (for descriptions / record mode). Icon-only: the English word ("Refresh"). */
  function describe(el) {
    el = htmlTarget(el);
    if (!el) return '';
    var ns = names(el, { anyText: true, near: true });
    if (!ns.length) return '';
    if (ns[0].source === 'icon') {
      var w = iconWord(ns[0].name);
      return w.charAt(0).toUpperCase() + w.slice(1);
    }
    return ns[0].name;
  }

  /** Title of the closest titled container around el (heading, legend, aria-label...). */
  function containerTitle(el) {
    var a = el.parentElement;
    for (var depth = 0; a && a !== document.body && depth < 12; depth++, a = a.parentElement) {
      if (a.hasAttribute('aria-label') && !isClickable(a)) return norm(attr(a, 'aria-label'));
      if (a.hasAttribute('aria-labelledby')) { var lt = labelledbyText(a); if (lt) return lt; }
      for (var c = a.firstElementChild; c; c = c.nextElementSibling) {
        if (isTitle(c) && !c.contains(el)) return text(c);
        if (c.children.length === 1 && isTitle(c.firstElementChild) && !c.contains(el)) return text(c.firstElementChild);
      }
      if (isA(a, CONTAINER_SEL)) { var ot = ownTitle(a); if (ot && !ot.contains(el)) return text(ot); }
    }
    // Flat layout: nearest preceding heading that shares a parent chain with el.
    var r = snap('preceding::*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6][1]', el);
    var h = r && r.snapshotLength ? r.snapshotItem(0) : null;
    return h && h.parentElement && h.parentElement !== document.body && h.parentElement.contains(el) ? text(h) : '';
  }

  function summary(el, name) {
    var ics = iconsOf(el);
    var n = name != null ? name : (ics.length && !labelOf(el) ? '' : describe(el));
    var s = el.localName;
    if (el.localName === 'input') s += '[' + inputType(el) + ']';
    if (role(el)) s += '[role=' + role(el) + ']';
    if (n) s += ' "' + (n.length > 60 ? n.slice(0, 57) + '...' : n) + '"';
    if (ics.length) s += ' [' + ics.map(iconLabel).join(', ') + ']';
    var ct = containerTitle(el);
    if (ct && ct !== n) s += ' in "' + (ct.length > 50 ? ct.slice(0, 47) + '...' : ct) + '"';
    return s;
  }

  /** Best non-icon name of el (label, aria, text, tooltip, title), or ''. */
  function labelOf(el) {
    var ns = names(el, { anyText: false });
    for (var i = 0; i < ns.length; i++) if (ns[i].source !== 'icon' && ns[i].source !== 'id' && ns[i].source !== 'name') return ns[i].name;
    return '';
  }

  function ordinal(k) {
    var s = ['th', 'st', 'nd', 'rd'], v = k % 100;
    return k + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  /** Where el is, for a human: 'toolbar of "Open Trades", 3rd icon' / 'row 2 (T-2) of "Open Trades"' / 'dialog "Trade Entry"'. */
  function locationOf(el) {
    var ct = containerTitle(el), parts = [];
    var dlg = closestOf(el, DIALOG_SEL);
    var dt = dlg ? (labelledbyText(dlg) || attr(dlg, 'aria-label') || (ownTitle(dlg) ? text(ownTitle(dlg)) : '')) : '';
    var tb = el.parentElement && closestOf(el.parentElement, TOOLBAR_SEL);
    var row = el.parentElement && closestOf(el.parentElement, '[role=row],tr');
    var where = ct ? ' of "' + ct + '"' : '';
    if (row) {
      var rows = row.parentElement ? Array.prototype.filter.call(row.parentElement.children, function (r) { return r.localName === row.localName; }) : [row];
      var cells = row.querySelectorAll('[role=gridcell],td,th');
      var key = cells.length ? text(cells[0]) : '';
      var idx = row.getAttribute('row-index');
      var n = idx != null && /^\d+$/.test(idx) ? +idx + 1 : rows.indexOf(row) + 1;
      parts.push('row ' + n + (key && key.length <= 30 ? ' (' + key + ')' : '') + where);
    } else if (tb) {
      parts.push('toolbar' + where);
    } else if (dlg) {
      parts.push('dialog' + (dt ? ' "' + dt + '"' : ''));
    } else if (ct) {
      parts.push('in "' + ct + '"');
    }
    if (dlg && parts.length && parts[0].indexOf('dialog') !== 0 && dt && dt !== ct) parts[0] += ' in dialog "' + dt + '"';
    var scope = row || tb;
    if (scope && iconsOf(el).length && !displayText(el)) {
      var sibs = Array.prototype.filter.call(scope.querySelectorAll('*'), function (e) {
        return isHtml(e) && isClickable(e) && iconsOf(e).length > 0;
      });
      var k = sibs.indexOf(el);
      if (k >= 0) parts.push(ordinal(k + 1) + ' icon');
    }
    if (!parts.length) parts.push('page');
    return parts.join(', ');
  }

  /** Human evidence strings: 'fa-arrows-rotate', 'tooltip "Filter trades"', 'no tooltip', 'text "Save"'. */
  function evidenceOf(el) {
    var ev = [], ns = names(el, { anyText: false }), labelled = false, seen = {};
    ns.forEach(function (n) {
      if (n.source === 'icon') return;
      var key = n.source + n.name;
      if (seen[key]) return;
      seen[key] = 1;
      if (n.source !== 'id' && n.source !== 'name' && n.source !== 'testid' && n.source !== 'description') labelled = true;
      ev.push((n.source === 'label-text' ? 'text' : n.source) + ' "' + n.name + '"');
    });
    var ics = iconsOf(el);
    ics.forEach(function (ic) { ev.push(iconLabel(ic)); });
    if (ics.length && !ns.some(function (n) { return n.source === 'tooltip' || n.source === 'title' || n.source === 'aria-label'; })) {
      ev.push('no tooltip');
    }
    if (!labelled && !ics.length) ev.push('no label');
    return ev;
  }

  /** Name for a new XML entry: best evidence + context ('Refresh Open Trades', 'Close Trade Entry', 'Save'). */
  function suggestName(el) {
    var label = labelOf(el);
    var ics = iconsOf(el);
    if (label && (!ics.length || displayText(el) || label.length <= 40)) return label;
    if (!ics.length) return describe(el);
    var w = iconWord(ics[0].name);
    w = w.charAt(0).toUpperCase() + w.slice(1);
    var ct = containerTitle(el);
    return ct ? w + ' ' + ct : w;
  }

  /** What an element looked like when an alias was recorded (re-checked when the alias is used). */
  function fingerprint(el) {
    return {
      tag: el.localName,
      icons: iconsOf(el).map(function (i) { return i.name; }).sort(),
      name: labelOf(el),
      context: containerTitle(el)
    };
  }

  // ------------------------------------------------------------------ candidate generation
  function scoreOf(xp, w) {
    var bare = xp.replace(/'[^']*'|"[^"]*"/g, '').replace(/following::[\w*-]+(\[[^\]]*\])?\[1\]/g, '');
    var idx = (bare.match(/\[\d+\]/g) || []).length;
    var s = w - Math.min(20, xp.length / 15) - idx * 8;
    return Math.round(Math.max(0.5, s) * 10) / 10;
  }

  /** Own predicates of el: [{p, s (strategy), w (weight)}]. All literals are escaped. */
  function ownPreds(el) {
    var out = [], t = el.localName;
    function add(p, s, w) { out.push({ p: p, s: s, w: w }); }
    (CFG.testIdAttrs || []).forEach(function (a) {
      var v = attr(el, a);
      if (v && stableText(v) && /^[A-Za-z_][\w.-]*$/.test(a)) add('@' + a + '=' + lit(v), 'test-id', 100);
    });
    var frameEl = t === 'iframe' || t === 'frame';
    // an iframe name/id with a random tail (appFrame-trades-k3j9x2) is only used through its stable head
    if (el.id && stableId(el.id) && !(frameEl && stableHead(el.id))) add('@id=' + lit(el.id), 'id', 90);
    var nm = attr(el, 'name');
    if (nm && stableId(nm) && t !== 'a' && t !== 'form' && !(frameEl && stableHead(nm))) add('@name=' + lit(nm), 'name', 85);
    if (t === 'input' && INPUT_BUTTON[inputType(el)] && attr(el, 'value') && stableText(attr(el, 'value'))) {
      add('@value=' + lit(attr(el, 'value')), 'value', 75);
    }
    var al = attr(el, 'aria-label');
    if (al && stableText(al)) add('@aria-label=' + lit(al), 'aria-label', 80);
    if (!FORM_TAGS[t]) {
      var tx = text(el);
      if (tx && tx.length <= 2000) {
        var click = isClickable(el), dt = displayText(el);
        if (!dt) {
          // all of the text is non-rendered (an FA SVG <title>, hidden content): FA drops the <title> when it
          // re-renders the icon, so this ranks below the icon identity, aria-label and tooltips
          if (tx.length <= 60 && stableText(tx)) add('normalize-space()=' + lit(tx), 'text', 50);
        } else if (dt !== tx && dt.length <= 60 && stableText(dt)) {
          // string-value polluted by icon text (<svg><title>): match the visible part
          var inner = null, ds = el.querySelectorAll('*');
          for (var di = 0; di < ds.length; di++) if (isHtml(ds[di]) && text(ds[di]) === dt) { inner = ds[di]; break; }
          if (inner) add('.//' + tagOf(inner) + '[' + textEq(dt) + ']', 'text', click ? 72 : 66);
          else add('contains(normalize-space(),' + lit(dt) + ')', 'text', click ? 62 : 58);
        } else if (tx.length <= 60 && stableText(tx)) {
          add(textEq(tx), 'text', click ? 75 : 68);
          // the NBSP-insensitive form can match more elements: keep the exact form as a fallback
          if (tx.indexOf(NBSP) >= 0) add('normalize-space()=' + lit(tx), 'text', click ? 73 : 66);
        } else {
          var pre = stablePrefix(tx, 32);
          if (pre) add('starts-with(normalize-space(),' + lit(pre) + ')', 'text', 60);
        }
      } else if (!tx) {
        var img = el.querySelector('img[alt]');
        var alt = img && attr(img, 'alt');
        if (alt && stableText(alt)) add('.//img[@alt=' + lit(alt) + ']', 'alt', 65);
      }
    }
    var ph = attr(el, 'placeholder');
    if (ph && stableText(ph)) add('@placeholder=' + lit(ph), 'placeholder', 70);
    var ti = attr(el, 'title');
    if (ti && stableText(ti)) add('@title=' + lit(ti), 'title', 65);
    var oa = attr(el, 'alt');
    if (oa && stableText(oa) && (t === 'img' || t === 'area' || t === 'input')) add('@alt=' + lit(oa), 'alt', 65);
    if (t === 'a') {
      var href = attr(el, 'href');
      if (href && href !== '#' && !/^(javascript:|data:|blob:)/i.test(href) && stableText(href) && href.length <= 80) {
        add('@href=' + lit(href), 'href', 60);
      }
    }
    // Icon identity on the clickable (FA classes are stable by construction: not subject to class patterns).
    iconsOf(el).slice(0, 2).forEach(function (ic) {
      var ip = iconPred(ic, el);
      if (ip) add(ip, 'icon', 70);
      var sp = iconPred(ic, el, true);
      if (sp && sp !== ip) { add(sp, 'icon', 71); out[out.length - 1].short = true; }
    });
    TOOLTIP_ATTRS.forEach(function (a) {
      var v = attr(el, a);
      if (v && stableText(v)) add('@' + a + '=' + lit(v), 'tooltip', 74);
    });
    if (t === 'iframe' || t === 'frame') iframePreds(el).forEach(function (x) { add(x.p, x.s, x.w); });
    if (t === 'input' && el.hasAttribute('type')) add('@type=' + lit(attr(el, 'type')), 'type', inputType(el) === 'text' ? 35 : 50);
    if (t === 'button' && el.hasAttribute('type')) add('@type=' + lit(attr(el, 'type')), 'type', 45);
    var r = attr(el, 'role');
    // a live region (alert, status, log) is usually the only one of its role: the short role locator ranks high
    if (r && /^[a-z ]+$/.test(r)) add('@role=' + lit(r), 'role', LIVE_ROLES[role(el)] ? 78 : 40);
    var live = attr(el, 'aria-live');
    if (live && /^(polite|assertive)$/.test(live) && !r) add('@aria-live=' + lit(live), 'role', 70);
    var classes = (el.getAttribute('class') || '').split(/[ \t\r\n]+/).filter(function (c) {
      return stableClass(c) && !isFaNoise(c) && !(c.lastIndexOf('fa-', 0) === 0 && iconsOf(el).length);
    });
    classes.slice(0, 3).forEach(function (c) {
      add("contains(concat(' ',normalize-space(@class),' '),' " + c + " ')", 'class', 40);
    });
    out.sort(function (a, b) { return b.w - a.w; });
    return out;
  }

  var CLASS_HAS = function (c) { return "contains(concat(' ',normalize-space(@class),' '),' " + c + " ')"; };
  var LIVE_ROLES = { alert: 1, status: 1, log: 1 };

  /**
   * The short contains(@class,'fa-x') form is exact only while no class anywhere in the document has
   * 'fa-x' as a proper substring (fa-eye vs fa-eye-slash, fa-pen vs fa-pen-to-square). Single-word names are
   * prefixes of other FA names too often to trust the current page alone.
   */
  function faShortSafe(cls) {
    if (!/^fa-[a-z0-9]+-[a-z0-9-]+$/.test(cls)) return false;
    var list = document.querySelectorAll('[class*="' + cls + '"]');
    for (var i = 0; i < list.length; i++) {
      var cs = (list[i].getAttribute('class') || '').split(/[ \t\r\n]+/);
      for (var j = 0; j < cs.length; j++) if (cs[j] !== cls && cs[j].indexOf(cls) >= 0) return false;
    }
    return true;
  }

  /**
   * Predicate (relative to the clickable el) that pins icon ic, e.g. .//i[contains(...,' fa-arrows-rotate ')].
   * short: the contains(@class,'fa-x') form, or '' when it could match a longer FA name (see faShortSafe).
   */
  function iconPred(ic, el, short) {
    if (short) {
      if (!ic.cls || ic.svg || ic.glyph || ic.mat || !faShortSafe(ic.cls)) return '';
      var sp = 'contains(@class,' + lit(ic.cls) + ')';
      return ic.node === el ? sp : './/' + tagOf(ic.node) + '[' + sp + ']';
    }
    var n = ic.node;
    if (ic.glyph) return '';
    if (ic.svg && n.hasAttribute('data-icon')) {
      return n === el ? '@data-icon=' + lit(ic.name) : ".//*[local-name()='svg'][@data-icon=" + lit(ic.name) + ']';
    }
    if (ic.mat) {
      var mp = ic.mat === 'text' ? 'normalize-space()=' + lit(text(n)) : '@' + ic.mat + '=' + lit(ic.name);
      return n === el ? mp : './/mat-icon[' + mp + ']';
    }
    if (!ic.cls) return '';
    if (n === el) return CLASS_HAS(ic.cls);
    var tag = isHtml(n) ? tagOf(n) : "*[local-name()='" + n.localName + "']";
    return './/' + tag + '[' + CLASS_HAS(ic.cls) + ']';
  }

  /** Leading stable part of a name/id with a random tail: 'appFrame-trades-k3j9x2' -> 'appFrame-trades'. */
  function stableHead(v) {
    var parts = String(v).split(/([-_.:])/), out = '';
    for (var i = 0; i < parts.length; i += 2) {
      var seg = parts[i];
      var bad = !seg || /^\d+$/.test(seg) || /\d{3,}/.test(seg) || (/\d/.test(seg) && /[a-z]/i.test(seg) && seg.length >= 5) ||
                /^[0-9a-f]{6,}$/i.test(seg) || anyRx(RX.id, seg);
      if (bad) break;
      out += (i ? parts[i - 1] : '') + seg;
    }
    // a generator prefix (rc_select_17, mat-input-3, cdk-overlay-2) is not a stable head
    if (anyRx(RX.id, out)) return '';
    return out.length >= 4 && out !== v ? out : '';
  }

  /** Extra predicates for an iframe: stable name/id head (page key) and the src path's file name. */
  function iframePreds(el) {
    var out = [];
    ['name', 'id'].forEach(function (a) {
      var v = attr(el, a);
      var h = v && stableHead(v);
      if (h) out.push({ p: 'contains(@' + a + ',' + lit(h) + ')', s: a, w: a === 'name' ? 82 : 78 });
    });
    var src = attr(el, 'src').split(/[?#]/)[0];
    var file = src.split('/').filter(Boolean).pop() || '';
    if (file && !/^(about:|javascript:|data:|blob:)/i.test(src) && /^[\w.-]{3,60}$/.test(file) && stableText(file) &&
        !/\d{3,}/.test(file) && file.indexOf(':') < 0) {
      out.push({ p: 'contains(@src,' + lit(file) + ')', s: 'src', w: 62 });
    }
    return out;
  }

  // AG Grid rows are handled by crawler/grid (row identity x column), never by the static row-key anchor.
  var AG_SEL = 'ag-grid-angular,ag-grid-vue,ag-grid-react,.ag-root-wrapper,.ag-row,[row-index]';
  var KEY_HEADER = /(^|[^a-z])(id|code|key|ref|no|num|number|#)([^a-z]|$)/i;
  var KEY_VALUE = /^[A-Za-z]{0,6}[-_\/.]?\d+([-_\/.]?[A-Za-z0-9]+)*$/;

  /** Rows of the repeated structure a belongs to (a table row, [role=row], a repeated list item), or null. */
  function rowSiblings(a) {
    var p = a.parentElement;
    if (!p || !isHtml(a) || closestOf(a, AG_SEL)) return null;
    var r = role(a);
    var isRow = a.localName === 'tr' || r === 'row' || r === 'listitem' || a.localName === 'li';
    if (!isRow) return null;
    var sibs = Array.prototype.filter.call(p.children, function (x) { return x.localName === a.localName && role(x) === r; });
    return sibs.length >= 2 ? sibs : null;
  }

  /** Cells (or, for list items, short leaf texts) of row a that do not contain el: [{el, step, text}]. */
  function rowCells(a, el) {
    var out = [];
    if (a.localName === 'tr' || role(a) === 'row') {
      for (var c = a.firstElementChild; c; c = c.nextElementSibling) {
        if (!isHtml(c) || c.contains(el)) continue;
        if (/^(td|th)$/.test(c.localName) || /^(gridcell|cell|rowheader)$/.test(role(c))) {
          out.push({ el: c, step: tagOf(c), text: text(c) });
        }
      }
    } else {
      var leaves = a.querySelectorAll('*');
      for (var i = 0; i < leaves.length && out.length < 12; i++) {
        var e = leaves[i];
        if (!isHtml(e) || e.firstElementChild || e.contains(el) || el.contains(e) || isClickable(e)) continue;
        out.push({ el: e, step: './/' + tagOf(e), text: text(e) });
      }
    }
    return out;
  }

  /**
   * Row-key anchors for row a: //tr[td[normalize-space()='B-2']] on a cell text that no sibling row has
   * (key-like columns first: an Id/Code header or an id-shaped value). The key is data: callers label the
   * resulting candidates "row-key" (data-dependent), which still beats a positional index.
   */
  function rowKeyXPaths(a, el) {
    var sibs = rowSiblings(a);
    if (!sibs) return [];
    var t = tagOf(a), table = closestOf(a, 'table'), heads = null;
    if (table && a.localName === 'tr') {
      var hr = table.querySelector('thead tr') || table.querySelector('tr');
      if (hr && hr !== a) heads = Array.prototype.map.call(hr.children, function (h) { return text(h); });
    }
    var keys = [], others = new Set();
    sibs.forEach(function (r) { if (r !== a) rowCells(r, el).forEach(function (o) { others.add(o.text); }); });
    rowCells(a, el).forEach(function (c, idx) {
      var tx = c.text;
      if (!tx || tx.length > 40 || others.has(tx)) return;
      var col = c.el.parentElement === a ? Array.prototype.indexOf.call(a.children, c.el) : -1;
      var k = 0;
      if (heads && col >= 0 && KEY_HEADER.test(heads[col] || '')) k += 3;
      if (KEY_VALUE.test(tx)) k += 2;
      if (idx === 0) k += 1;
      if (stableText(tx)) k += 1;
      keys.push({ c: c, k: k });
    });
    keys.sort(function (x, y) { return y.k - x.k || x.c.text.length - y.c.text.length; });
    return keys.slice(0, 2).map(function (x, i) {
      return { xp: '//' + t + '[' + x.c.step + '[' + keyEq(x.c.text) + ']]', w: 62 - i, kind: 'row-key' };
    });
  }

  /**
   * Row-key cell predicate. An id-shaped key (B-2, T-1001) must be the cell's first word, so a badge or
   * note added after it ("B-2 (new)") keeps the row while B-20 never matches; other keys match exactly.
   */
  function keyEq(k) {
    if (KEY_VALUE.test(k)) return "starts-with(concat(normalize-space(),' ')," + lit(k + ' ') + ')';
    return textEq(k);
  }

  /** First title element in a's subtree, or null. */
  function firstTitle(a) {
    var ts = a.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading],' + TITLE_SEL);
    for (var i = 0; i < ts.length && i < 40; i++) if (text(ts[i])) return ts[i];
    return null;
  }

  /** Anchor xpaths for ancestor a of el, best first: [{xp, w, kind}]. Unverified. */
  function anchorXPaths(a, el) {
    var out = [], t = tagOf(a);
    function add(xp, w, kind) { out.push({ xp: xp, w: w, kind: kind }); }
    (CFG.testIdAttrs || []).forEach(function (at) {
      var v = attr(a, at);
      if (v && stableText(v) && /^[A-Za-z_][\w.-]*$/.test(at)) add('//' + t + '[@' + at + '=' + lit(v) + ']', 100, 'test-id');
    });
    if (a.id && stableId(a.id)) add('//' + t + '[@id=' + lit(a.id) + ']', 90, 'id');
    if (t === 'form' && attr(a, 'name') && stableId(attr(a, 'name'))) add('//form[@name=' + lit(attr(a, 'name')) + ']', 85, 'name');
    var al = attr(a, 'aria-label');
    if (al && stableText(al) && !isClickable(a)) {
      add('//' + t + '[@aria-label=' + lit(al) + ']', 80, 'aria-label');
      if (role(a)) add('//' + t + '[@role=' + lit(attr(a, 'role')) + ' and @aria-label=' + lit(al) + ']', 79, 'aria-label');
    }
    var lb = labelledbyEls(a);
    if (lb.length === 1) {
      var lt = text(lb[0]);
      if (lt && lt.length <= 60 && stableText(lt)) {
        var linner = '//' + tagOf(lb[0]) + '[' + textEq(lt) + ']';
        add('//' + t + '[@aria-labelledby=' + linner + '/@id]', 76, 'aria-labelledby');
        out[out.length - 1].check = function (el) { return refUnique(t, 'aria-labelledby', linner, 'id', el); };
      }
    }
    function headPred(h) {
      var ht = text(h);
      if (!ht || ht.length > 60 || !stableText(ht)) return '';
      var ht0 = role(h) === 'heading' && !HEADING_TAGS[h.localName] ? "*[@role='heading' and " : tagOf(h) + '[';
      return ht0 + textEq(ht) + ']';
    }
    for (var c = a.firstElementChild; c; c = c.nextElementSibling) {
      if (isTitle(c)) { var hp = headPred(c); if (hp) { add('//' + t + '[' + hp + ']', 78, 'heading'); break; } }
    }
    // Grandchild heading only through a wrapper that holds just the title (header, hgroup, title div).
    c = a.firstElementChild;
    if (c && !isTitle(c)) {
      var g = c.firstElementChild;
      if (g && isTitle(g) && (c.children.length === 1 || /^(header|hgroup|mat-card-header)$/.test(c.localName))) {
        var gp = headPred(g);
        if (gp) add('//' + t + '[' + tagOf(c) + '/' + gp + ']', 74, 'heading');
      }
    }
    // Heading 2-3 levels down (card > header > h3 + meta): anchor on the tightest container whose subtree's
    // FIRST title it is, with the exact child path (//div[div/h3[...]]), never the loose //div[.//h3[...]].
    var fh = firstTitle(a);
    if (fh && el && !fh.contains(el)) {
      var steps = [], n = fh.parentElement;
      while (n && n !== a && steps.length < 3) { steps.unshift(tagOf(n)); n = n.parentElement; }
      var top = steps.length ? fh.parentElement : null;
      while (top && top.parentElement !== a) top = top.parentElement;
      // the wrapper holding the title must be a header, not a nested card/section with its own content
      var wrapperOk = n === a && steps.length >= 1 && steps.length <= 2 && top && !isA(top, CONTAINER_SEL) &&
        !top.querySelector(CONTAINER_SEL) && titlesIn(top).length === 1 && !top.contains(el);
      if (wrapperOk) {
        var fp = headPred(fh);
        // one header wrapper: right after a heading child; deeper: below the shorter //card[.//title] form
        if (fp) add('//' + t + '[' + steps.join('/') + '/' + fp + ']', steps.length === 1 ? 77 : 70, 'heading');
      }
    }
    // A card/panel/dialog whose title sits deeper (mat-card > mat-card-header > div > mat-card-title).
    if (isA(a, CONTAINER_SEL)) {
      var ot = ownTitle(a);
      if (ot && (!c || (ot !== c && ot.parentElement !== a))) {
        var op = headPred(ot);
        if (op) add('//' + t + '[.//' + op + ']', 72, 'heading');
      }
    }
    if (el) rowKeyXPaths(a, el).forEach(function (r) { add(r.xp, r.w, r.kind); });
    return out;
  }

  /** Up to `max` anchors for el: nearest titled/identified ancestors with a unique xpath. */
  function anchorsOf(el, max) {
    var res = [], a = el.parentElement;
    for (var depth = 0; a && depth < 8 && res.length < max; depth++, a = a.parentElement) {
      if (a === document.body || a === document.documentElement || !isHtml(a)) break;
      var xs = anchorXPaths(a, el).sort(function (x, y) { return y.w - x.w; });
      for (var i = 0; i < xs.length; i++) {
        if (xs[i].check ? xs[i].check(a) : uniqueIs(xs[i].xp, a)) {
          res.push({ el: a, xp: xs[i].xp, w: xs[i].w, kind: xs[i].kind });
          break;
        }
      }
    }
    return res;
  }

  function stepOf(n) {
    var tag = tagOf(n), p = n.parentElement, idx = 0, total = 0;
    for (var s = p ? p.firstElementChild : n; s; s = s.nextElementSibling) {
      if (tag === '*' || s.localName === n.localName) { total++; if (s === n) idx = total; }
    }
    return total > 1 ? tag + '[' + idx + ']' : tag;
  }

  function positional(el, anchor) {
    if (el === document.documentElement) return '/html';
    if (el === document.body) return '/html/body';
    var stop = anchor ? anchor.el : document.body, steps = [], n = el;
    while (n && n !== stop) {
      if (n === document.documentElement || !n.parentElement) return null;
      steps.unshift(stepOf(n));
      n = n.parentElement;
    }
    if (!n) return null;
    return (anchor ? anchor.xp : '/html/body') + '/' + steps.join('/');
  }

  /** Anchored candidates rank below placeholder/title and above near-text (GOALS G1). */
  function anchoredWeight(aw, iw) { return 48 + 16 * (0.35 * aw + 0.65 * iw) / 100; }

  /** Verified locator candidates for el, best first. */
  function candidates(el0) {
    var el = htmlTarget(el0);
    if (!el || !el.isConnected) return [];
    var memo = new Map(), out = new Map();
    function ok(xp, check) {
      if (!memo.has(xp)) memo.set(xp, check ? !!check() : uniqueIs(xp, el));
      return memo.get(xp);
    }
    /** `check` is an exact, cheaper equivalent of "xp selects exactly el" (see refSelect, anchors). */
    function add(xp, strategy, w, check) {
      if (out.has(xp) || !ok(xp, check)) return false;
      out.set(xp, { xpath: xp, strategy: strategy, score: scoreOf(xp, w) });
      return true;
    }
    var t = tagOf(el), preds = ownPreds(el), anyUnique = false;

    // 1-6: single own predicates
    preds.forEach(function (pr) { if (add('//' + t + '[' + pr.p + ']', pr.s, pr.w)) anyUnique = true; });

    // 4: labels
    if (el.labels) {
      for (var i = 0; i < el.labels.length; i++) {
        var l = el.labels[i];
        if (!l.contains(el) && el.id && attr(l, 'for') === el.id) {
          var full = text(l);
          var linner = '';
          if (full && full.length <= 80 && stableText(full)) linner = '//label[normalize-space()=' + lit(full) + ']';
          else if (full && stablePrefix(full, 40)) linner = '//label[starts-with(normalize-space(),' + lit(stablePrefix(full, 40)) + ')]';
          if (linner && add('//' + t + '[@id=' + linner + '/@for]', 'label', linner.indexOf('starts-with') > 0 ? 72 : 82,
                            refUnique.bind(null, t, 'id', linner, 'for', el))) anyUnique = true;
        } else if (l.contains(el)) {
          var segs = labelSegments(l), whole = text(l);
          var seg = segs.filter(function (x) { return x.length >= 3 && stableText(x); })[0] || segs[0];
          if (seg && seg.length <= 80 && stableText(seg)) {
            var L = whole === seg ? '//label[normalize-space()=' + lit(seg) + ']'
                                  : '//label[contains(normalize-space(.),' + lit(seg) + ')]';
            if (add(L + '//' + t, 'label', 78)) anyUnique = true;
            var tp = preds.filter(function (p) { return p.s === 'type' || p.s === 'name'; })[0];
            if (tp && add(L + '//' + t + '[' + tp.p + ']', 'label', 77)) anyUnique = true;
          }
        }
      }
    }
    // aria-labelledby
    var lbs = labelledbyEls(el);
    if (lbs.length === 1) {
      var ref = lbs[0], rt = text(ref);
      if (rt && rt.length <= 80 && stableText(rt)) {
        var rinner = '//' + tagOf(ref) + '[normalize-space()=' + lit(rt) + ']';
        if (add('//' + t + '[@aria-labelledby=' + rinner + '/@id]', 'aria-labelledby', 78,
                refUnique.bind(null, t, 'aria-labelledby', rinner, 'id', el))) anyUnique = true;
      }
      var lbv = attr(el, 'aria-labelledby');
      if (stableId(lbv) && add('//' + t + '[@aria-labelledby=' + lit(lbv) + ']', 'aria-labelledby', 79)) anyUnique = true;
    }
    // Material tooltip: aria-describedby -> hidden cdk-describedby message (random id, stable text)
    tooltipsOf(el).forEach(function (tt) {
      if (tt.source !== 'tooltip' || !tt.el || attr(el, 'aria-describedby').split(/\s+/).length !== 1) return;
      var tx = tt.text;
      if (tx.length > 80 || !stableText(tx)) return;
      var tinner = '//' + tagOf(tt.el) + '[normalize-space()=' + lit(tx) + ']';
      if (add('//' + t + '[@aria-describedby=' + tinner + '/@id]', 'tooltip', 73,
              refUnique.bind(null, t, 'aria-describedby', tinner, 'id', el))) anyUnique = true;
    });

    // 7: pairs of own predicates when nothing single is unique
    if (!anyUnique) {
      var pp = preds.slice(0, 8);
      for (var a = 0; a < pp.length; a++) {
        for (var b = a + 1; b < pp.length; b++) {
          if (pp[a].s === pp[b].s && pp[a].s !== 'class') continue;
          add('//' + t + '[' + pp[a].p + ' and ' + pp[b].p + ']', pp[a].s, Math.min(pp[a].w, pp[b].w) - 5);
        }
      }
    }
    // unique tag alone (e.g. the only textarea)
    add('//' + t, 'tag', 30);

    // 8: anchored to stable ancestors
    var anchors = anchorsOf(el, 3);
    // A.xp selects exactly A.el, so `${A.xp}//rest` selects what `.//rest` selects from A.el.
    function rel(A, path) { return function () { return uniqueIs('.' + path, el, A.el); }; }
    // Icon clickables are anchored on their icon identity (or a better own predicate), not on @type/bare tag.
    var iconEl = preds.some(function (p) { return p.s === 'icon'; });
    anchors.forEach(function (A) {
      var st = A.kind === 'row-key' ? 'row-key' : 'anchored';
      preds.slice(0, 5).forEach(function (pr) {
        if (iconEl && pr.w < 60) return;
        var path = '//' + t + '[' + pr.p + ']';
        add(A.xp + path, st, anchoredWeight(A.w, pr.w), rel(A, path));
      });
      if (!iconEl) add(A.xp + '//' + t, st, anchoredWeight(A.w, 25), rel(A, '//' + t));
      var list = all('.//' + t, A.el), k = list.indexOf(el);
      if (k >= 0 && list.length > 1) {
        add('(' + A.xp + '//' + t + ')[' + (k + 1) + ']', 'anchored', 30, function () { return list[k] === el; });
      }
    });

    // 9: near-text for unlabeled fields
    if (FORM_TAGS[t] || el.isContentEditable || role(el) === 'textbox' || role(el) === 'combobox') {
      var nt = nearText(el);
      if (nt) {
        var base = '//' + tagOf(nt.el) + '[normalize-space()=' + lit(nt.text) + ']';
        if (nt.contains) add(base + '//' + t, 'near-text', 50);
        else {
          add(base + '/following::' + t + '[1]', 'near-text', 50);
          var tpr = preds.filter(function (p) { return p.s === 'type'; })[0];
          if (tpr) add(base + '/following::' + t + '[' + tpr.p + '][1]', 'near-text', 49);
        }
      }
    }

    // 9b: document-order index on the best non-unique own predicates (fragile, but beats a path)
    var strongCount = 0;
    out.forEach(function (c) { if (c.score >= 45) strongCount++; });
    if (strongCount < 2) {
      // (the exact FA class form only: a positional fallback should not also be class-dependent)
      preds.filter(function (p) { return p.w >= 60 && !p.short; }).slice(0, 2).forEach(function (pr) {
        var xs = '//' + t + '[' + pr.p + ']', lst = all(xs), k = lst.indexOf(el);
        if (k >= 0 && lst.length > 1 && lst.length <= 20) add('(' + xs + ')[' + (k + 1) + ']', 'indexed', 35);
      });
    }

    // 10: positional fallback (always produce one)
    var pos = positional(el, anchors[0]);
    if (pos) add(pos, 'positional', 10, anchors[0] ? rel(anchors[0], pos.slice(anchors[0].xp.length)) : null);
    if (!out.size) {
      pos = positional(el, null);
      if (pos) add(pos, 'positional', 5);
    }

    var res = Array.from(out.values());
    res.sort(function (x, y) { return y.score - x.score || x.xpath.length - y.xpath.length; });
    return res.slice(0, MAX_CANDIDATES);
  }

  // ------------------------------------------------------------------ resolver
  function match(q, n) {
    if (!q || !n) return 0;
    if (q === n) return 1;
    var r = q.length / n.length, s = 0;
    if (n.lastIndexOf(q, 0) === 0) {
      s = n.charAt(q.length) === ' ' ? 0.75 + 0.1 * r : 0.4 + 0.2 * r;
    } else if ((' ' + n + ' ').indexOf(' ' + q + ' ') >= 0) {
      s = 0.55 + 0.25 * r;
    }
    var qt = q.split(' '), nt = n.split(' '), ns = {}, qs = {}, inter = 0, qn = 0, nn = 0;
    nt.forEach(function (x) { if (!ns[x]) { ns[x] = 1; nn++; } });
    qt.forEach(function (x) { if (!qs[x]) { qs[x] = 1; qn++; if (ns[x]) inter++; } });
    var jac = inter / (qn + nn - inter);
    return Math.max(s, jac * 0.6);
  }

  var POOL_SEL = {
    fill: 'input,textarea,select,[contenteditable]:not([contenteditable=false]),[role=textbox],[role=searchbox],[role=combobox],[role=spinbutton]',
    select: 'select,[role=combobox],[role=listbox],[aria-haspopup=listbox]',
    check: 'input[type=checkbox],input[type=radio],[role=checkbox],[role=radio],[role=switch],[role=menuitemcheckbox],[role=menuitemradio]',
    click: 'button,a,summary,label,input[type=button],input[type=submit],input[type=reset],input[type=image],' +
           'input[type=checkbox],input[type=radio],[role=button],[role=link],[role=tab],[role=menuitem],' +
           '[role=menuitemcheckbox],[role=menuitemradio],[role=option],[role=checkbox],[role=radio],[role=switch],' +
           '[role=treeitem],[onclick],[ng-click],[data-ng-click],[routerlink],[mat-menu-item],[mat-dialog-close],[tabindex]'
  };
  POOL_SEL.uncheck = POOL_SEL.check;
  POOL_SEL.hover = POOL_SEL.click + ',[aria-haspopup],[title]';
  POOL_SEL.press = POOL_SEL.fill + ',' + POOL_SEL.click;
  POOL_SEL.locate = '*';

  function inPool(el, action) {
    if (!isHtml(el)) return false;
    var t = el.localName;
    if (t === 'input' && (action === 'fill' || action === 'press') && NOT_FILLABLE[inputType(el)]) {
      return action === 'press' && inputType(el) !== 'hidden';
    }
    if (t === 'input' && inputType(el) === 'hidden') return false;
    if (/^(script|style|head|meta|link|title|template|noscript|html|body)$/.test(t)) return false;
    if ((action === 'click' || action === 'hover') && el.hasAttribute('tabindex') && !el.matches(POOL_SEL.click.replace(',[tabindex]', ''))) {
      return parseInt(attr(el, 'tabindex'), 10) >= 0;
    }
    return true;
  }

  /** Title/heading elements of container a that belong to it (not to a nested container). */
  function titlesIn(a) {
    var ts = a.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading],' + TITLE_SEL), out = [];
    for (var i = 0; i < ts.length; i++) {
      var c = ts[i].parentElement && closestOf(ts[i].parentElement, CONTAINER_SEL);
      if (c === a || !c || !a.contains(c)) out.push(ts[i]);
    }
    return out;
  }

  /** Containers whose title matches `title`: [{root, title, after, before}]. */
  function findContainersRaw(title) {
    var q = canon(title), scored = [];
    var els = document.querySelectorAll(
      'h1,h2,h3,h4,h5,h6,legend,caption,summary,[role=heading],[aria-label],[aria-labelledby],' + TITLE_SEL);
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (!isHtml(e)) continue;
      var name, self = false;
      if (e.hasAttribute('aria-label') && !isClickable(e) && !FORM_TAGS[e.localName]) { name = attr(e, 'aria-label'); self = true; }
      else if (e.hasAttribute('aria-labelledby') && !isClickable(e) && !FORM_TAGS[e.localName]) { name = labelledbyText(e); self = true; }
      else if (isTitle(e) || e.localName === 'summary') name = text(e);
      else continue;
      var s = match(q, canon(name));
      if (s >= THRESHOLD) scored.push({ el: e, s: s, self: self });
    }
    if (!scored.length) return [];
    var best = Math.max.apply(null, scored.map(function (x) { return x.s; }));
    var out = [];
    scored.filter(function (x) { return x.s >= best - TIE; }).forEach(function (x) {
      if (x.self) { out.push({ root: x.el, title: x.el }); return; }
      var h = x.el;
      if (h.localName === 'legend') { out.push({ root: h.closest('fieldset') || h.parentElement, title: h }); return; }
      if (h.localName === 'caption') { out.push({ root: h.closest('table') || h.parentElement, title: h }); return; }
      if (h.localName === 'summary') { out.push({ root: h.closest('details') || h.parentElement, title: h }); return; }
      // A card/panel/dialog title names the whole card (mat-card-title, .panel-title, [mat-dialog-title], or the
      // only heading of a section/card).
      var box = h.parentElement && closestOf(h.parentElement, CONTAINER_SEL);
      if (box && box !== document.body && (isA(h, TITLE_SEL) || titlesIn(box).length === 1)) {
        out.push({ root: box, title: h });
        if (isA(h, TITLE_SEL)) return;
      }
      var a = h.parentElement;
      while (a && a !== document.body && a.children.length === 1) a = a.parentElement;
      if (!a) return;
      var c = { root: a, title: h, after: null, before: null };
      // Flat layout: several same-level headings in one container -> section = heading..next heading.
      var lvl = /^h([1-6])$/.exec(h.localName);
      if (lvl) {
        var hs = a.querySelectorAll('h1,h2,h3,h4,h5,h6');
        var seen = false;
        for (var k = 0; k < hs.length; k++) {
          if (hs[k] === h) { seen = true; continue; }
          var l2 = +hs[k].localName.charAt(1);
          if (l2 <= +lvl[1]) {
            if (seen) { c.before = hs[k]; break; }
            c.after = h;
          }
        }
        if (c.before) c.after = h;
      }
      out.push(c);
    });
    return out;
  }

  // "the Trade Entry dialog", "Open Trades grid", "Open Trades toolbar": a trailing role word narrows the container.
  var ROLE_WORDS = {
    dialog: 'dialog', modal: 'dialog', popup: 'dialog', window: 'dialog', grid: 'grid', table: 'grid',
    toolbar: 'toolbar', panel: '', section: '', card: '', tab: '', form: '', page: '', area: '', box: ''
  };

  function findContainers(title) {
    var q = canon(title).replace(/^the /, '');
    var m = /^(.*\S) ([a-z]+)$/.exec(q);
    if (m && ROLE_WORDS.hasOwnProperty(m[2])) {
      var kind = ROLE_WORDS[m[2]], res = findContainersRaw(m[1]);
      if (kind === 'dialog') res = res.filter(function (c) { return !!closestOf(c.root, DIALOG_SEL); });
      else if (kind === 'grid') res = res.filter(function (c) { return isA(c.root, GRID_SEL) || !!c.root.querySelector(GRID_SEL); });
      else if (kind === 'toolbar') {
        var tbs = [];
        res.forEach(function (c) {
          var list = c.root.querySelectorAll(TOOLBAR_SEL);
          if (isA(c.root, TOOLBAR_SEL)) tbs.push(c);
          for (var i = 0; i < list.length; i++) if (inContainer(list[i], c)) tbs.push({ root: list[i], title: c.title });
        });
        res = tbs;
      }
      if (res.length) return res;
    }
    return findContainersRaw(q);
  }

  function inContainer(el, c) {
    if (!c.root.contains(el)) return false;
    if (c.after && !(c.after.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) return false;
    if (c.before && !(c.before.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING)) return false;
    return true;
  }

  // Target.kind -> role pool (icon is special: elements named by icon evidence).
  var KIND_SEL = {
    button: 'button,input[type=button],input[type=submit],input[type=reset],input[type=image],[role=button],' +
            'a:not([href]),a[href="#"],a[href^="javascript:"],a.btn,[mat-button],[mat-icon-button],[mat-raised-button],' +
            '[mat-flat-button],[mat-stroked-button],[mat-fab],[mat-mini-fab]',
    link: 'a[href],[role=link]',
    tab: '[role=tab],.mat-mdc-tab,.mat-tab-label',
    checkbox: 'input[type=checkbox],input[type=radio],[role=checkbox],[role=radio],[role=switch],mat-checkbox,mat-slide-toggle',
    dropdown: 'select,[role=combobox],[role=listbox],[aria-haspopup=listbox],mat-select',
    field: 'input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=reset]):' +
           'not([type=image]):not([type=hidden]),textarea,select,[contenteditable]:not([contenteditable=false]),' +
           '[role=textbox],[role=searchbox],[role=spinbutton],[role=combobox]',
    menu: '[role=menuitem],[role=menuitemcheckbox],[role=menuitemradio],[aria-haspopup=menu],[aria-haspopup=true],' +
          '[mat-menu-item],.mat-mdc-menu-item,[mat-menu-trigger-for],[matmenutriggerfor]',
    dialog: '[role=dialog],[role=alertdialog],dialog,mat-dialog-container'
  };
  function kindOk(el, kind) {
    if (!kind) return true;
    if (kind === 'icon') return iconsOf(el).length > 0;
    var sel = KIND_SEL[kind];
    if (!sel) return true;
    if (isA(el, sel)) return true;
    // a link that is really a button (no real href) still counts as a button
    return kind === 'button' && el.localName === 'a' && !attr(el, 'href');
  }

  function collect(sel, containers, action, kind) {
    var seen = new Set(), out = [];
    if (kind && KIND_SEL[kind] && sel !== '*') sel += ',' + KIND_SEL[kind];
    var icons = action === 'click' || action === 'hover' || kind === 'icon';
    containers.forEach(function (c) {
      var list = c.root.querySelectorAll(sel);
      for (var i = 0; i < list.length; i++) {
        var e = list[i];
        if (!seen.has(e) && inContainer(e, c) && inPool(e, action)) { seen.add(e); out.push(e); }
      }
      if (!icons) return;
      // icon clickables without clickable markup (Angular (click) on an <i>/<span>/<svg>): the icon's owner
      var ics = c.root.querySelectorAll(ICON_SEL);
      for (var j = 0; j < ics.length; j++) {
        if (!iconOf(ics[j])) continue;
        var o = iconOwner(ics[j]);
        if (o && !seen.has(o) && isHtml(o) && inContainer(o, c)) { seen.add(o); out.push(o); }
      }
    });
    return out;
  }

  function docOrder(a, b) {
    if (a === b) return 0;
    return a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
  }

  /** Drop matches that contain another match, and labels whose control matched too; document order. */
  function dedupe(list) {
    var out = list.filter(function (m) { return !list.some(function (o) { return o !== m && m.el.contains(o.el); }); });
    out = out.filter(function (m) {
      return !(m.el.localName === 'label' && m.el.control && out.some(function (o) { return o.el === m.el.control; }));
    });
    out.sort(function (a, b) { return docOrder(a.el, b.el); });
    return out;
  }

  function pickTies(matches) {
    if (!matches.length) return [];
    matches.sort(function (a, b) { return b.score - a.score; });
    var best = matches[0].score;
    return dedupe(matches.filter(function (m) { return m.score >= best - TIE; }));
  }

  function found(m, action) {
    var r = {
      status: 'found', el: m.el, matchedBy: m.source === 'label-text' ? 'text' : m.source, score: Math.round(m.score * 1000) / 1000,
      description: summary(m.el, m.name), name: m.name, action: action, tier: m.tier || 'exact',
      context: containerTitle(m.el), location: locationOf(m.el), evidence: evidenceOf(m.el)
    };
    if (m.kindFallback) r.note = 'no ' + m.kindFallback + ' matched; this element is not a ' + m.kindFallback;
    return r;
  }

  /** Best name of e for canonical query q: {el, score, source, name, tier: exact|fuzzy|icon}. */
  function bestName(e, q, source, anyText) {
    var ns = names(e, { anyText: anyText, source: source });
    var best = null;
    for (var i = 0; i < ns.length; i++) {
      var n = ns[i], mm, tier;
      if (n.source === 'icon') { var im = iconMatch(q, n.name); mm = im.m; tier = im.tier; }
      else { mm = match(q, canon(n.name)); tier = mm === 1 ? 'exact' : 'fuzzy'; }
      var s = n.weight * mm;
      if (!best || s > best.score + 1e-9 || (Math.abs(s - best.score) < 1e-9 && tier === 'exact' && best.tier !== 'exact')) {
        best = { el: e, score: s, source: n.source, name: n.source === 'icon' ? '' : n.name, tier: tier,
                 icon: n.source === 'icon' ? n.name : '' };
      }
    }
    return best;
  }

  /** Every pool element with a positive match, best first (no visibility filtering). */
  function scan(spec, action, containers, q) {
    var pool = collect(POOL_SEL[action] || POOL_SEL.locate, containers, action, spec.kind);
    var out = [];
    pool.forEach(function (e) {
      if (!kindOk(e, spec.kind)) return;
      var b = bestName(e, q, spec.source, action === 'locate');
      if (b && b.score > 0) out.push(b);
    });
    out.sort(function (a, b) { return b.score - a.score; });
    return out;
  }

  function resolveDirect(spec, containers) {
    var els;
    if (spec.xpath) {
      var r = snap(spec.xpath);
      if (!r) return { status: 'error', message: 'Invalid XPath: ' + spec.xpath };
      els = [];
      for (var i = 0; i < r.snapshotLength; i++) if (r.snapshotItem(i).nodeType === 1) els.push(r.snapshotItem(i));
    } else {
      try { els = Array.from(document.querySelectorAll(spec.css)); }
      catch (e) { return { status: 'error', message: 'Invalid CSS selector: ' + spec.css }; }
    }
    if (containers) els = els.filter(function (e) { return containers.some(function (c) { return inContainer(e, c); }); });
    var how = spec.xpath ? 'xpath' : 'css';
    if (!els.length) return { status: 'none', near: [], reason: 'no element matches ' + how };
    if (spec.nth) {
      if (spec.nth > els.length) return { status: 'error', message: 'nth=' + spec.nth + ' but only ' + els.length + ' element(s) match the ' + how };
      return found({ el: els[spec.nth - 1], source: how, score: 1, name: describe(els[spec.nth - 1]) }, spec.action);
    }
    if (els.length > 1) {
      var alts = els.slice(0, 10).map(function (e) { return summary(e); });
      if (els.length > 10) alts.push('... and ' + (els.length - 10) + ' more');
      return { status: 'ambiguous', alternatives: alts, count: els.length };
    }
    return found({ el: els[0], source: how, score: 1, name: describe(els[0]) }, spec.action);
  }

  /**
   * Resolve a target spec {query, source, xpath, css, within, nth, action, diagnose}.
   * Returns {status: 'found'|'ambiguous'|'none'|'error', ...}. Never guesses between ties.
   */
  function resolve(spec) {
    try {
      return resolveInner(spec || {});
    } catch (e) {
      return { status: 'error', message: 'engine error: ' + (e && e.message || e) };
    }
  }

  function resolveInner(spec) {
    var action = spec.action || 'locate';
    if (action === 'uncheck') action = 'check';
    if (/^(right_click|double_click|dblclick|context_click)$/.test(action)) action = 'click';
    if (!document.body) return { status: 'none', near: [], reason: 'document has no body yet' };
    var containers = null;
    if (spec.within) {
      containers = findContainers(spec.within);
      if (!containers.length) return { status: 'none', near: [], reason: 'no container titled "' + spec.within + '"' };
    }
    if (spec.xpath || spec.css) return resolveDirect(spec, containers);
    if (!spec.query) return { status: 'error', message: 'target needs a query, xpath or css' };
    containers = containers || [{ root: document.body }];
    var r = resolveQuery(spec, action, containers);
    if (r.status === 'none' && spec.kind) {
      // Nothing of that kind: other elements are accepted only as the single inexact candidate.
      var r2 = resolveQuery(Object.assign({}, spec, { kind: null, kindFallback: spec.kind }), action, containers);
      if (r2.status !== 'none') return r2;
      r.reason = r.reason || 'no ' + spec.kind + ' matches';
    }
    return r;
  }

  /** Clickable by markup, by an onclick property, inside a clickable, or styled as clickable (pointer cursor). */
  function interactive(e) {
    for (var a = e; a && a !== document.body && a !== document.documentElement; a = a.parentElement) {
      if (isHtml(a) && (isClickable(a) || typeof a.onclick === 'function')) return true;
    }
    return pointer(e);
  }

  /** The topmost open modal dialog (its backdrop blocks everything behind it), or null. */
  function topModal() {
    var list = document.querySelectorAll('[aria-modal=true],mat-dialog-container,.mat-mdc-dialog-container,dialog[open]');
    var top = null;
    for (var i = 0; i < list.length; i++) {
      var d = list[i];
      if (d.localName === 'dialog' && !isA(d, ':modal')) continue;
      if (visible(d)) top = d;   // document order: a later overlay is stacked above an earlier one
    }
    return top;
  }

  /** A located toast/alert text: the live region (role=status|alert|log, aria-live) whose whole text it is. */
  function promoteLive(el) {
    var c = el.parentElement && closestOf(el.parentElement, '[role=status],[role=alert],[role=log],[aria-live]');
    return c && c !== document.body && text(c) === text(el) ? c : el;
  }

  function resolveQuery(spec, action, containers) {
    var q = canon(spec.query);
    var all = scan(spec, action, containers, q);
    var matches = [], hidden = [], near = [];
    all.forEach(function (b) {
      if (spec.kindFallback) { b.tier = 'fuzzy'; b.kindFallback = spec.kindFallback; }
      if (b.score >= THRESHOLD) matches.push(b);
      else if (spec.diagnose && b.score >= 0.25) near.push(b);
    });
    // Visibility is the expensive part: check best-first, only while still within the tie window
    // (an inexact best match needs every reasonable candidate checked: it must be the only one).
    var vis = [], topVisible = -1, topTier = 'exact';
    for (var mi = 0; mi < matches.length; mi++) {
      var m = matches[mi];
      if (topVisible >= 0 && m.score < topVisible - TIE && topTier === 'exact' && !spec.diagnose) break;
      if (usable(m.el)) { vis.push(m); if (topVisible < 0) { topVisible = m.score; topTier = m.tier; } }
      else if (spec.diagnose) hidden.push(m);
    }
    matches = vis;

    // Click fallback: innermost visible element whose own text equals the query, if something can be clicked
    // there (a heading or paragraph that merely shows the words is not a click target).
    var bestScore = matches.reduce(function (m, x) { return Math.max(m, x.score); }, 0);
    var inert = [];
    if ((action === 'click' || action === 'hover') && bestScore < 0.85 && (!spec.source || spec.source === 'text')) {
      var fb = [];
      containers.forEach(function (c) {
        var list = c.root.querySelectorAll('*');
        for (var i = 0; i < list.length; i++) {
          var e = list[i];
          if (!isHtml(e) || /^(script|style|template|option|select|textarea)$/.test(e.localName) || !inContainer(e, c)) continue;
          var tc = e.textContent;
          if (tc.length > 300 || canon(tc) !== q) continue;
          var inner = false;
          for (var ch = e.firstElementChild; ch; ch = ch.nextElementSibling) if (canon(ch.textContent) === q) { inner = true; break; }
          if (inner || !visible(e) || !kindOk(e, spec.kind)) continue;
          if (matches.some(function (m) { return m.el.contains(e) || e.contains(m.el); })) continue;
          if (!interactive(e)) { inert.push(e); continue; }
          fb.push({ el: e, score: 0.9, source: 'text', name: text(e), tier: spec.kindFallback ? 'fuzzy' : 'exact',
                    kindFallback: spec.kindFallback });
        }
      });
      matches = matches.concat(fb);
    }

    var ties = pickTies(matches);
    if (!ties.length) {
      var diag = near.concat(hidden.map(function (h) { h.hidden = true; return h; }));
      diag.sort(function (a, b) { return b.score - a.score; });
      var nearList = inert.slice(0, 3).map(function (e) {
        return summary(e) + ' (has this text but is not clickable: no role, click handler, tabindex or pointer cursor)';
      }).concat(diag.slice(0, 6).map(function (m) {
        return summary(m.el, m.name) + ' (score ' + m.score.toFixed(2) + (m.hidden ? ', hidden' : '') + ')';
      }));
      var none = { status: 'none', near: nearList.slice(0, 6) };
      if (inert.length) none.reason = 'only non-clickable text matches; name the button/link, or use `within:`';
      return none;
    }
    var inexact = !ties.some(function (m) { return m.tier === 'exact'; });
    var reason = '';
    if (inexact) {
      // A fuzzy or synonym match is accepted only if it is the single reasonable candidate in scope.
      ties = dedupe(matches.slice());
      reason = 'inexact';
    }
    // An open modal's backdrop blocks everything behind it: matches inside the topmost modal win.
    if (ties.length > 1 && action !== 'locate') {
      var md = topModal();
      var inModal = md ? ties.filter(function (m) { return md.contains(m.el); }) : [];
      if (inModal.length && inModal.length < ties.length) ties = inModal;
    }
    if (action === 'locate') {
      ties = ties.map(function (m) { var p = promoteLive(m.el); return p === m.el ? m : Object.assign({}, m, { el: p }); });
    }
    if (spec.nth) {
      if (spec.nth > ties.length) {
        return { status: 'error', message: 'nth=' + spec.nth + ' but only ' + ties.length + ' equally good match(es)',
                 alternatives: ties.map(function (m) { return summary(m.el, m.name); }) };
      }
      return found(ties[spec.nth - 1], action);
    }
    if (ties.length > 1) {
      var descs = ties.slice(0, 10).map(function (m) { return summary(m.el, m.name); });
      var dup = {};
      descs.forEach(function (d) { dup[d] = (dup[d] || 0) + 1; });
      descs = descs.map(function (d, i) {
        if (dup[d] <= 1) return d;
        var loc = locationOf(ties[i].el);
        return d + ' (' + (loc && loc !== 'page' ? loc + '; ' : '') + 'nth: ' + (i + 1) + ')';
      });
      if (ties.length > 10) descs.push('... and ' + (ties.length - 10) + ' more');
      return { status: 'ambiguous', alternatives: descs, count: ties.length, reason: reason,
               score: Math.round(ties[0].score * 1000) / 1000 };
    }
    return found(ties[0], action);
  }

  /**
   * Ask mode: every reasonable candidate for spec (best first, at most `limit`), with evidence and location.
   * When nothing matches by name, unlabeled icon clickables in scope are offered instead.
   * Returns {items: [{el, score, tier, description, evidence, context, location, name}], reason}.
   */
  function rank(spec, limit) {
    limit = limit || 9;
    try {
      var action = spec.action || 'locate';
      if (action === 'uncheck') action = 'check';
      var containers = spec.within ? findContainers(spec.within) : [{ root: document.body }];
      if (!containers.length) containers = [{ root: document.body }];
      var q = canon(spec.query || '');
      var list = q ? scan(spec, action, containers, q).filter(function (b) { return b.score >= 0.25 && usable(b.el); }) : [];
      if (!list.length && spec.kind) {
        list = scan(Object.assign({}, spec, { kind: null }), action, containers, q)
          .filter(function (b) { return b.score >= 0.25 && usable(b.el); });
      }
      var reason = list.length ? 'name' : 'unlabeled';
      if (!list.length) {
        collect(POOL_SEL.click, containers, 'click', 'icon').forEach(function (e) {
          if (iconsOf(e).length && !labelOf(e) && usable(e)) list.push({ el: e, score: 0, tier: 'none', name: '', source: 'icon' });
        });
      }
      list = list.filter(function (m) { return !list.some(function (o) { return o !== m && m.el.contains(o.el); }); });
      list.sort(function (a, b) { return (b.score - a.score) || docOrder(a.el, b.el); });
      return {
        reason: reason,
        items: list.slice(0, limit).map(function (b) {
          return { el: b.el, score: Math.round(b.score * 1000) / 1000, tier: b.tier, name: b.name || describe(b.el),
                   description: summary(b.el, b.name), evidence: evidenceOf(b.el), context: containerTitle(b.el),
                   location: locationOf(b.el) };
        })
      };
    } catch (e) {
      return { reason: 'error: ' + (e && e.message || e), items: [] };
    }
  }

  // ------------------------------------------------------------------ public api
  var api = {
    version: BOOT.version,
    key: null,
    cfg: null,
    configure: configure,
    norm: norm,
    lit: lit,
    canon: canon,
    match: match,
    count: count,
    first: first,
    all: all,
    verify: function (xp, el) { return el ? uniqueIs(xp, el) : count(xp) === 1; },
    candidates: candidates,
    names: function (el, opts) { el = htmlTarget(el); return el ? names(el, opts || { anyText: true, near: true }) : []; },
    describe: describe,
    summary: function (el) { el = htmlTarget(el); return el ? summary(el) : ''; },
    containerTitle: function (el) { el = htmlTarget(el); return el ? containerTitle(el) : ''; },
    stableId: stableId,
    stableClass: stableClass,
    stableText: stableText,
    visible: visible,
    resolve: resolve,
    rank: rank,
    icons: function (el) {
      el = htmlTarget(el);
      return el ? iconsOf(el).map(function (i) { return { name: i.name, label: iconLabel(i), word: iconWord(i.name) }; }) : [];
    },
    iconMatch: iconMatch,
    iconWord: iconWord,
    isFaNoise: isFaNoise,
    evidence: function (el) { el = htmlTarget(el); return el ? evidenceOf(el) : []; },
    location: function (el) { el = htmlTarget(el); return el ? locationOf(el) : ''; },
    fingerprint: function (el) { el = htmlTarget(el); return el ? fingerprint(el) : null; },
    suggestName: function (el) { el = htmlTarget(el); return el ? suggestName(el) : ''; },
    findContainers: function (t) { return findContainers(t).map(function (c) { return c.root; }); }
  };
  configure(BOOT);
  Object.defineProperty(W, '__crawler', { value: api, configurable: true, writable: true, enumerable: false });
  return true;
})(/*__CRAWLER_BOOT__*/null);
