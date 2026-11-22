/* Landing de NexusFlow. JS puro, sin dependencias.
 * Se carga en <head> sin defer para marcar .js antes del primer pintado (sin JS, todo
 * el contenido queda visible en su estado final) y espera al DOM para lo demás. */
(function () {
  "use strict";

  document.documentElement.classList.replace("no-js", "js");

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  function whenVisible(el, fn, threshold) {
    if (!("IntersectionObserver" in window)) return fn();
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          io.disconnect();
          fn();
        }
      });
    }, { threshold: threshold || 0.25 });
    io.observe(el);
  }

  /* ---------- B1: split text ---------- */
  function splitTitle() {
    var h = document.querySelector("[data-split]");
    if (!h) return;
    var text = h.textContent.trim();
    h.setAttribute("aria-label", text);
    h.textContent = "";
    text.split(/\s+/).forEach(function (word, i) {
      var s = document.createElement("span");
      s.className = "w";
      s.setAttribute("aria-hidden", "true");
      s.style.setProperty("--i", i);
      s.textContent = word;
      h.appendChild(s);
      h.appendChild(document.createTextNode(" "));
    });
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { h.classList.add("is-in"); });
    });
  }

  /* ---------- A1 / A2: reveal y stagger ---------- */
  function reveals() {
    document.querySelectorAll("[data-stagger]").forEach(function (list) {
      Array.prototype.forEach.call(list.children, function (c, i) { c.style.setProperty("--i", i); });
    });
    var els = document.querySelectorAll(".reveal, [data-stagger]");
    if (!("IntersectionObserver" in window)) {
      els.forEach(function (el) { el.classList.add("is-in"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add("is-in");
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.15, rootMargin: "0px 0px -8% 0px" });
    els.forEach(function (el) { io.observe(el); });
  }

  /* ---------- A7: count-up ---------- */
  function countUps() {
    var fmt = new Intl.NumberFormat("es-ES");
    document.querySelectorAll("[data-count]").forEach(function (el) {
      var target = Number(el.dataset.count);
      var pre = el.dataset.prefix || "";
      var suf = el.dataset.suffix || "";
      if (reduced.matches) return; // el HTML ya trae el valor final
      el.textContent = pre + "0" + suf;
      whenVisible(el, function () {
        var t0 = performance.now();
        var dur = 1400;
        (function tick(now) {
          var p = Math.min(1, (now - t0) / dur);
          var v = Math.round(target * (1 - Math.pow(1 - p, 3)));
          el.textContent = pre + fmt.format(v) + suf;
          if (p < 1) requestAnimationFrame(tick);
        })(t0);
      }, 0.6);
    });
  }

  /* ---------- H3: terminal ---------- */
  function terminal() {
    var term = document.querySelector("[data-term]");
    if (!term) return;
    var lines = term.querySelectorAll(".t-line");
    var cmd = term.querySelector("[data-type]");
    var cmdText = cmd.textContent;
    var replay = term.querySelector("[data-term-replay]");
    var run = 0;

    function showAll() {
      cmd.textContent = cmdText;
      lines.forEach(function (l) { l.classList.add("is-shown"); });
    }

    function wait(ms, id) {
      return new Promise(function (res, rej) {
        setTimeout(function () { (id === run ? res : rej)(); }, ms);
      });
    }

    function play() {
      var id = ++run;
      replay.hidden = true;
      lines.forEach(function (l) { l.classList.remove("is-shown", "t-caret"); });
      cmd.textContent = "";
      cmd.classList.add("is-shown", "t-caret");
      var chain = wait(400, id);
      cmdText.split("").forEach(function (ch) {
        chain = chain.then(function () {
          cmd.textContent += ch;
          return wait(38 + Math.random() * 40, id);
        });
      });
      chain = chain.then(function () { cmd.classList.remove("t-caret"); return wait(350, id); });
      Array.prototype.slice.call(lines, 1).forEach(function (l) {
        chain = chain.then(function () {
          l.classList.add("is-shown");
          return wait(l.classList.contains("t-dim") ? 450 : 750, id);
        });
      });
      chain.then(function () { replay.hidden = false; }, function () { /* sustituida por otra */ });
    }

    if (reduced.matches) return showAll();
    replay.addEventListener("click", play);
    whenVisible(term, play, 0.5);
  }

  /* ---------- C4 + H5: el cobro que falla ---------- */
  var P = {
    inTop: [140, 40], lookup: [140, 180], lookupR: [250, 180], crm: [360, 180],
    vip: [140, 304], charge: [140, 428], chargeR: [250, 428], gw: [360, 428]
  };

  // Estado final de cada paso. Volver atrás o saltar pasos aplica el estado sin animar.
  var STATES = [
    { nodes: {}, edges: [], rows: 0, ledger: 0, key: false },
    { nodes: { lookup: "ok", crm: "ok", vip: "ok", charge: "run" },
      edges: ["in-lookup", "lookup-crm", "lookup-vip", "vip-charge"], rows: 0, ledger: 0, key: false },
    { nodes: { lookup: "ok", crm: "ok", vip: "ok", charge: "wait", gw: "fail" }, attempt: "intento 1/4", gw: "503",
      edges: ["in-lookup", "lookup-crm", "lookup-vip", "vip-charge", "charge-gw"], rows: 1, ledger: 0, key: true },
    { nodes: { lookup: "ok", crm: "ok", vip: "ok", charge: "wait", gw: "fail" }, attempt: "intento 2/4", gw: "503",
      edges: ["in-lookup", "lookup-crm", "lookup-vip", "vip-charge", "charge-gw"], rows: 2, ledger: 0, key: true },
    { nodes: { lookup: "ok", crm: "ok", vip: "ok", charge: "ok", gw: "ok" }, attempt: "intento 3/4", gw: "201",
      edges: ["in-lookup", "lookup-crm", "lookup-vip", "vip-charge", "charge-gw"], rows: 3, ledger: 1, key: true },
    { nodes: { input: "ok", lookup: "ok", crm: "ok", vip: "ok", charge: "ok", gw: "ok" }, attempt: "intento 3/4", gw: "201",
      edges: ["in-lookup", "lookup-crm", "lookup-vip", "vip-charge", "charge-gw"], rows: 4, ledger: 1, key: true }
  ];

  function story() {
    var flow = document.querySelector(".flow");
    var steps = document.querySelectorAll(".step");
    if (!flow || !steps.length) return;
    var svg = flow.querySelector("svg");
    var packet = svg.querySelector(".packet");
    var nodes = {};
    svg.querySelectorAll("[data-node]").forEach(function (n) { nodes[n.dataset.node] = n; });
    var attemptEl = svg.querySelector("[data-attempt]");
    var gwCode = svg.querySelector("[data-gw-code]");
    var ledger = svg.querySelector("[data-ledger]");
    var keyChip = svg.querySelector(".flow__key");
    var rows = flow.querySelectorAll("[data-row]");
    var current = -1;
    var token = 0;

    function apply(n) {
      var s = STATES[n];
      Object.keys(nodes).forEach(function (k) {
        nodes[k].classList.remove("is-ok", "is-fail", "is-wait", "is-run");
        if (s.nodes[k]) nodes[k].classList.add("is-" + s.nodes[k]);
      });
      svg.querySelectorAll("[data-edge]").forEach(function (e) {
        e.classList.toggle("is-lit", s.edges.indexOf(e.dataset.edge) !== -1);
      });
      if (s.attempt) attemptEl.textContent = s.attempt;
      if (s.gw) gwCode.textContent = s.gw;
      ledger.textContent = String(s.ledger);
      ledger.parentNode.classList.toggle("is-one", s.ledger === 1);
      flow.classList.toggle("has-key", s.key);
      rows.forEach(function (r, i) { r.classList.toggle("is-hidden", i >= s.rows); });
      flow.dataset.step = String(n);
    }

    function pulse(el, cls) {
      el.classList.remove(cls);
      void el.getBoundingClientRect(); // reinicia la animación CSS
      el.classList.add(cls);
    }

    // Mueve el paquete por una lista de puntos a velocidad constante.
    function travel(points, kind, id) {
      return new Promise(function (res, rej) {
        if (id !== token) return rej();
        packet.setAttribute("class", "packet" + (kind ? " is-" + kind : ""));
        packet.style.opacity = "1";
        var segs = [];
        var total = 0;
        for (var i = 1; i < points.length; i++) {
          var a = points[i - 1], b = points[i];
          var len = Math.hypot(b[0] - a[0], b[1] - a[1]);
          segs.push({ a: a, b: b, len: len });
          total += len;
        }
        var dur = Math.max(320, total * 2.6);
        var t0 = performance.now();
        (function frame(now) {
          if (id !== token) return rej();
          var p = Math.min(1, (now - t0) / dur);
          var e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
          var d = e * total;
          var s = segs[0];
          for (var j = 0; j < segs.length; j++) {
            s = segs[j];
            if (d <= s.len || j === segs.length - 1) break;
            d -= s.len;
          }
          var f = s.len ? Math.min(1, d / s.len) : 1;
          packet.setAttribute("transform", "translate(" + (s.a[0] + (s.b[0] - s.a[0]) * f) + " " + (s.a[1] + (s.b[1] - s.a[1]) * f) + ")");
          if (p < 1) requestAnimationFrame(frame);
          else res();
        })(t0);
      });
    }

    function hold(ms, id) {
      return new Promise(function (res, rej) {
        setTimeout(function () { (id === token ? res : rej)(); }, ms);
      });
    }

    function hide() { packet.style.opacity = "0"; }

    // Coreografía de cada paso, partiendo del estado del paso anterior.
    function attempt(id, ok, n) {
      var gw = nodes.gw;
      gw.classList.remove("is-ok", "is-fail");
      nodes.charge.classList.remove("is-wait", "is-ok");
      nodes.charge.classList.add("is-run");
      attemptEl.textContent = "intento " + n + "/4";
      pulse(keyChip, "is-pulse");
      return travel([P.charge, P.chargeR, P.gw], null, id)
        .then(function () {
          gwCode.textContent = ok ? "201" : "503";
          gw.classList.add(ok ? "is-ok" : "is-fail");
          if (!ok) pulse(gw, "is-flash");
          return hold(260, id);
        })
        .then(function () { return travel([P.gw, P.chargeR, P.charge], ok ? "ok" : "fail", id); });
    }

    var PLAYS = {
      1: function (id) {
        return travel([P.inTop, P.lookup, P.lookupR, P.crm], null, id)
          .then(function () { nodes.crm.classList.add("is-ok"); return hold(180, id); })
          .then(function () { return travel([P.crm, P.lookupR, P.lookup], "ok", id); })
          .then(function () { nodes.lookup.classList.add("is-ok"); return travel([P.lookup, P.vip], "ok", id); })
          .then(function () { nodes.vip.classList.add("is-ok"); return travel([P.vip, P.charge], null, id); });
      },
      2: function (id) { return attempt(id, false, 1); },
      3: function (id) { return attempt(id, false, 2); },
      4: function (id) { return attempt(id, true, 3); },
      5: function (id) {
        // La nueva petición llega a la entrada y vuelve: no baja por el flujo.
        return travel([[140, -10], P.inTop], null, id)
          .then(function () { nodes.input.classList.add("is-ok"); return hold(300, id); })
          .then(function () { return travel([P.inTop, [140, -10]], "ok", id); });
      }
    };

    function activate(n) {
      if (n === current) return;
      steps.forEach(function (s, i) { s.classList.toggle("is-active", i === n); });
      var prev = current;
      current = n;
      var id = ++token;
      hide();
      if (reduced.matches || n !== prev + 1 || !PLAYS[n]) return apply(n);
      apply(prev);
      PLAYS[n](id).then(
        function () { if (id === token) { hide(); apply(n); } },
        function () { /* otro paso tomó el relevo */ }
      );
    }

    // En móvil el diagrama fijo tapa la mitad superior: el paso se activa justo debajo de él.
    // shortcut: se calcula una vez al cargar; si se gira el móvil, recargar.
    function activationLine() {
      if (!window.matchMedia("(max-width: 900px)").matches) return "-48% 0px -48% 0px";
      var visual = document.querySelector(".story__visual").getBoundingClientRect().height + 56;
      var line = Math.round(visual + (window.innerHeight - visual) * 0.3);
      return "-" + line + "px 0px -" + (window.innerHeight - line - 1) + "px 0px";
    }

    apply(0);
    if (!("IntersectionObserver" in window)) { apply(5); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) activate(Number(e.target.dataset.step));
      });
    }, { rootMargin: activationLine() });
    steps.forEach(function (s) { io.observe(s); });
  }

  onReady(function () {
    splitTitle();
    reveals();
    countUps();
    terminal();
    story();
  });
})();
