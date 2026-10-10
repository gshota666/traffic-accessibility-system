"""Package a native Windows build; never includes user databases or credentials."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.version import APP_VERSION


def package():
    if sys.platform != 'win32':
        raise RuntimeError('Windows 发布包必须在 Windows 原生构建并冒烟通过后生成')
    exe = ROOT / 'dist' / '交通可达性分析系统.exe'
    if not exe.is_file():
        raise FileNotFoundError(exe)
    output = ROOT / 'dist' / f'交通可达性分析系统_Windows_x64_{APP_VERSION}.zip'
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, exe.name)
        archive.write(ROOT / 'Windows使用说明.md', 'Windows使用说明.md')
        archive.writestr('版本与校验.json', json.dumps({
            'version': APP_VERSION, 'platform': 'Windows x64',
            'executable': exe.name, 'sha256': digest,
            'data_included': False,
        }, ensure_ascii=False, indent=2))
    print(output)


if __name__ == '__main__':
    package()
