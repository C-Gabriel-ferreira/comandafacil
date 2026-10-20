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
  function enableDownload(url, version, bytes) {
    dl.href = url; dl.removeAttribute("aria-disabled");
    var mb = bytes ? " · " + Math.round(bytes / 1048576) + " MB" : "";
    $("dl-meta").textContent = "Windows 10 ou 11" + (version ? " · versão " + version : "") + mb;
    $("dl-note").textContent = "Instalador .exe com o PDV em janela. Funciona sem internet depois de instalado.";
  }
  if (cfg.downloadUrl) {
    enableDownload(cfg.downloadUrl);
  } else if (cfg.repo) {
    fetch("https://api.github.com/repos/" + cfg.repo + "/releases/latest")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rel) {
        var a = rel && (rel.assets || []).find(function (x) { return /\.exe$/i.test(x.name); });
        if (a) enableDownload(a.browser_download_url, (rel.tag_name || "").replace(/^v/, ""), a.size);
      })
      .catch(function () {});
  }
  // ---- novidades: uma entrada por versão publicada (lidas das Releases, sem republicar o site)
  function addRich(el, text) {
    text.split(/(\*\*[^*]+\*\*)/).forEach(function (part) {
      if (!part) return;
      if (/^\*\*[^*]+\*\*$/.test(part)) { var b = document.createElement("b"); b.textContent = part.slice(2, -2); el.appendChild(b); }
      else el.appendChild(document.createTextNode(part));
    });
  }
  function renderNews(rels) {
    var box = $("news-list"); if (!box) return;
    box.textContent = "";
    var shown = 0;
    rels.forEach(function (rel) {
      if (rel.draft || rel.prerelease || shown >= 8) return;
      var items = (rel.body || "").split("\n---")[0].split("\n").filter(function (l) { return /^\s*[-*] /.test(l); });
      if (!items.length) return;
      shown++;
      var art = document.createElement("article"); art.className = "news-item";
      var h = document.createElement("h3");
      var date = rel.published_at ? new Date(rel.published_at).toLocaleDateString("pt-BR") : "";
      h.textContent = "Versão " + (rel.tag_name || "").replace(/^v/, "") + (date ? " · " + date : "");
      art.appendChild(h);
      var ul = document.createElement("ul");
      items.forEach(function (l) { var li = document.createElement("li"); addRich(li, l.replace(/^\s*[-*] /, "")); ul.appendChild(li); });
      art.appendChild(ul); box.appendChild(art);
    });
    if (!shown) box.innerHTML = '<p class="muted">Em breve.</p>';
  }
  if (cfg.repo) {
    fetch("https://api.github.com/repos/" + cfg.repo + "/releases?per_page=10")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(renderNews)
      .catch(function () { var b = $("news-list"); if (b) b.innerHTML = '<p class="muted">Não foi possível carregar agora.</p>'; });
  }
  if (cfg.whatsapp) {
    document.querySelectorAll(".plan-cta").forEach(function (a) {
      var msg = encodeURIComponent("Olá! Tenho interesse no plano " + a.dataset.plan + " do ComandaFácil.");
      a.href = "https://wa.me/" + cfg.whatsapp + "?text=" + msg; a.target = "_blank"; a.rel = "noopener";
    });
  }
})();
