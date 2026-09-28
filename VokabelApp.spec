# Erstellt von CodeCockpit. Sie dürfen die Datei anpassen, das Cockpit nutzt sie
# bei jedem Bau weiter. Pfade sind relativ zum Ordner Code.

a = Analysis(['engine.py'], pathex=[], binaries=[], datas=[],
             hiddenimports=[], excludes=[])
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='VokabelApp',
          console=False, icon=None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='VokabelApp', upx=False)
