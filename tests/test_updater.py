"""Atualizador do programa instalado (sem rede: GitHub falso)."""
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comandafacil import updater as up

EXE = b"MZ" + b"x" * 700_000
GOOD = hashlib.sha256(EXE).hexdigest()


class Resp(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def release(tag="v2.4.0", with_sha=True, size=len(EXE)):
    assets = [{"name": up.INSTALLER_NAME, "size": size,
               "browser_download_url": "https://github.com/o/r/releases/download/x/ComandaFacil-Setup.exe"}]
    if with_sha:
        assets.append({"name": up.INSTALLER_NAME + ".sha256",
                       "browser_download_url": "https://github.com/o/r/releases/download/x/ComandaFacil-Setup.exe.sha256"})
    return {"tag_name": tag, "body": "- novidade", "html_url": "https://github.com/o/r/releases/tag/x", "assets": assets}


def fake_opener(rel, sha=GOOD, exe=EXE):
    def opener(req, timeout=0):
        url = req.full_url
        if url.endswith("/releases/latest"):
            return Resp(json.dumps(rel).encode())
        if url.endswith(".sha256"):
            return Resp(f"{sha}  ComandaFacil-Setup.exe\n".encode())
        return Resp(exe)
    return opener


class VersionTests(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(up.is_newer("v2.10.0", "2.9.9"))
        self.assertFalse(up.is_newer("2.2.0", "2.2.0"))
        self.assertFalse(up.is_newer("2.1.9", "2.2.0"))
        with self.assertRaises(up.UpdateError):
            up.parse_version("abc")

    def test_current_version_matches_file(self):
        self.assertEqual(up.current_version(), (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip())

    def test_changelog_sections(self):
        text = "# Novidades\n\n## 2.3.0 — 19/10/2026\n- a\n- b\n\n## 2.2.0\n- c\n"
        self.assertEqual([v for v, _ in up.changelog_sections(text)], ["2.3.0", "2.2.0"])
        self.assertEqual(up.notes_for("2.3.0", text), "- a\n- b")
        self.assertEqual(up.notes_for("9.9.9", text), "")

    def test_repo_changelog_has_current_version(self):
        self.assertTrue(up.notes_for(up.current_version()), "CHANGELOG.md precisa ter a versão atual")


class CheckTests(unittest.TestCase):
    def test_new_version_found(self):
        info = up.check_for_update("2.2.0", "o/r", fake_opener(release("v2.4.0")))
        self.assertEqual(info["version"], "2.4.0")
        self.assertEqual(info["notes"], "- novidade")

    def test_up_to_date_and_garbage_tag(self):
        self.assertIsNone(up.check_for_update("2.4.0", "o/r", fake_opener(release("v2.4.0"))))
        self.assertIsNone(up.check_for_update("2.4.0", "o/r", fake_opener(release("rascunho"))))

    def test_offline_message(self):
        def boom(req, timeout=0):
            raise OSError("no net")
        with self.assertRaises(up.UpdateError) as cm:
            up.check_for_update("2.2.0", "o/r", boom)
        self.assertIn("internet", str(cm.exception))


class DownloadTests(unittest.TestCase):
    def info(self, **kw):
        return up.check_for_update("2.2.0", "o/r", fake_opener(release(**kw)))

    def test_download_ok_with_progress(self):
        seen = []
        p = up.download_installer(self.info(), tempfile.mkdtemp(), lambda d, t: seen.append((d, t)),
                                  fake_opener(release()))
        self.assertEqual(p.read_bytes(), EXE)
        self.assertEqual(seen[-1], (len(EXE), len(EXE)))

    def test_bad_hash_deletes_file(self):
        d = tempfile.mkdtemp()
        with self.assertRaises(up.UpdateError) as cm:
            up.download_installer(self.info(), d, None, fake_opener(release(), sha="0" * 64))
        self.assertIn("SHA-256", str(cm.exception))
        self.assertEqual(list(Path(d).iterdir()), [])

    def test_truncated_download_rejected(self):
        d = tempfile.mkdtemp()
        with self.assertRaises(up.UpdateError):
            up.download_installer(self.info(), d, None, fake_opener(release(), exe=EXE[:1000]))
        self.assertEqual(list(Path(d).iterdir()), [])

    def test_release_without_checksum_refused(self):
        with self.assertRaises(up.UpdateError):
            up.download_installer(self.info(with_sha=False), tempfile.mkdtemp(), None, fake_opener(release()))

    def test_untrusted_host_refused(self):
        info = self.info()
        info["url"] = "https://evil.example.com/ComandaFacil-Setup.exe"
        with self.assertRaises(up.UpdateError):
            up.download_installer(info, tempfile.mkdtemp(), None, fake_opener(release()))
        info["url"] = "http://github.com/x.exe"
        with self.assertRaises(up.UpdateError):
            up.download_installer(info, tempfile.mkdtemp(), None, fake_opener(release()))

    def test_launch_uses_silent_flags(self):
        calls = []
        up.launch_installer("C:/x/Setup.exe", popen=lambda args, **k: calls.append(args))
        self.assertEqual(calls[0][0], "C:/x/Setup.exe")
        self.assertIn("/SILENT", calls[0])
        self.assertIn("/CLOSEAPPLICATIONS", calls[0])


if __name__ == "__main__":
    unittest.main()
