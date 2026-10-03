"""Build only on the target OS; no fake cross compilation."""
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    # PyInstaller uses ':' on POSIX and ';' on Windows for --add-data.
    data_sep=';' if sys.platform=='win32' else ':'
    args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--name','交通可达性分析系统','--add-data',f'{ROOT / "frontend"}{data_sep}frontend','--add-data',f'{ROOT / "resources"}{data_sep}resources','--collect-submodules','keyring.backends','--hidden-import','uvicorn.logging','--hidden-import','uvicorn.loops.auto','--hidden-import','uvicorn.protocols.http.auto','--hidden-import','uvicorn.lifespan.on']
    if sys.platform=='win32': args+=['--onefile']
    if sys.platform=='darwin':args+=['--osx-bundle-identifier','local.traffic.accessibility']
    args+=[str(ROOT/'start.py')]
    subprocess.run(args,cwd=ROOT,check=True)
