# PyInstaller: gera dist/ComandaFacil/ComandaFacil.exe (sem console)
import os
from PyInstaller.utils.hooks import collect_data_files

root = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(root, "templates"), "templates"),
         (os.path.join(root, "static"), "static")] + collect_data_files("tzdata")

a = Analysis([os.path.join(root, "launcher.py")], pathex=[root], datas=datas,
             hiddenimports=["waitress", "tzdata"], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ComandaFacil", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ComandaFacil")
