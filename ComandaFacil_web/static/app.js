// ComandaFácil — interface (sem dependências)
(function () {
  const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const brlC = c => "R$ " + (c / 100).toFixed(2).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, ".");

  // ---------- tema claro/escuro ----------
  const root = document.documentElement;
  function applyTheme(t) {
    root.dataset.theme = t;
    try { localStorage.setItem("cf_theme", t); } catch (e) {}
    const m = $('meta[name="theme-color"]'); if (m) m.content = t === "dark" ? "#0e0d0c" : "#f6f5f2";
  }
  $$("[data-theme-toggle]").forEach(b => b.addEventListener("click", () => applyTheme(root.dataset.theme === "dark" ? "light" : "dark")));

  // ---------- avisos (toasts) ----------
  const box = $("#toasts");
  function dismiss(el) { el.style.transition = "opacity .25s"; el.style.opacity = "0"; setTimeout(() => el.remove(), 260); }
  function wire(el, ms) { el.addEventListener("click", e => { if (!e.target.closest("button")) dismiss(el); }); if (ms) setTimeout(() => dismiss(el), ms); }
  if (box) $$(".flash", box).forEach(el => wire(el, el.classList.contains("erro") ? 9000 : 5000));
  function toast(msg, o) {
    o = o || {};
    if (!box) { alert(msg); return; }
    const el = document.createElement("div"); el.className = "toast " + (o.kind || "info"); el.setAttribute("role", "status");
    const t = document.createElement("span"); t.textContent = msg; el.appendChild(t);
    if (o.actionLabel) { const b = document.createElement("button"); b.type = "button"; b.className = "btn sm"; b.textContent = o.actionLabel; b.addEventListener("click", () => { dismiss(el); o.onAction && o.onAction(); }); el.appendChild(b); }
    box.appendChild(el); wire(el, o.sticky ? 0 : 6000);
  }

  // ---------- menu lateral (celular) ----------
  $$("[data-menu]").forEach(b => b.addEventListener("click", () => document.body.classList.toggle("nav-open")));

  // ---------- bloqueio de duplo clique ----------
  document.addEventListener("submit", function (e) {
    const f = e.target;
    if (f.dataset.sent === "1") { e.preventDefault(); return; }
    f.dataset.sent = "1";
    setTimeout(() => $$("button[type=submit],button:not([type])", f).forEach(b => { b.disabled = true; }), 0);
    setTimeout(() => { f.dataset.sent = ""; $$("button", f).forEach(b => { b.disabled = false; }); }, 4000);
  });

  // ---------- diálogos ----------
  document.addEventListener("click", function (e) {
    const o = e.target.closest("[data-open]");
    if (o) { const d = document.getElementById(o.dataset.open); if (d) { d.showModal(); const i = $("[autofocus]", d); i && i.focus(); } }
    const c = e.target.closest("[data-close]");
    if (c) c.closest("dialog").close();
  });
  $$("dialog").forEach(d => d.addEventListener("click", e => { if (e.target === d) d.close(); }));

  // ---------- tempo de mesa ocupada ----------
  const loaded = Date.now();
  function tick() {
    $$("[data-min]").forEach(el => {
      const m = parseInt(el.dataset.min, 10) + Math.floor((Date.now() - loaded) / 60000);
      el.textContent = m < 60 ? "há " + m + " min" : "há " + Math.floor(m / 60) + "h" + String(m % 60).padStart(2, "0");
    });
  }
  tick(); setInterval(tick, 30000);

  // ---------- cardápio: busca e categorias ----------
  const search = $("#prod-search"), rows = $$(".prod[data-name]"); let cat = "";
  function filter() { const q = (search ? search.value : "").toLowerCase().trim(); rows.forEach(r => { r.hidden = !(r.dataset.name.includes(q) && (!cat || r.dataset.cat === cat)); }); }
  if (search) search.addEventListener("input", filter);
  $$(".chip[data-cat]").forEach(ch => ch.addEventListener("click", function () {
    cat = ch.dataset.cat === cat ? "" : ch.dataset.cat;
    $$(".chip[data-cat]").forEach(x => x.classList.toggle("on", x.dataset.cat === cat)); filter();
  }));

  // ---------- fechamento: pagamentos, troco, divisão ----------
  const pay = $("#pay-box");
  if (pay) {
    const total = parseInt(pay.dataset.total, 10);
    const parse = s => { s = (s || "").replace(/[R$\s]/g, ""); if (!s) return 0; if (s.includes(",")) s = s.replace(/\./g, "").replace(",", "."); const n = parseFloat(s); return isFinite(n) ? Math.round(n * 100) : 0; };
    const rowsBox = $("#pay-rows"), tpl = $("#pay-tpl"), out = $("#pay-status");
    const recalc = () => { let paid = 0; $$("input[name=pay_amount]", rowsBox).forEach(i => { paid += parse(i.value); }); const diff = paid - total;
      out.className = "badge " + (diff < 0 ? "err" : "ok"); out.textContent = diff < 0 ? "Faltam " + brlC(-diff) : diff > 0 ? "Troco: " + brlC(diff) + " (em dinheiro)" : "Valor exato"; };
    const addRow = () => { rowsBox.appendChild(tpl.content.cloneNode(true)); recalc(); };
    $("#pay-add").addEventListener("click", addRow);
    $("#pay-fill").addEventListener("click", function () {
      let paid = 0; const inputs = $$("input[name=pay_amount]", rowsBox);
      inputs.forEach((i, k) => { if (k < inputs.length - 1) paid += parse(i.value); });
      inputs[inputs.length - 1].value = (Math.max(0, total - paid) / 100).toFixed(2).replace(".", ","); recalc();
    });
    rowsBox.addEventListener("input", recalc); addRow(); $("#pay-fill").click();
  }
  const splitN = $("#split-n");
  if (splitN) {
    const total = parseInt(splitN.dataset.total, 10), res = $("#split-res");
    const run = () => { const n = Math.max(1, Math.min(50, parseInt(splitN.value, 10) || 1)), base = Math.floor(total / n), rest = total - base * n;
      res.innerHTML = Array.from({ length: n }, (_, i) => "<div class='row sb'><span>Pessoa " + (i + 1) + "</span><b class='num'>" + brlC(base + (i < rest ? 1 : 0)) + "</b></div>").join(""); };
    splitN.addEventListener("input", run); run();
  }

  // ---------- WhatsApp acoplado ----------
  const SPLIT = "cf_split", WA = "comandafacil_whatsapp", APP = "comandafacil_app";
  const split = () => { let v = NaN; try { v = parseInt(localStorage.getItem(SPLIT), 10); } catch (e) {} return v >= 40 && v <= 75 ? v : 60; };
  function geom(side) {
    const L = screen.availLeft || 0, T = screen.availTop || 0, W = screen.availWidth, H = screen.availHeight, lw = Math.round(W * split() / 100);
    return side === "app" ? { left: L, top: T, width: lw, height: H } : { left: L + lw, top: T, width: W - lw, height: H };
  }
  // 'popup' mede a área interna; desconta a moldura da janela para não passar da tela
  const feat = g => "popup=yes,left=" + g.left + ",top=" + g.top + ",width=" + Math.max(360, g.width - 16) + ",height=" + Math.max(400, g.height - 72);
  function openWA(url) {
    if (window.__cfWA && !window.__cfWA.closed && !url) { window.__cfWA.focus(); return window.__cfWA; }
    const w = window.open(url || "https://web.whatsapp.com/", WA, feat(geom("wa")));
    if (!w) { toast("O navegador bloqueou a janela do WhatsApp. Permita pop-ups para este endereço e tente de novo.", { kind: "erro" }); return null; }
    window.__cfWA = w; try { w.focus(); } catch (e) {} return w;
  }
  function fitSelf() { if (window.name === APP) { try { const g = geom("app"); window.moveTo(g.left, g.top); window.resizeTo(g.width, g.height); } catch (e) {} } }
  function openApp() {
    const w = window.open(location.href, APP, feat(geom("app")));
    if (!w) { toast("O navegador bloqueou a nova janela. Permita pop-ups e tente de novo.", { kind: "erro" }); return; }
    toast("Sistema aberto em janela própria, no lado esquerdo. Você pode fechar esta aba.", { kind: "sucesso" });
  }
  function dock() {
    fitSelf();
    const w = openWA();
    if (w && window.name !== APP) toast("WhatsApp aberto ao lado direito.", { kind: "sucesso", sticky: true, actionLabel: "Ajustar o sistema ao lado", onAction: openApp });
  }
  const dockBtn = $("#wa-dock"); if (dockBtn) dockBtn.addEventListener("click", dock);
  const range = $("#split-range");
  if (range) {
    range.value = split(); $("#split-val").textContent = range.value;
    range.addEventListener("input", () => { $("#split-val").textContent = range.value; try { localStorage.setItem(SPLIT, range.value); } catch (e) {} });
    $("#dock-now").addEventListener("click", () => { if (window.__cfWA && !window.__cfWA.closed) { try { window.__cfWA.close(); } catch (e) {} window.__cfWA = null; } $("#dlg-dock").close(); dock(); });
    $("#open-app").addEventListener("click", () => { $("#dlg-dock").close(); openApp(); });
  }
  window.cf = { toast, openWhatsApp: u => openWA(u) };

  // ---------- comanda: mensagem do WhatsApp ----------
  const waText = $("#wa-text");
  if (waText) {
    const phone = $("#wa-phone");
    $("#wa-copy").addEventListener("click", async function () {
      try { await navigator.clipboard.writeText(waText.value); } catch (e) { waText.select(); document.execCommand("copy"); }
      this.textContent = "Copiado!"; setTimeout(() => { this.textContent = "Copiar mensagem"; }, 1800);
    });
    $("#wa-send").addEventListener("click", function () {
      const p = (phone.value || "").replace(/\D/g, "");
      if (p.length < 10) { toast("Informe o telefone do cliente com DDD.", { kind: "erro" }); return; }
      const num = p.length <= 11 ? "55" + p : p;
      $("#dlg-wa").close();
      window.cf.openWhatsApp("https://web.whatsapp.com/send?phone=" + num + "&text=" + encodeURIComponent(waText.value));
    });
  }
})();
