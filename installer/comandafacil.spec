# PyInstaller: gera dist/ComandaFacil/ComandaFacil.exe (PDV em janela + servidor para celulares, sem console)
import os
from PyInstaller.utils.hooks import collect_data_files

root = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(root, "templates"), "templates"),
         (os.path.join(root, "static"), "static"),
         (os.path.join(root, "VERSION"), "."), (os.path.join(root, "CHANGELOG.md"), ".")] + collect_data_files("tzdata")

a = Analysis([os.path.join(root, "comandafacil_desktop.py")], pathex=[root], datas=datas,
             hiddenimports=["waitress", "tzdata", "PySide6.QtPrintSupport"],
             excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtSvg",
                       "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ComandaFacil", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ComandaFacil")
