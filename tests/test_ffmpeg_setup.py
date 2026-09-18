

import sys


class TestPortableDirs:
    def test_frozen_installs_next_to_exe(self, tmp_path, monkeypatch):
        import converter.ffmpeg_setup as fs

        monkeypatch.setattr(fs, "app_dir", lambda: tmp_path)
        monkeypatch.setattr(fs, "_is_writable", lambda d: True)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert fs.install_dir() == tmp_path / "bin"
        assert fs.data_dir() == tmp_path

    def test_readonly_falls_back_to_profile(self, tmp_path, monkeypatch):
        import converter.ffmpeg_setup as fs

        monkeypatch.setattr(fs, "app_dir", lambda: tmp_path)
        monkeypatch.setattr(fs, "_is_writable", lambda d: False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert fs.install_dir() == fs.cache_bin_dir()
        assert fs.data_dir() == fs.cache_bin_dir().parent

    def test_source_runs_use_profile(self, tmp_path, monkeypatch):
        import converter.ffmpeg_setup as fs

        monkeypatch.setattr(fs, "app_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        assert fs.install_dir() == fs.cache_bin_dir()

    def test_is_writable_detects_real_dir(self, tmp_path):
        import converter.ffmpeg_setup as fs

        assert fs._is_writable(tmp_path) is True
