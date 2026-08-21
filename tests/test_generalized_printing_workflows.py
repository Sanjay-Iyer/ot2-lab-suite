"""The generalized AI-facing layer over the two validated printing workflows.

What this file guards:

  * the two hand-validated ground truths are untouched and still resolve
  * both generalized templates are valid, resolvable, and free of the answers
  * a template-driven four-clover job reproduces the manual ground truth's
    physical behaviour exactly
  * the four-clover schema fails closed on the edits an agent is most likely to
    get wrong
  * one Printing Agent routes a request to the right family, tools, and skill

Nothing here needs an LLM and nothing here contacts a robot. The live
natural-language reproduction lives in
``scripts/reproduce_ground_truth_from_language.py``.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.printing.clover.loader import (  # noqa: E402
    CloverJobLoadError,
    load_experiment_job,
    load_experiment_job_mapping,
    load_manual_executor_config,
)
from src.printing.clover.resolver import (  # noqa: E402
    CloverResolutionError,
    resolve_experiment_job,
    resolve_manual_config,
)
from src.printing.clover.review import render_clover_review  # noqa: E402

STANDARD_GROUND_TRUTH = REPO / "configs" / "experiments" / "01_printing_standard.yaml"
CLOVER_GROUND_TRUTH = REPO / "configs" / "experiments" / "02_printing_four_clover.yaml"

TEMPLATE_DIR = REPO / "configs" / "templates" / "printing"
STANDARD_TEMPLATE = TEMPLATE_DIR / "01_printing_standard.template.yaml"
CLOVER_TEMPLATE = TEMPLATE_DIR / "02_printing_four_clover.template.yaml"

CLOVER_PROFILE = "configs/machines/ot2_four_clover_printing_p20_v1.yaml"
CLOVER_EXECUTOR = REPO / "src" / "protocols" / "printing" / "02_printing_four_clover.py"
STANDARD_EXECUTOR = REPO / "src" / "protocols" / "printing" / "01_printing_standard.py"


def _clover_job(**overrides) -> dict:
    """A minimal valid four-clover job, unrelated to any real study."""
    job = {
        "schema_version": "four-clover-experiment-job/v1",
        "machine_profile": CLOVER_PROFILE,
        "experiment": {
            "metadata": {"experiment_id": "unit_probe", "title": "Unit probe"},
            "source": {
                "liquid_id": "material_1",
                "display_name": "Material 1",
                "well": "A1",
                "loaded_volume_ul": 5000.0,
                "minimum_remaining_ul": 100.0,
            },
            "printing": {"droplet_volume_ul": 4.0},
            "default_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clovers": [{"name": "one", "reference_well": "D6"}],
        },
    }
    for path, value in overrides.items():
        target = job["experiment"]
        keys = path.split(".")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return job


# ── the ground truths remain untouched ────────────────────────────────────────────

def test_ground_truth_configs_are_present_and_self_contained() -> None:
    """Both must stay hand-editable with no template or profile indirection."""
    assert STANDARD_GROUND_TRUTH.is_file()
    assert CLOVER_GROUND_TRUTH.is_file()
    clover = yaml.safe_load(CLOVER_GROUND_TRUTH.read_text(encoding="utf-8"))
    # The manual fallback declares its own hardware on purpose: it must run with
    # no schema layer, no profile lookup, and no agent.
    assert "machine_profile" not in clover
    for section in ("deck", "pipette", "printing", "destination", "tips", "safety"):
        assert section in clover, section


def test_ground_truth_clover_still_resolves_to_its_validated_geometry() -> None:
    config, run_modes = load_manual_executor_config(CLOVER_GROUND_TRUTH)
    plan = resolve_manual_config(config, experiment_id="ground_truth")

    assert plan.totals.clover_count == 4
    assert plan.totals.deposit_count == 16
    assert plan.droplet_volume_ul == 5.0
    # paper surface 6.0 mm + validated 0.5 mm standoff
    assert plan.paper_surface_mm == 6.0
    assert plan.dispense_standoff_mm == 0.5
    assert plan.absolute_dispense_mm == 6.5
    assert run_modes["do_print"] is True

    coordinates = {
        clover.name: {
            droplet.key: (round(droplet.x_mm, 2), round(droplet.y_mm, 2))
            for droplet in clover.droplets
        }
        for clover in plan.clovers
    }
    assert coordinates["sep_2mm"] == {
        "d1": (22.38, 66.24), "d2": (24.38, 66.24),
        "d3": (22.38, 64.24), "d4": (24.38, 64.24),
    }
    assert coordinates["sep_5mm"] == {
        "d1": (101.88, 67.74), "d2": (106.88, 67.74),
        "d3": (101.88, 62.74), "d4": (106.88, 62.74),
    }


# ── the templates ─────────────────────────────────────────────────────────────────

def test_both_templates_exist_where_the_workflows_expect_them() -> None:
    assert STANDARD_TEMPLATE.is_file()
    assert CLOVER_TEMPLATE.is_file()


def test_clover_template_is_itself_a_valid_resolvable_job() -> None:
    """A template that does not validate teaches a language nothing accepts."""
    job = load_experiment_job(CLOVER_TEMPLATE)
    plan = resolve_experiment_job(job)
    assert plan.totals.clover_count >= 1
    assert plan.absolute_dispense_mm == 6.5


#: Answers a blind agent must derive from the scientist's request, not read here.
GROUND_TRUTH_ANSWERS = (
    "sep_2mm", "sep_3mm", "sep_4mm", "sep_5mm",
    "nanoparticle", "crystal violet", "crystal_violet",
    "b11", "1_128", "1/128",
)


def test_clover_template_does_not_pre_solve_the_ground_truth() -> None:
    text = CLOVER_TEMPLATE.read_text(encoding="utf-8").lower()
    leaked = [answer for answer in GROUND_TRUTH_ANSWERS if answer in text]
    assert not leaked, f"the template leaks ground-truth answers: {leaked}"


def test_clover_template_uses_neutral_placeholder_names() -> None:
    mapping = yaml.safe_load(CLOVER_TEMPLATE.read_text(encoding="utf-8"))
    experiment = mapping["experiment"]
    assert experiment["metadata"]["experiment_id"].startswith("template_")
    assert experiment["source"]["liquid_id"] == "material_1"
    assert all(
        clover["name"].startswith("placeholder") for clover in experiment["clovers"]
    )


def test_clover_template_references_a_registered_profile_and_inlines_no_hardware() -> None:
    mapping = yaml.safe_load(CLOVER_TEMPLATE.read_text(encoding="utf-8"))
    assert mapping["machine_profile"] == CLOVER_PROFILE
    assert "machine" not in mapping
    for forbidden in ("deck", "pipette", "tips", "safety", "flow_rates", "validation"):
        assert forbidden not in mapping["experiment"], forbidden


# ── the template route reproduces the manual route ────────────────────────────────

def test_template_driven_job_matches_the_manual_ground_truth_physically() -> None:
    """The whole point of the generalized layer: same experiment, same behaviour."""
    manual_config, _ = load_manual_executor_config(CLOVER_GROUND_TRUTH)
    manual = resolve_manual_config(manual_config, experiment_id="ground_truth")

    job = load_experiment_job_mapping({
        "schema_version": "four-clover-experiment-job/v1",
        "machine_profile": CLOVER_PROFILE,
        "experiment": {
            "metadata": {
                "experiment_id": "clover_separation_sweep",
                "title": "Droplet separation sweep",
            },
            "source": {
                "liquid_id": "bp",
                "display_name": "BP",
                "well": "A2",
                "loaded_volume_ul": 5000.0,
                "minimum_remaining_ul": 100.0,
            },
            "printing": {
                "droplet_volume_ul": 5.0,
                "layers": 1,
                "inter_drop_delay_s": 2.0,
                "order": "clover_by_clover",
            },
            "default_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clovers": [
                {"name": "a", "reference_well": "B2",
                 "geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0}},
                {"name": "b", "reference_well": "B5",
                 "geometry": {"half_width_mm": 1.5, "half_height_mm": 1.5}},
                {"name": "c", "reference_well": "B8",
                 "geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0}},
                {"name": "d", "reference_well": "B11",
                 "geometry": {"half_width_mm": 2.5, "half_height_mm": 2.5}},
            ],
        },
    })
    templated = resolve_experiment_job(job)

    # Names and ids differ on purpose; the physical fingerprint must not.
    assert templated.physical_sha256() == manual.physical_sha256()


def test_physical_fingerprint_ignores_naming_but_not_geometry() -> None:
    base = _clover_job()
    renamed = copy.deepcopy(base)
    renamed["experiment"]["metadata"]["experiment_id"] = "renamed_probe"
    renamed["experiment"]["metadata"]["title"] = "A different title"
    renamed["experiment"]["clovers"][0]["name"] = "differently_named"
    renamed["experiment"]["source"]["display_name"] = "Something else"

    moved = copy.deepcopy(base)
    moved["experiment"]["clovers"][0]["reference_well"] = "D7"

    first = resolve_experiment_job(load_experiment_job_mapping(base))
    assert (
        resolve_experiment_job(load_experiment_job_mapping(renamed)).physical_sha256()
        == first.physical_sha256()
    )
    assert (
        resolve_experiment_job(load_experiment_job_mapping(moved)).physical_sha256()
        != first.physical_sha256()
    )


# ── the schema fails closed ───────────────────────────────────────────────────────

def test_an_inline_machine_section_is_refused() -> None:
    job = _clover_job()
    job["machine"] = {"robot_type": "OT-2"}
    with pytest.raises(CloverJobLoadError, match="may not contain an inline"):
        load_experiment_job_mapping(job)


def test_a_missing_machine_profile_is_refused() -> None:
    job = _clover_job()
    del job["machine_profile"]
    with pytest.raises(CloverJobLoadError, match="must reference a registered"):
        load_experiment_job_mapping(job)


def test_an_unregistered_machine_profile_is_refused() -> None:
    job = _clover_job()
    job["machine_profile"] = "configs/printing/four_clover_spacing_v13.yaml"
    with pytest.raises(CloverJobLoadError, match="registered laboratory profile"):
        load_experiment_job_mapping(job)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("printing.droplet_volume_ul", 0.0, "greater_than"),
        ("printing.droplet_volume_ul", -5.0, "greater_than"),
        ("printing.droplet_volume_ul", 25.0, "less_than_equal"),
        ("printing.layers", 0, "greater_than_equal"),
        ("printing.inter_layer_delay_s", -1.0, "greater_than_equal"),
        ("printing.order", "spiral", "literal_error"),
        ("default_geometry.half_width_mm", 0.0, "greater_than"),
        ("default_geometry.half_height_mm", -2.0, "greater_than"),
        ("source.well", "Z9", "string_pattern_mismatch"),
        ("source.loaded_volume_ul", 0.0, "greater_than"),
        ("metadata.experiment_id", "Not Snake Case", "string_pattern_mismatch"),
    ],
)
def test_invalid_scientific_values_are_rejected_by_the_schema(
    field: str, value, message: str
) -> None:
    with pytest.raises((CloverJobLoadError, ValidationError)) as error:
        load_experiment_job_mapping(_clover_job(**{field: value}))
    assert message in str(error.value)


def test_an_unknown_paper_position_is_rejected() -> None:
    job = _clover_job(clovers=[{"name": "one", "reference_well": "J13"}])
    with pytest.raises((CloverJobLoadError, ValidationError)):
        load_experiment_job_mapping(job)


def test_duplicate_clover_names_are_rejected() -> None:
    job = _clover_job(
        clovers=[
            {"name": "same", "reference_well": "C3"},
            {"name": "same", "reference_well": "C6"},
        ]
    )
    with pytest.raises((CloverJobLoadError, ValidationError), match="unique"):
        load_experiment_job_mapping(job)


def test_a_reserve_above_the_load_is_rejected() -> None:
    job = _clover_job()
    job["experiment"]["source"]["minimum_remaining_ul"] = 6000.0
    with pytest.raises((CloverJobLoadError, ValidationError), match="reserves"):
        load_experiment_job_mapping(job)


def test_a_pattern_that_leaves_the_paper_is_rejected_at_resolution() -> None:
    job = load_experiment_job_mapping(
        _clover_job(
            clovers=[{"name": "off_paper", "reference_well": "A1",
                      "x_offset_mm": -55.0}]
        )
    )
    with pytest.raises(CloverResolutionError, match="outside the usable paper box"):
        resolve_experiment_job(job)


def test_insufficient_source_is_rejected_at_resolution() -> None:
    # One clover of 4 uL droplets needs 16 uL; 20 uL loaded with a 10 uL reserve
    # cannot cover that.
    job = _clover_job()
    job["experiment"]["source"]["loaded_volume_ul"] = 20.0
    job["experiment"]["source"]["minimum_remaining_ul"] = 10.0
    with pytest.raises(CloverResolutionError, match="insufficient source"):
        resolve_experiment_job(load_experiment_job_mapping(job))


def test_a_source_that_would_uncover_the_tip_is_rejected() -> None:
    """A raw volume budget passes here; the submersion check is what catches it."""
    job = _clover_job()
    job["experiment"]["source"]["loaded_volume_ul"] = 200.0
    job["experiment"]["source"]["minimum_remaining_ul"] = 0.0
    with pytest.raises(CloverResolutionError, match="submerged"):
        resolve_experiment_job(load_experiment_job_mapping(job))


def test_droplets_sharing_one_coordinate_are_always_fatal() -> None:
    """Stacking all four droplets on one point is refused regardless of mode."""
    job = _clover_job(
        **{
            "default_geometry": {"half_width_mm": 1e-9, "half_height_mm": 1e-9},
            "clovers": [{"name": "one", "reference_well": "D6"}],
        }
    )
    with pytest.raises(CloverResolutionError, match="same coordinate"):
        resolve_experiment_job(load_experiment_job_mapping(job))


def test_crowding_is_fatal_when_the_profile_asks_for_error_mode() -> None:
    """The warn/error switch is a laboratory setting, and it really switches."""
    from src.printing.clover.resolver import (
        build_executor_config,
        resolve_executor_config,
    )

    job = load_experiment_job_mapping(
        _clover_job(
            **{
                "default_geometry": {"half_width_mm": 0.2, "half_height_mm": 0.2},
                "clovers": [{"name": "one", "reference_well": "D6"}],
            }
        )
    )
    config = build_executor_config(job)
    assert config["validation"]["mode"] == "warn"

    lenient = resolve_executor_config(
        config, job_id=job.job_id, experiment_id="lenient"
    )
    assert any("below min_intra" in warning for warning in lenient.warnings)

    config["validation"]["mode"] = "error"
    with pytest.raises(CloverResolutionError, match="below min_intra"):
        resolve_executor_config(config, job_id=job.job_id, experiment_id="strict")


def test_two_patterns_on_the_same_well_are_reported_as_a_warning() -> None:
    """Documents real, validated behaviour rather than the behaviour I expected.

    The registered profile runs the spacing checks in ``warn`` mode, and the
    executor's duplicate-position rule applies within a clover, not between two
    clovers. Two patterns stacked on one well therefore RESOLVE, carrying an
    explicit warning, instead of being refused. Set ``validation.mode: error`` in
    the profile to make inter-clover crowding fatal.
    """
    job = _clover_job(
        clovers=[
            {"name": "one", "reference_well": "D6"},
            {"name": "two", "reference_well": "D6"},
        ]
    )
    plan = resolve_experiment_job(load_experiment_job_mapping(job))
    assert plan.minimum_inter_clover_distance_mm == 0.0
    assert any("approach to 0.00 mm" in warning for warning in plan.warnings)


# ── geometry stays where it belongs ───────────────────────────────────────────────

def test_the_agent_facing_schema_has_nowhere_to_put_a_coordinate() -> None:
    """An LLM must not be able to submit a droplet position, even by accident."""
    for attempt in (
        {"clovers": [{"name": "one", "reference_well": "D6", "d1": {"x_mm": 1.0}}]},
        {"clovers": [{"name": "one", "reference_well": "D6", "x_mm": 40.0}]},
        {"printing": {"droplet_volume_ul": 4.0, "dispense_height_mm": 3.0}},
        {"printing": {"droplet_volume_ul": 4.0, "air_gap_ul": 5.0}},
    ):
        with pytest.raises((CloverJobLoadError, ValidationError)):
            load_experiment_job_mapping(_clover_job(**attempt))


def test_resolution_derives_the_four_coordinates_from_half_offsets() -> None:
    job = load_experiment_job_mapping(
        _clover_job(
            **{
                "default_geometry": {"half_width_mm": 1.5, "half_height_mm": 1.5},
                "clovers": [{"name": "one", "reference_well": "D6"}],
            }
        )
    )
    plan = resolve_experiment_job(job)
    clover = plan.clovers[0]
    centre_x, centre_y = clover.center_x_mm, clover.center_y_mm
    offsets = {droplet.key: (droplet.offset_x_mm, droplet.offset_y_mm)
               for droplet in clover.droplets}
    assert offsets == {
        "d1": (-1.5, 1.5), "d2": (1.5, 1.5), "d3": (-1.5, -1.5), "d4": (1.5, -1.5),
    }
    for droplet in clover.droplets:
        assert droplet.x_mm == pytest.approx(centre_x + droplet.offset_x_mm)
        assert droplet.y_mm == pytest.approx(centre_y + droplet.offset_y_mm)
        assert droplet.z_mm == 6.5


def test_review_is_readable_without_the_yaml() -> None:
    plan = resolve_experiment_job(load_experiment_job_mapping(_clover_job()))
    review = render_clover_review(plan)
    for expected in ("SOURCE", "SUBSTRATE", "PRINTING", "CLOVER 1", "TOTALS",
                     "D1:", "D2:", "D3:", "D4:"):
        assert expected in review, expected


# ── one agent, two families ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Print four clover patterns at 2, 3, 4 and 5 mm separations.", "four_clover"),
        ("Make a coffee-ring overlap test at B5 with 5 uL drops.", "four_clover"),
        ("Prepare eight twofold dilutions and print them down column 1.", "standard"),
        ("Print one 5 uL drop of dye at A1 and H12.", "standard"),
        ("Print three replicate controls with stock dye.", "standard"),
    ],
)
def test_one_agent_routes_the_request_to_a_family(request_text: str, expected: str) -> None:
    from src.agents.printing_agent import select_printing_workflow_family

    assert select_printing_workflow_family(request_text).value == expected


def test_each_family_loads_only_its_own_skill() -> None:
    from src.printing.skills import select_printing_experiment_skills

    assert select_printing_experiment_skills("standard") == (
        "standard-printing-experiment",
    )
    assert select_printing_experiment_skills("four_clover") == (
        "four-clover-experiment",
    )
    with pytest.raises(KeyError):
        select_printing_experiment_skills("rings")


def test_the_single_agent_carries_both_tool_sets_and_no_python_writing_tool() -> None:
    from src.agents.printing_tools import PRINTING_EXPERIMENT_TOOLS

    names = {item.name for item in PRINTING_EXPERIMENT_TOOLS}
    assert {
        "list_standard_printing_experiment_capabilities",
        "create_standard_printing_experiment_config",
        "inspect_standard_printing_layout",
        "list_four_clover_experiment_capabilities",
        "create_four_clover_experiment_config",
        "preview_four_clover_experiment",
        "simulate_four_clover_experiment",
        "load_printing_experiment_template",
    } <= names
    # No tool may write files freely, write Python, or reach a robot.
    for forbidden in ("write_file", "edit_file", "run_python", "deploy", "execute"):
        assert not any(forbidden in name for name in names), forbidden


def test_the_template_tool_is_an_allowlist_not_filesystem_access() -> None:
    from src.agents.printing_tools import load_printing_experiment_template

    text = load_printing_experiment_template.invoke({"workflow_family": "four_clover"})
    assert "four-clover-experiment-job/v1" in text
    with pytest.raises(Exception):
        load_printing_experiment_template.invoke(
            {"workflow_family": "../../configs/experiments/01_printing_standard.yaml"}
        )


def test_generated_configurations_never_land_on_a_ground_truth_path() -> None:
    from src.agents.printing_tools import (
        FOUR_CLOVER_PROPOSAL_DIR,
        STANDARD_EXPERIMENT_PROPOSAL_DIR,
    )

    ground_truth_dir = REPO / "configs" / "experiments"
    for directory in (FOUR_CLOVER_PROPOSAL_DIR, STANDARD_EXPERIMENT_PROPOSAL_DIR):
        assert Path(directory).resolve() != ground_truth_dir.resolve()
        assert Path(directory).name == "generated"


# ── the deterministic executors are untouched by the AI layer ─────────────────────

def test_the_executors_do_not_import_the_agent_layer() -> None:
    for path in (STANDARD_EXECUTOR, CLOVER_EXECUTOR):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("src.agents", "langchain", "anthropic", "openai"):
            assert forbidden not in text, f"{path.name} references {forbidden}"


def test_the_clover_resolver_owns_geometry_and_the_schema_does_not() -> None:
    """Coordinate arithmetic must live in the frozen executor, nowhere else."""
    schema_text = (REPO / "src" / "printing" / "clover" / "schemas.py").read_text(
        encoding="utf-8"
    )
    assert "math." not in schema_text
    resolver_text = (REPO / "src" / "printing" / "clover" / "resolver.py").read_text(
        encoding="utf-8"
    )
    # The resolver may call the engine, but must not recompute droplet offsets.
    assert "_resolve_clovers" in resolver_text
    assert "half_width" not in resolver_text
