// Fixture data for orders.html. Deterministic per key (so a key always has the same
// content), but the ROW ORDER is shuffled on every page load like a real backend
// that returns rows in arbitrary order.
(function () {
  "use strict";

  function seeded(n) {
    // tiny deterministic PRNG per row so content is stable across loads
    var x = (n * 2654435761) >>> 0;
    return function () {
      x ^= x << 13; x >>>= 0;
      x ^= x >>> 17;
      x ^= x << 5; x >>>= 0;
      return x / 4294967296;
    };
  }
  function pick(r, arr) { return arr[Math.floor(r() * arr.length)]; }
  function pad(n, w) { n = String(n); while (n.length < w) n = "0" + n; return n; }
  function date(r) {
    return "2024-" + pad(1 + Math.floor(r() * 12), 2) + "-" + pad(1 + Math.floor(r() * 28), 2);
  }
  // ?seed=N makes the shuffles reproducible for tests (one PRNG per grid, so the order of the
  // async data callbacks does not matter); default is a new order on every load.
  var seedParam = new URLSearchParams(location.search).get("seed");
  function rng(salt) {
    if (seedParam == null) return Math.random;
    var a = (parseInt(seedParam, 10) || 0) + salt;
    return function () {  // mulberry32
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function shuffle(a, rand) {
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(rand() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  var STATUSES = ["Open", "Pending", "Shipped", "Delivered", "Cancelled"];
  var REGIONS = ["North", "South", "East", "West", "Central"];
  var PRODUCTS = ["Widget", "Gadget", "Sprocket", "Gizmo", "Doohickey", "Flange", "Bracket"];
  var CARRIERS = ["UPS", "FedEx", "DHL", "USPS", "Local courier"];
  var CHANNELS = ["Web", "Phone", "Partner", "Retail"];
  var REPS = ["Ann Lee", "Bo Chen", "Cy Diaz", "Di Evans", "Ed Fox"];
  var CUSTOMERS = ["Acme Corp", "Globex", "Initech", "Umbrella", "Hooli", "Stark Ind", "Wayne Ent", "Wonka"];

  function makeOrders(count) {
    var rows = [];
    for (var i = 0; i < count; i++) {
      var n = 100 + i, r = seeded(n);
      var qty = 1 + Math.floor(r() * 99), price = Math.round((5 + r() * 495) * 100) / 100;
      rows.push({
        orderId: "A-" + n,
        customer: pick(r, CUSTOMERS),
        quantity: qty,
        status: pick(r, STATUSES),
        priority: r() < 0.3,
        notes: r() < 0.5 ? "Leave at dock " + pick(r, ["B", "C", "D"]) : "",
        product: pick(r, PRODUCTS),
        sku: "SKU-" + pad(Math.floor(r() * 99999), 5),
        category: pick(r, ["Hardware", "Parts", "Tools", "Misc"]),
        unitPrice: price,
        currency: pick(r, ["USD", "EUR", "GBP"]),
        orderDate: date(r),
        shipDate: date(r),
        carrier: pick(r, CARRIERS),
        tracking: "1Z" + pad(Math.floor(r() * 99999999), 8),
        warehouse: pick(r, ["WH-East", "WH-West", "WH-Central"]),
        salesRep: pick(r, REPS),
        channel: pick(r, CHANNELS),
        discount: Math.floor(r() * 20) + "%",
        tax: Math.round(r() * 1000) / 100,
        payment: pick(r, ["Card", "Invoice", "Wire"]),
        terms: pick(r, ["Net 30", "Net 60", "Prepaid"]),
        createdBy: pick(r, REPS),
        updatedAt: date(r),
        region: pick(r, REGIONS)
      });
    }
    return shuffle(rows, rng(0));
  }

  function makeCustomers(count) {
    var rows = [];
    for (var i = 0; i < count; i++) {
      var r = seeded(5000 + i);
      rows.push({
        customerId: "C-" + pad(i + 1, 3),
        name: CUSTOMERS[i % CUSTOMERS.length] + " " + (Math.floor(i / CUSTOMERS.length) + 1),
        // Deliberate collisions with the Orders grid: same col-ids, overlapping values.
        orderId: "A-" + (100 + Math.floor(r() * 1000)),
        quantity: Math.floor(r() * 50),
        status: pick(r, STATUSES),
        country: pick(r, ["US", "DE", "FR", "UK", "JP"]),
        tier: pick(r, ["Gold", "Silver", "Bronze"])
      });
    }
    // make sure a customer's "Last Order" is A-950 (same text as an Orders key)
    rows[3].orderId = "A-950";
    return shuffle(rows, rng(7919));
  }

  window.fixtureData = { makeOrders: makeOrders, makeCustomers: makeCustomers };
})();
