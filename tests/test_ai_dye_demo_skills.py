"""Agent NanoDrop's router skills (skills/nanodrop-*/SKILL.md): discovered by frontmatter, composed into the system
prompt after the fixed output contract, and invisible to the other agents' skill loaders."""
from __future__ import annotations

import pytest

from src.agents.dye_demo.conversation import CORE_ROUTER_PROMPT, router_prompt
from src.agents.dye_demo.skills import discover_nanodrop_skills, loaded_skills, skills_prompt
from src.labware.skills import discover_labware_skills
from src.printing.skills import discover_printing_skills

NANODROP = ["nanodrop-edit-semantics", "nanodrop-printing", "nanodrop-dilutions", "nanodrop-plan-settings"]


def test_the_repository_skills_are_found_in_their_declared_order():
    skills = loaded_skills()
    assert [skill.name for skill in skills] == NANODROP
    assert all(skill.description and skill.body and skill.path.name == "SKILL.md" for skill in skills)
    assert {scope for skill in skills for scope in skill.scopes} == {"printing", "dilution", "plan"}


def test_the_router_prompt_is_the_contract_then_every_skill_body():
    prompt = router_prompt()
    assert prompt.startswith(CORE_ROUTER_PROMPT)
    for skill in loaded_skills():
        assert f"### SKILL {skill.name} - " in prompt and skill.body in prompt
    # what the router must say is in the contract; how to decide it is in the skills
    assert '"edit_modes"' in CORE_ROUTER_PROMPT and "nanodrop-edit-semantics" in CORE_ROUTER_PROMPT
    assert "REPLACE" in prompt and "STARTUP DEFAULT" in prompt and "RECENT FOCUS" in prompt
    assert "Can I print A11 five times?" in prompt and "SAFETY question" in prompt


def test_other_agents_loaders_do_not_see_nanodrop_skills_and_this_one_does_not_see_theirs():
    assert not set(NANODROP) & {skill.name for skill in discover_printing_skills()}
    assert not set(NANODROP) & {skill.name for skill in discover_labware_skills()}
    assert discover_printing_skills() and discover_labware_skills()


def write(root, name, frontmatter, body="Do the thing."):
    folder = root / name
    folder.mkdir()
    (folder / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


def test_frontmatter_selects_and_validates(tmp_path):
    write(tmp_path, "b", "name: b\ndescription: second\ndomain: dye_demo\nagent: nanodrop\norder: 2")
    write(tmp_path, "a", "name: a\ndescription: first\ndomain: dye_demo\nagent: nanodrop\norder: 1\nscopes: [printing]")
    write(tmp_path, "other", "name: other\ndescription: printing agent\ndomain: printing\nagent: printing")
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
    skills = discover_nanodrop_skills(tmp_path)
    assert [(skill.name, skill.scopes) for skill in skills] == [("a", ("printing",)), ("b", ())]
    assert skills_prompt(skills) == "### SKILL a - first\nDo the thing.\n\n### SKILL b - second\nDo the thing."


@pytest.mark.parametrize("frontmatter, body", [
    ("name: x\ndomain: dye_demo\nagent: nanodrop\norder: 1", "body"),                        # no description
    ("name: x\ndescription: d\ndomain: dye_demo\nagent: nanodrop\norder: first", "body"),     # order not an int
    ("name: x\ndescription: d\ndomain: dye_demo\nagent: nanodrop\norder: 1\nscopes: dilution", "body"),
    ("name: x\ndescription: d\ndomain: dye_demo\nagent: nanodrop\norder: 1", ""),            # no body
])
def test_a_malformed_nanodrop_skill_stops_the_router(tmp_path, frontmatter, body):
    write(tmp_path, "x", frontmatter, body)
    with pytest.raises(ValueError):
        discover_nanodrop_skills(tmp_path)


def test_duplicate_names_are_refused(tmp_path):
    for folder in ("one", "two"):
        write(tmp_path, folder, "name: same\ndescription: d\ndomain: dye_demo\nagent: nanodrop\norder: 1")
    with pytest.raises(ValueError):
        discover_nanodrop_skills(tmp_path)
