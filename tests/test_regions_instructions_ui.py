from pathlib import Path


def _read_index_html() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "web" / "index.html").read_text(encoding="utf-8")


def test_auto_instructions_appear_after_region_definition_mode_section():
    html = _read_index_html()

    region_mode_idx = html.find("<label>Region Definition Mode</label>")
    auto_instructions_idx = html.find('id="autoInstructions"')

    assert region_mode_idx != -1, "Could not find Region Definition Mode label"
    assert auto_instructions_idx != -1, "Could not find autoInstructions block"
    assert auto_instructions_idx > region_mode_idx


def test_manual_instructions_exist_and_hidden_by_default():
    html = _read_index_html()

    manual_id_idx = html.find('id="manualInstructions"')
    assert manual_id_idx != -1, "Could not find manualInstructions block"

    tag_start = html.rfind("<div", 0, manual_id_idx)
    tag_end = html.find(">", manual_id_idx)
    assert (
        tag_start != -1 and tag_end != -1
    ), "Could not locate manualInstructions opening tag"

    opening_tag = html[tag_start : tag_end + 1]
    assert "style=" in opening_tag
    assert "display: none" in opening_tag


def test_toggle_region_mode_updates_instruction_visibility_for_both_modes():
    html = _read_index_html()

    fn_start = html.find("function toggleRegionMode(isManual)")
    assert fn_start != -1, "Could not find toggleRegionMode function"

    fn_end = html.find("// Show/hide relevant sections", fn_start)
    assert fn_end != -1, "Could not find expected toggleRegionMode body marker"
    fn_body = html[fn_start:fn_end]

    manual_branch_idx = fn_body.find("if (isManual)")
    else_branch_idx = fn_body.find("} else {")

    assert manual_branch_idx != -1, "Could not find manual branch"
    assert else_branch_idx != -1, "Could not find automatic branch"

    manual_branch = fn_body[manual_branch_idx:else_branch_idx]
    auto_branch = fn_body[else_branch_idx:]

    assert "autoInstructions.style.display = 'none'" in manual_branch
    assert "manualInstructions.style.display = 'block'" in manual_branch

    assert "autoInstructions.style.display = 'block'" in auto_branch
    assert "manualInstructions.style.display = 'none'" in auto_branch
