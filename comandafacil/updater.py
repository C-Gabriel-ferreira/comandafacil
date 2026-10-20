"""Atualização do programa instalado (sem Qt).

Fluxo: o PDV consulta a última Release no GitHub; se houver versão nova, mostra as novidades e, com um clique do
gerente, baixa o instalador, confere o tamanho e o SHA-256 publicados na própria Release e o executa em modo
"silencioso com barra de progresso". O instalador mantém o mesmo AppId, então atualiza por cima e preserva os
dados (ficam em %LOCALAPPDATA%\\ComandaFacil\\dados) e o banco se migra sozinho, com backup antes.

Para lançar uma versão nova: edite VERSION e CHANGELOG.md e faça merge na main (ver RELEASE.md).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_REPO = "C-Gabriel-ferreira/comandafacil"
INSTALLER_NAME = "ComandaFacil-Setup.exe"
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
USER_AGENT = "ComandaFacil-Updater"


class UpdateError(Exception):
    pass


def _root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def current_version() -> str:
    try:
        return (_root() / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(v: str) -> tuple:
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", v or "")
    if not m:
        raise UpdateError(f"Versão inválida: {v!r}")
    return tuple(int(x) for x in m.groups())


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


# ------------------------------------------------------------------------------ novidades
def changelog_text() -> str:
    try:
        return (_root() / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def changelog_sections(text: str) -> list[tuple[str, str]]:
    """[(versão, corpo)] na ordem do arquivo (mais nova primeiro). Títulos no formato '## 2.3.0 — data'."""
    out, ver, body = [], None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+v?(\d+\.\d+\.\d+)", line)
        if m:
            if ver:
                out.append((ver, "\n".join(body).strip()))
            ver, body = m.group(1), []
        elif ver:
            body.append(line)
    if ver:
        out.append((ver, "\n".join(body).strip()))
    return out


def notes_for(version: str, text: str | None = None) -> str:
    for ver, body in changelog_sections(changelog_text() if text is None else text):
        if ver == version:
            return body
    return ""


# ------------------------------------------------------------------------------- consulta
def _open(url, opener=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        return (opener or urllib.request.urlopen)(req, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise UpdateError("Sem conexão com a internet para procurar atualização.") from e


def check_for_update(current=None, repo=DEFAULT_REPO, opener=None):
    """Devolve None (já está na última) ou {'version','notes','url','size','sha_url','page'}."""
    current = current or current_version()
    with _open(f"https://api.github.com/repos/{repo}/releases/latest", opener) as r:
        try:
            rel = json.loads(r.read())
        except ValueError as e:
            raise UpdateError("Resposta inesperada do GitHub.") from e
    tag = rel.get("tag_name") or ""
    try:
        if not is_newer(tag, current):
            return None
    except UpdateError:
        return None
    assets = {a.get("name"): a for a in rel.get("assets", [])}
    exe = assets.get(INSTALLER_NAME)
    sha = assets.get(INSTALLER_NAME + ".sha256")
    return {"version": tag.lstrip("v"), "notes": (rel.get("body") or "").strip(),
            "url": exe and exe.get("browser_download_url"), "size": exe and exe.get("size"),
            "sha_url": sha and sha.get("browser_download_url"), "page": rel.get("html_url", "")}


# ------------------------------------------------------------------------------ download
def _check_url(url):
    p = urlparse(url or "")
    if p.scheme != "https" or p.hostname not in ALLOWED_HOSTS:
        raise UpdateError("Endereço de download não confiável; atualização cancelada.")


def download_installer(info, dest_dir, progress=None, opener=None) -> Path:
    """Baixa e valida. `progress(baixado, total)` é chamado durante o download."""
    if not info.get("url") or not info.get("sha_url"):
        raise UpdateError("Esta versão não tem verificação de integridade. Baixe pelo site ou pela página da Release.")
    _check_url(info["url"])
    _check_url(info["sha_url"])
    with _open(info["sha_url"], opener) as r:
        m = re.search(rb"\b([0-9a-fA-F]{64})\b", r.read(4096))
    if not m:
        raise UpdateError("Arquivo de verificação (SHA-256) inválido.")
    expected = m.group(1).decode().lower()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"ComandaFacil-Setup-{info['version']}.exe"
    h, done = hashlib.sha256(), 0
    with _open(info["url"], opener, timeout=30) as r, open(path, "wb") as f:
        total = int(info.get("size") or r.headers.get("Content-Length") or 0)
        while chunk := r.read(256 * 1024):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if info.get("size") and done != int(info["size"]):
        path.unlink(missing_ok=True)
        raise UpdateError("O download veio incompleto. Tente de novo.")
    if h.hexdigest() != expected:
        path.unlink(missing_ok=True)
        raise UpdateError("O instalador baixado não confere com o SHA-256 da Release; atualização cancelada.")
    return path


def launch_installer(path, popen=subprocess.Popen):
    """Inicia o instalador (progresso visível, sem perguntas) e reabre o programa ao terminar.
    O chamador deve fechar o PDV logo depois."""
    if os.name != "nt" and popen is subprocess.Popen:
        raise UpdateError("A atualização automática só funciona no Windows.")
    popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"], close_fds=True,
          creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
