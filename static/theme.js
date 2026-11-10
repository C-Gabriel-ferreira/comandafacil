// Aplica o tema salvo antes de a página aparecer (evita o "flash" claro/escuro).
(function () {
  try {
    var t = localStorage.getItem("cf_theme");
    if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    document.documentElement.dataset.theme = t;
  } catch (e) {}
})();
