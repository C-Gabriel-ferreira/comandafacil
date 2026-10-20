(function () {
  "use strict";
  var CFG = window.CARDAPIO || {};
  var params = new URLSearchParams(location.search);
  var slug = (params.get("loja") || "").toLowerCase().replace(/[^a-z0-9-]/g, "");
  var $ = function (id) { return document.getElementById(id); };
  var state = { store: null, items: [], cart: {}, timer: null };
  var KEY = "cardapio:" + slug;

  function brl(c) { return (c / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" }); }
  function el(tag, props, kids) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) { if (k === "text") n.textContent = props[k]; else n.setAttribute(k, props[k]); });
    (kids || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }
  function rpc(fn, body) {
    return fetch(CFG.url + "/rest/v1/rpc/" + fn, {
      method: "POST",
      headers: { apikey: CFG.key, Authorization: "Bearer " + CFG.key, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); });
  }
  function remember(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* sem armazenamento */ } }
  function recall(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } }

  var ERR = {
    loja_fechada: "A loja não está recebendo pedidos agora.", entrega_indisponivel: "Esta loja não faz entrega.",
    retirada_indisponivel: "Esta loja não tem retirada.", nome_invalido: "Digite seu nome.",
    telefone_invalido: "Digite um telefone válido com DDD.", endereco_invalido: "Digite o endereço completo.",
    itens_invalidos: "Confira os itens do carrinho.", item_indisponivel: "Um item acabou de ficar indisponível. Atualize o cardápio.",
    muitos_pedidos: "Muitos pedidos seguidos. Tente de novo em alguns minutos.", pedido_minimo: "Pedido abaixo do mínimo da loja."
  };

  // ------------------------------------------------------------------ cardápio
  function load() {
    if (!slug) { $("menu").textContent = "Link inválido: falta o nome da loja."; return; }
    rpc("cf_get_menu", { p_slug: slug }).then(function (d) {
      if (!d.ok) { $("menu").textContent = "Loja não encontrada."; return; }
      state.store = d.store; state.items = d.items;
      document.title = d.store.name + " — Cardápio";
      $("store-name").textContent = d.store.name;
      var info = [];
      if (d.store.hours_text) info.push(d.store.hours_text);
      if (d.store.delivery_enabled && d.store.delivery_fee_cents) info.push("Entrega " + brl(d.store.delivery_fee_cents));
      if (d.store.min_order_cents) info.push("Mínimo " + brl(d.store.min_order_cents));
      $("store-info").textContent = info.join(" · ");
      var n = $("notice");
      if (!d.store.accepting) { n.hidden = false; n.className = "banner closed"; n.textContent = "Loja fechada no momento — o cardápio está só para consulta."; }
      else if (d.store.notice) { n.hidden = false; n.className = "banner"; n.textContent = d.store.notice; }
      document.querySelector('input[value="entrega"]').disabled = !d.store.delivery_enabled;
      document.querySelector('input[value="retirada"]').disabled = !d.store.pickup_enabled;
      if (!d.store.delivery_enabled) document.querySelector('input[value="retirada"]').checked = true;
      render();
    }).catch(function () { $("menu").textContent = "Não foi possível carregar o cardápio. Verifique a internet e atualize a página."; });
  }

  function render() {
    var menu = $("menu"), chips = $("chips");
    menu.textContent = ""; chips.textContent = "";
    var cats = [];
    state.items.forEach(function (i) { if (cats.indexOf(i.category) < 0) cats.push(i.category); });
    if (!cats.length) { menu.appendChild(el("p", { class: "muted", text: "O cardápio ainda está sendo preparado." })); }
    cats.forEach(function (c, idx) {
      var b = el("button", { class: "chip" + (idx === 0 ? " on" : ""), type: "button", text: c });
      b.onclick = function () {
        Array.prototype.forEach.call(chips.children, function (x) { x.classList.remove("on"); }); b.classList.add("on");
        var h = document.getElementById("cat-" + idx); if (h) h.scrollIntoView({ behavior: "smooth", block: "start" });
      };
      chips.appendChild(b);
      menu.appendChild(el("h2", { id: "cat-" + idx, text: c }));
      state.items.filter(function (i) { return i.category === c; }).forEach(function (i) { menu.appendChild(row(i)); });
    });
    updateBar();
  }

  function row(i) {
    var q = state.cart[i.id] || 0;
    var qty = el("div", { class: "qty" });
    var accepting = state.store.accepting;
    if (q > 0) {
      var m = el("button", { class: "minus", type: "button", "aria-label": "Tirar um " + i.name, text: "−" });
      m.onclick = function () { setQty(i.id, q - 1); };
      qty.appendChild(m); qty.appendChild(el("span", { text: String(q) }));
    }
    var p = el("button", { type: "button", "aria-label": "Adicionar " + i.name, text: q > 0 ? "+" : "Adicionar" });
    if (q === 0) p.className = "add";
    if (!accepting) p.disabled = true;
    p.onclick = function () { setQty(i.id, q + 1); };
    qty.appendChild(p);
    var info = el("div", {}, [el("b", { text: i.name })]);
    if (i.description) info.appendChild(el("span", { class: "muted", text: i.description }));
    info.appendChild(el("span", { class: "price", text: brl(i.price_cents) }));
    return el("div", { class: "item" }, [info, qty]);
  }

  function setQty(id, q) { q = Math.max(0, Math.min(99, q)); if (q) state.cart[id] = q; else delete state.cart[id]; render(); }
  function lines() { return state.items.filter(function (i) { return state.cart[i.id]; }).map(function (i) { return { item: i, qty: state.cart[i.id] }; }); }
  function subtotal() { return lines().reduce(function (s, l) { return s + l.item.price_cents * l.qty; }, 0); }
  function fee() { return currentKind() === "entrega" ? state.store.delivery_fee_cents : 0; }
  function currentKind() { return document.querySelector('input[name="kind"]:checked').value; }

  function updateBar() {
    var n = lines().reduce(function (s, l) { return s + l.qty; }, 0), b = $("open-cart");
    b.disabled = !n;
    b.textContent = n ? "Ver carrinho (" + n + ") · " + brl(subtotal()) : "Seu carrinho está vazio";
  }

  // -------------------------------------------------------------------- pedido
  function openCheckout() {
    var saved = recall("cliente") || {};
    if (!$("f-name").value) $("f-name").value = saved.name || "";
    if (!$("f-phone").value) $("f-phone").value = saved.phone || "";
    if (!$("f-addr").value) $("f-addr").value = saved.addr || "";
    drawCheckout(); $("sheet-checkout").classList.add("open"); $("f-name").focus();
  }
  function drawCheckout() {
    var box = $("co-lines"); box.textContent = "";
    lines().forEach(function (l) {
      box.appendChild(el("div", { class: "row" }, [el("span", { text: l.qty + "× " + l.item.name }), el("span", { text: brl(l.item.price_cents * l.qty) })]));
    });
    var k = currentKind();
    $("addr-wrap").hidden = k !== "entrega";
    $("change-wrap").hidden = $("f-pay").value !== "Dinheiro";
    var t = $("co-total"); t.textContent = "";
    t.appendChild(el("div", { class: "row" }, [el("span", { text: "Subtotal" }), el("span", { text: brl(subtotal()) })]));
    if (k === "entrega") t.appendChild(el("div", { class: "row" }, [el("span", { text: "Taxa de entrega" }), el("span", { text: brl(fee()) })]));
    t.appendChild(el("div", { class: "row total" }, [el("span", { text: "Total" }), el("span", { text: brl(subtotal() + fee()) })]));
  }
  function parseMoney(s) {
    s = (s || "").replace(/[^\d,.]/g, "").replace(/\.(?=\d{3}(\D|$))/g, "").replace(",", ".");
    var v = parseFloat(s); return isFinite(v) && v > 0 ? Math.round(v * 100) : null;
  }
  function send() {
    var err = $("co-err"); err.textContent = "";
    var k = currentKind(), name = $("f-name").value.trim(), phone = $("f-phone").value.replace(/\D/g, ""), addr = $("f-addr").value.trim();
    if (name.length < 2) { err.textContent = ERR.nome_invalido; return; }
    if (phone.length < 10) { err.textContent = ERR.telefone_invalido; return; }
    if (k === "entrega" && addr.length < 6) { err.textContent = ERR.endereco_invalido; return; }
    var total = subtotal() + fee();
    if (total - fee() < state.store.min_order_cents) { err.textContent = "O pedido mínimo é " + brl(state.store.min_order_cents) + "."; return; }
    var pay = $("f-pay").value, change = pay === "Dinheiro" ? parseMoney($("f-change").value) : null;
    if (change !== null && change < total) { err.textContent = "O troco precisa ser maior que o total (" + brl(total) + ")."; return; }
    var body = { kind: k, name: name, phone: phone, address: k === "entrega" ? addr : "", payment: pay, note: $("f-note").value.trim(),
      change_for_cents: change, items: lines().map(function (l) { return { id: l.item.id, qty: l.qty }; }) };
    var b = $("send"); b.disabled = true; b.textContent = "Enviando…";
    rpc("cf_create_order", { p_slug: slug, p_order: body }).then(function (d) {
      b.disabled = false; b.textContent = "Enviar pedido";
      if (!d.ok) { err.textContent = ERR[d.error] || "Não foi possível enviar o pedido."; if (d.error === "item_indisponivel") load(); return; }
      remember("cliente", { name: name, phone: phone, addr: addr });
      remember(KEY, d.code);
      state.cart = {}; $("sheet-checkout").classList.remove("open"); render(); showStatus(d.code);
    }).catch(function () {
      b.disabled = false; b.textContent = "Enviar pedido";
      err.textContent = "Sem conexão. Seu pedido NÃO foi enviado — tente de novo.";
    });
  }

  // ------------------------------------------------------------------- status
  var LABEL = { novo: "Aguardando a loja confirmar…", aceito: "Pedido aceito! Em breve começa o preparo.", preparo: "Em preparo 👨‍🍳",
    pronto: "Pronto!", saiu: "Saiu para entrega 🛵", entregue: "Entregue. Bom apetite!", recusado: "A loja não pôde atender este pedido.", cancelado: "Pedido cancelado." };
  var STEP = { novo: 0, aceito: 1, preparo: 2, pronto: 3, saiu: 3, entregue: 4 };
  function showStatus(code) {
    $("sheet-status").classList.add("open");
    var poll = function () {
      rpc("cf_order_status", { p_code: code }).then(function (d) {
        var b = $("st-body"); b.textContent = "";
        if (!d.ok) { b.textContent = "Pedido não encontrado."; stopPoll(); return; }
        b.appendChild(el("p", { class: "muted", text: d.store_name + " · pedido #" + d.seq }));
        b.appendChild(el("p", { style: "font-weight:700;font-size:1.1rem", text: (d.status === "pronto" && d.kind === "retirada") ? "Pronto para retirar!" : LABEL[d.status] || d.status }));
        if (d.status === "recusado" && d.reject_reason) b.appendChild(el("p", { text: "Motivo: " + d.reject_reason }));
        if (STEP[d.status] !== undefined) {
          var s = el("div", { class: "steps" }); for (var i = 0; i < 4; i++) s.appendChild(el("i", { class: i < STEP[d.status] ? "on" : "" })); b.appendChild(s);
        }
        d.items.forEach(function (i) { b.appendChild(el("div", { class: "row" }, [el("span", { text: i.qty + "× " + i.name }), el("span", { text: brl(i.price_cents * i.qty) })])); });
        b.appendChild(el("div", { class: "row total" }, [el("span", { text: "Total (" + d.payment + ")" }), el("span", { text: brl(d.total_cents) })]));
        if (d.whatsapp) { var a = el("a", { href: "https://wa.me/" + encodeURIComponent(d.whatsapp), text: "Falar com a loja no WhatsApp" }); b.appendChild(el("p", {}, [a])); }
        if (["entregue", "recusado", "cancelado"].indexOf(d.status) >= 0) { stopPoll(); remember(KEY, null); }
      }).catch(function () { /* tenta de novo no próximo ciclo */ });
    };
    stopPoll(); poll(); state.timer = setInterval(poll, 15000);
  }
  function stopPoll() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

  // ---------------------------------------------------------------------- init
  $("open-cart").onclick = openCheckout;
  $("back").onclick = function () { $("sheet-checkout").classList.remove("open"); };
  $("send").onclick = send;
  $("st-close").onclick = function () { stopPoll(); $("sheet-status").classList.remove("open"); };
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { $("sheet-checkout").classList.remove("open"); } });
  Array.prototype.forEach.call(document.querySelectorAll('input[name="kind"]'), function (r) { r.onchange = drawCheckout; });
  $("f-pay").onchange = drawCheckout;
  load();
  var pending = recall(KEY) || params.get("pedido");
  if (pending && /^[A-Za-z0-9]{6,20}$/.test(pending)) showStatus(pending);
})();
