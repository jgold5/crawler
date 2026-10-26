/* hostile.js: helpers that make the fixture app hostile to locator generation.
 *
 * Every page load gets:
 *  - fresh random ids in React/MUI/Ember/rc styles (:r3f:, mui-1234, ember482, rc_select_17)
 *  - fresh hashed classes (css-1x9f2k, sc-bdVaJa, Button_primary__a1B2c)
 *  - a random Vue-style scoped attribute (data-v-1a2b3c)
 *  - async rendering after a fake XHR (200-800 ms) behind a spinner
 *
 * Templates use tokens:
 *   {{id:key}}  random id, the same for `key` within ONE render call (so label/for and
 *               aria-labelledby stay valid), different on every load / every render
 *   {{c:key}}   hashed class, the same for `key` within one page load
 *   {{rid}}     a fresh anonymous random id
 *   {{v}}       the per-load Vue scoped attribute
 */
(function () {
  'use strict';
  var LOWER = 'abcdefghijklmnopqrstuvwxyz';
  var B36 = LOWER + '0123456789';
  var ALNUM = LOWER + LOWER.toUpperCase() + '0123456789';
  var LETTERS = LOWER + LOWER.toUpperCase();

  function rnd(n) { return Math.floor(Math.random() * n); }
  function pick(a) { return a[rnd(a.length)]; }
  function rs(len, chars) { var s = ''; for (var i = 0; i < len; i++) s += chars[rnd(chars.length)]; return s; }

  var usedIds = new Set();
  function newId() {
    for (;;) {
      var f = rnd(4), id;
      if (f === 0) id = ':r' + rs(2 + rnd(2), B36) + ':';
      else if (f === 1) id = 'mui-' + (1000 + rnd(9000));
      else if (f === 2) id = 'ember' + (100 + rnd(99900));
      else id = 'rc_select_' + rnd(1000);
      if (!usedIds.has(id) && !document.getElementById(id)) { usedIds.add(id); return id; }
    }
  }

  function strHash(s) { var h = 7; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h; }
  var clsMap = {};
  function cls(key) {
    if (!clsMap[key]) {
      var f = strHash(key) % 3, name;
      if (f === 0) name = 'css-' + rs(1, LOWER) + rs(5, B36);
      else if (f === 1) name = 'sc-' + rs(6, LETTERS);
      else {
        var parts = key.split(/[^A-Za-z0-9]+/).filter(Boolean);
        var base = parts.map(function (p, i) { return i === 0 ? p.charAt(0).toUpperCase() + p.slice(1) : p; }).join('_');
        name = base + '__' + rs(5, ALNUM);
      }
      clsMap[key] = name;
    }
    return clsMap[key];
  }

  var vAttr = 'data-v-' + rs(6, '0123456789abcdef');

  function tpl(s) {
    var local = {};
    return s
      .replace(/\{\{id:([\w.-]+)\}\}/g, function (m, k) { return local[k] || (local[k] = newId()); })
      .replace(/\{\{c:([\w.-]+)\}\}/g, function (m, k) { return cls(k); })
      .replace(/\{\{rid\}\}/g, function () { return newId(); })
      .replace(/\{\{v\}\}/g, function () { return vAttr; });
  }

  function frag(html) {
    var t = document.createElement('template');
    t.innerHTML = tpl(html).trim();
    return t.content;
  }

  /* Add random ids / hashed classes to some anonymous wrappers, and a Vue attr to all. */
  function sprinkle(root) {
    root.querySelectorAll('div, span, section, li').forEach(function (el) {
      if (!el.id && Math.random() < 0.35) el.id = newId();
      if (Math.random() < 0.3) el.classList.add('css-' + rs(1, LOWER) + rs(5, B36));
      el.setAttribute(vAttr, '');
    });
  }

  /* Inject CSS for hashed classes: styles = {key: 'css text'} */
  function styles(map) {
    var css = Object.keys(map).map(function (k) { return '.' + cls(k) + '{' + map[k] + '}'; }).join('\n');
    var st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);
  }

  var SPIN_SVG = '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="3" stroke-dasharray="40 20"/></svg>';

  /* Show a spinner in `container`, wait a fake XHR (200-800 ms), then run render(). */
  function load(container, render, minMs, maxMs) {
    minMs = minMs == null ? 200 : minMs; maxMs = maxMs == null ? 800 : maxMs;
    container.setAttribute('aria-busy', 'true');
    var sp = frag('<div class="app-spinner {{c:Spinner.root}}" role="progressbar" aria-label="Loading" id="{{rid}}">' + SPIN_SVG + '</div>').firstElementChild;
    container.appendChild(sp);
    return new Promise(function (resolve) {
      setTimeout(function () {
        sp.remove();
        render(container);
        sprinkle(container);
        container.removeAttribute('aria-busy');
        resolve(container);
      }, minMs + rnd(maxMs - minMs + 1));
    });
  }

  /* Transient toast (role=status) that disappears after 1.2-1.8 s. */
  function toast(text) {
    var region = document.querySelector('.toast-region');
    if (!region) {
      region = frag('<div class="toast-region {{c:Toast.region}}" aria-live="polite" id="{{rid}}"></div>').firstElementChild;
      document.body.appendChild(region);
    }
    var t = frag('<div class="toast {{c:Toast.root}}" role="status" id="{{rid}}">' +
      '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3 8l3 3 7-7" stroke="currentColor" fill="none" stroke-width="2"/></svg>' +
      '<span class="{{c:Toast.text}}"></span></div>').firstElementChild;
    t.querySelector('span').textContent = text;
    region.appendChild(t);
    setTimeout(function () { t.remove(); }, 1200 + rnd(600));
    return t;
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  var ICONS = {
    arrow: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M4 8h8M9 5l3 3-3 3" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>',
    lock: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><rect x="3" y="7" width="10" height="7" rx="1" fill="currentColor"/><path d="M5 7V5a3 3 0 0 1 6 0v2" stroke="currentColor" fill="none"/></svg>',
    eye: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><ellipse cx="8" cy="8" rx="6" ry="4" stroke="currentColor" fill="none"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/></svg>',
    check: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M3 8l3 3 7-7" stroke="currentColor" fill="none" stroke-width="2"/></svg>',
    chevron: '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false"><path d="M4 6l4 4 4-4" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>',
    save: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M3 2h8l2 2v10H3z" fill="none" stroke="currentColor"/></svg>',
    trash: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M3 4h10M6 4V2h4v2M4 4l1 10h6l1-10" stroke="currentColor" fill="none"/></svg>',
    search: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><circle cx="7" cy="7" r="4.5" stroke="currentColor" fill="none"/><path d="M10.5 10.5L14 14" stroke="currentColor"/></svg>',
    mail: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><rect x="2" y="4" width="12" height="8" fill="none" stroke="currentColor"/><path d="M2 4l6 5 6-5" stroke="currentColor" fill="none"/></svg>'
  };

  window.Hostile = {
    rnd: rnd, pick: pick, rs: rs, newId: newId, cls: cls, tpl: tpl, frag: frag,
    sprinkle: sprinkle, styles: styles, load: load, toast: toast, esc: esc, icons: ICONS, vAttr: vAttr
  };
})();
