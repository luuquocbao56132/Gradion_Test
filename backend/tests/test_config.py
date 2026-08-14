from app.config import load_settings


def test_load_settings_reads_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "abc123")
    monkeypatch.setenv("GEMINI_TEXT_MODEL", "text-model-x")
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "image-model-x")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("USE_FAKE_GEMINI", "0")

    settings = load_settings()

    assert settings.gemini_api_key == "abc123"
    assert settings.text_model == "text-model-x"
    assert settings.image_model == "image-model-x"
    assert settings.data_dir == (tmp_path / "data").resolve()
    assert settings.db_path == (tmp_path / "data").resolve() / "app.db"
    assert settings.use_fake_gemini is False


def test_use_fake_gemini_is_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("USE_FAKE_GEMINI", raising=False)
    assert load_settings().use_fake_gemini is False
    monkeypatch.setenv("USE_FAKE_GEMINI", "1")
    assert load_settings().use_fake_gemini is True


def test_each_load_mints_a_distinct_server_run_id(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert load_settings().server_run_id != load_settings().server_run_id
