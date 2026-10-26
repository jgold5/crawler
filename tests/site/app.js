/* app.js: shared shell for authenticated pages (dashboard, orders, settings).
 *
 * CONTRACT (orders.html depends on it, keep it stable):
 *  - As soon as the script runs: if localStorage.getItem('auth') !== '1' -> location.replace('login.html').
 *  - Renders a top nav at the START of <body>: links "Dashboard" (dashboard.html),
 *    "Orders" (orders.html), "Settings" (settings.html) with stable class `nav-link`,
 *    and a "Sign out" button (svg + span) that clears auth and goes to login.html.
 *  - Self-contained (does not need hostile.js). Safe in <head> or at the end of <body>,
 *    and on pages that already have their own content. Never touches other nodes.
 *  - Nav ids/classes are hostile too (random per load), except the stable classes
 *    `app-nav`, `nav-link`, `nav-signout`.
 */
(function () {
  'use strict';
  var authed = false;
  try { authed = localStorage.getItem('auth') === '1'; } catch (e) { authed = false; }
  if (!authed) { location.replace('login.html'); return; }

  function rnd(n) { return Math.floor(Math.random() * n); }
  function rs(len, chars) { var s = ''; for (var i = 0; i < len; i++) s += chars[rnd(chars.length)]; return s; }
  var L = 'abcdefghijklmnopqrstuvwxyz', B36 = L + '0123456789';
  function rid() {
    for (;;) {
      var f = rnd(3), id;
      id = f === 0 ? ':r' + rs(3, B36) + ':' : f === 1 ? 'mui-' + (1000 + rnd(9000)) : 'ember' + (100 + rnd(99900));
      if (!document.getElementById(id)) return id;
    }
  }
  function hcls() { return rnd(2) ? 'css-' + rs(1, L) + rs(5, B36) : 'sc-' + rs(6, L + L.toUpperCase()); }

  var here = (location.pathname.split('/').pop() || '').toLowerCase();
  var links = [['Dashboard', 'dashboard.html'], ['Orders', 'orders.html'], ['Settings', 'settings.html']];

  function render() {
    if (document.querySelector('nav.app-nav')) return;
    var nav = document.createElement('nav');
    nav.className = 'app-nav ' + hcls();
    nav.id = rid();
    nav.setAttribute('aria-label', 'Main');
    var brand = document.createElement('div');
    brand.className = 'app-brand ' + hcls();
    brand.innerHTML = '<svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><rect x="2" y="2" width="12" height="12" rx="3" fill="currentColor"/></svg><span>Acmeish</span>';
    nav.appendChild(brand);
    var ul = document.createElement('ul');
    ul.className = hcls();
    links.forEach(function (l) {
      var li = document.createElement('li');
      li.className = hcls();
      if (rnd(2)) li.id = rid();
      var a = document.createElement('a');
      a.href = l[1];
      a.id = rid();
      a.className = 'nav-link ' + hcls();
      a.innerHTML = '<span class="' + hcls() + '"></span>';
      a.firstChild.textContent = l[0];
      if (here === l[1]) { a.setAttribute('aria-current', 'page'); a.classList.add('active'); }
      li.appendChild(a);
      ul.appendChild(li);
    });
    nav.appendChild(ul);
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = rid();
    btn.className = 'nav-signout ' + hcls();
    btn.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M6 3H3v10h3M10 5l3 3-3 3M13 8H6" stroke="currentColor" fill="none"/></svg><span class="' + hcls() + '">Sign out</span>';
    btn.addEventListener('click', function () {
      try { localStorage.removeItem('auth'); } catch (e) { /* ignore */ }
      location.href = 'login.html';
    });
    nav.appendChild(btn);
    document.body.insertBefore(nav, document.body.firstChild);
  }

  if (document.body) render();
  else document.addEventListener('DOMContentLoaded', render);
})();
