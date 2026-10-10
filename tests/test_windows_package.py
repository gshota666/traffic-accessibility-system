import hashlib
import json
import zipfile

import pytest
from scripts import package_windows


def test_windows_package_rejects_cross_platform(monkeypatch):
    monkeypatch.setattr(package_windows.sys, 'platform', 'darwin')
    with pytest.raises(RuntimeError, match='Windows'):
        package_windows.package()


def test_windows_package_only_contains_release_files(tmp_path, monkeypatch):
    monkeypatch.setattr(package_windows.sys, 'platform', 'win32')
    monkeypatch.setattr(package_windows, 'ROOT', tmp_path)
    (tmp_path / 'dist').mkdir()
    payload = b'fake-executable-fixture-not-a-real-build'
    (tmp_path / 'dist' / '交通可达性分析系统.exe').write_bytes(payload)
    (tmp_path / 'Windows使用说明.md').write_text('说明', encoding='utf-8')
    (tmp_path / 'credentials.json').write_text('private fixture')
    (tmp_path / 'traffic.db').write_bytes(b'private fixture')
    package_windows.package()
    archive_path = next((tmp_path / 'dist').glob('*.zip'))
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {
            '交通可达性分析系统.exe', 'Windows使用说明.md', '版本与校验.json'}
        manifest = json.loads(archive.read('版本与校验.json'))
        assert manifest['sha256'] == hashlib.sha256(payload).hexdigest()
        assert manifest['data_included'] is False
