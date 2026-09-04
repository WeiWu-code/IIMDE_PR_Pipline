from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
API = (ROOT / "evoagent" / "api.py").read_text(encoding="utf-8")


def test_frontend_uses_xidian_branding_and_light_console_theme():
    assert "智数所PR自动审查工具" in INDEX
    assert '/assets/xidian-logo-on.png' in INDEX
    assert (ROOT / "web" / "assets" / "xidian-logo-on.png").is_file()
    assert 'color-scheme: light' in CSS
    assert '--accent: #1f5fbf' in CSS
    assert 'radial-gradient(circle at 92% -10%' not in CSS
    assert 'grid-template-columns: 280px minmax(0, 1fr)' in CSS
    assert 'white-space: nowrap' in CSS
    assert 'xidian-logo-on.png' in API
