from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def test_review_runtime_uses_live_nodes_instead_of_primary_json_output():
    assert 'class="result runtime-result empty"' in INDEX
    assert "function renderRunGraph" in JS
    assert "function followSubmittedTask" in JS
    assert "renderReviewRun(task)" in JS
    assert "output.textContent = formatJson(data)" not in JS
    assert "@keyframes node-pulse" in CSS
    assert "@keyframes node-flow" in CSS


def test_task_report_is_structured_with_optional_raw_debug_data():
    assert 'class="task-report-view"' in INDEX
    assert "function renderTaskReport" in JS
    assert "function renderFindings" in JS
    assert "查看原始 JSON" in JS
    assert '$("#task-report").textContent = formatJson(task)' not in JS
