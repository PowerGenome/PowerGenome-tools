from datetime import datetime
from pathlib import Path


def _read_index_html() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "web" / "index.html").read_text(encoding="utf-8")


def test_model_setup_uses_row_based_planning_period_editor_with_hidden_inputs():
    html = _read_index_html()

    assert 'id="planningPeriodEditor"' in html
    assert 'id="planningPeriodRows"' in html
    assert 'id="addPlanningPeriodBtn"' in html
    assert 'id="modelYears" type="hidden"' in html
    assert 'id="planningYears" type="hidden"' in html


def test_planning_period_editor_initializes_with_current_year_and_2030():
    html = _read_index_html()
    current_year = datetime.now().year

    assert "const currentYear = new Date().getFullYear();" in html
    assert "addPlanningPeriodRow(String(currentYear), '2030');" in html


def test_planning_period_editor_derives_following_starts_and_notifies_model_year_handlers():
    html = _read_index_html()

    assert "previousPlanningYear + 1" in html
    assert "row.dataset.startMode = 'manual';" in html
    assert "row.dataset.startMode = currentValue === '' ? 'auto' : 'manual';" in html
    assert (
        "const shouldAutofill = row.dataset.startMode !== 'manual' || currentStart === '';"
        in html
    )
    assert (
        "modelYearsInput.dispatchEvent(new Event('input', { bubbles: true }));" in html
    )
    assert "Error: Complete both years for each planning period." in html
