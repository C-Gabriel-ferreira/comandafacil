(function () {
  var cfg = window.COMANDAFACIL || {};
  var brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  var $ = function (id) { return document.getElementById(id); };

  // ---- comanda de demonstração
  var ITEMS = [
    { n: "Água mineral", c: 300 }, { n: "Refrigerante", c: 600 },
    { n: "Suco natural", c: 800 }, { n: "Hambúrguer", c: 2200 },
    { n: "Batata frita", c: 1800 }, { n: "Prato feito", c: 2500 }
  ];
  var qty = {}, paid = false;
  var menu = $("menu"), lines = $("lines"), ticket = $("ticket");

  ITEMS.forEach(function (it, i) {
    var b = document.createElement("button");
    b.type = "button";
    b.innerHTML = it.n + "<small>" + brl.format(it.c / 100) + "</small>";
    b.addEventListener("click", function () { qty[i] = (qty[i] || 0) + 1; render(); });
    menu.appendChild(b);
  });

  function render() {
    var sub = 0, html = "";
    ITEMS.forEach(function (it, i) {
      if (!qty[i]) return;
      sub += it.c * qty[i];
      html += '<li><span class="q">' + qty[i] + 'x</span><span>' + it.n + '</span><span>' + brl.format(it.c * qty[i] / 100) + "</span></li>";
    });
    var svc = $("svc").checked ? Math.round(sub * 0.1) : 0;
    lines.innerHTML = html;
    $("empty").hidden = sub > 0;
    $("sub").textContent = brl.format(sub / 100);
    $("svcv").textContent = brl.format(svc / 100);
    $("tot").textContent = brl.format((sub + svc) / 100);
    ticket.classList.toggle("paid", paid);
    $("close").textContent = paid ? "Nova comanda" : "Fechar conta";
    $("close").disabled = !paid && sub === 0;
    Array.prototype.forEach.call(menu.children, function (b) { b.disabled = paid; });
    $("svc").disabled = paid;
  }
  $("svc").addEventListener("change", render);
  $("clear").addEventListener("click", function () { qty = {}; paid = false; render(); });
  $("close").addEventListener("click", function () {
    if (paid) { qty = {}; paid = false; } else { paid = true; }
    render();
  });
  render();

  // ---- painel de mesas de exemplo
  var STATES = ["livre", "aberta", "conta"], LABEL = { livre: "livre", aberta: "aberta", conta: "conta" };
  var initial = { 1: "aberta", 2: "livre", 3: "aberta", 4: "aberta", 5: "livre", 6: "conta", 7: "livre", 8: "aberta", 9: "livre", 10: "livre", 11: "conta", 12: "livre" };
  var grid = $("grid");
  for (var m = 1; m <= 12; m++) {
    (function (n) {
      var b = document.createElement("button");
      b.type = "button";
      b.dataset.s = initial[n];
      function draw() { b.innerHTML = "Mesa " + n + "<small>" + LABEL[b.dataset.s] + "</small>"; b.setAttribute("aria-label", "Mesa " + n + ", " + LABEL[b.dataset.s]); }
      b.addEventListener("click", function () {
        b.dataset.s = STATES[(STATES.indexOf(b.dataset.s) + 1) % STATES.length]; draw();
      });
      draw();
      grid.appendChild(b);
    })(m);
  }

  $("year").textContent = new Date().getFullYear();

  // ---- download e contato (config.js)
  var dl = $("dl");
  if (cfg.downloadUrl) {
    dl.href = cfg.downloadUrl; dl.removeAttribute("aria-disabled"); dl.setAttribute("download", "");
    $("dl-note").textContent = "Windows 10 ou 11. Arquivo .zip.";
  }
  if (cfg.whatsapp) {
    document.querySelectorAll(".plan-cta").forEach(function (a) {
      var msg = encodeURIComponent("Olá! Tenho interesse no plano " + a.dataset.plan + " do ComandaFácil.");
      a.href = "https://wa.me/" + cfg.whatsapp + "?text=" + msg; a.target = "_blank"; a.rel = "noopener";
    });
  }
})();
