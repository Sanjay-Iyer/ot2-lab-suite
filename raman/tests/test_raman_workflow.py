"""Focused scientific and end-to-end tests for the YAML-only Raman workflow."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

from raman_lib import io_utils, preprocessing
from raman_lib.analysis_workflow import (
    _group_partitions,
    characterize_target_peak,
    normalize_spectrum,
    process_spectrum,
    resolve_spectra,
    run_analysis,
)
from raman_lib.workflow_config import WorkflowConfigError, load_analysis_config

RAMAN_ROOT = Path(__file__).resolve().parents[1]


def synthetic_spectrum(
    *,
    seed: int = 7,
    center: float = 1080.0,
    target_amplitude: float = 260.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return x, signal, and known curved baseline for a Raman-like spectrum."""
    rng = np.random.default_rng(seed)
    x = np.arange(400.0, 1801.0, 1.0)
    baseline = 800.0 + 0.06 * (x - 400.0) + 0.00035 * (x - 1050.0) ** 2
    target = target_amplitude * np.exp(-0.5 * ((x - center) / 7.0) ** 2)
    other = 170.0 * np.exp(-0.5 * ((x - 1450.0) / 10.0) ** 2)
    noise = rng.normal(0.0, 5.0, x.size)
    return x, baseline + target + other + noise, baseline


def minimal_config(tmp_path: Path, spectra: list[dict] | None = None) -> Path:
    config = {
        "analysis": {
            "name": "test",
            "input_root": "data",
            "output_root": "out",
        },
        "spectra": spectra or [{"file": "sample.csv", "include": True}],
        "smoothing": {"enabled": False},
        "plots": {
            "overlay": {"enabled": False},
            "groups": {"enabled": False},
            "output": {"dpi": 90, "formats": ["png"]},
        },
    }
    path = tmp_path / "analysis.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def target_config() -> dict:
    return {
        "name": "band_1080",
        "expected_position_cm1": 1080.0,
        "search_window_cm1": [1060.0, 1100.0],
        "integration_window_cm1": [1065.0, 1095.0],
        "minimum_snr": 3.0,
        "minimum_prominence": None,
        "minimum_points_for_width": 5,
    }


def test_configuration_loading_and_validation(tmp_path: Path) -> None:
    path = minimal_config(tmp_path)
    cfg = load_analysis_config(path)
    assert cfg["normalization"]["method"] == "target_peak"
    bad = yaml.safe_load(path.read_text(encoding="utf-8"))
    bad["smoothing"] = {"enabled": True, "window_length": 6}
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(WorkflowConfigError, match="odd integer"):
        load_analysis_config(path)
    bad = {
        "analysis": {"name": "x", "input_root": "data", "output_root": "out"},
        "spectra": [{"file": "a.csv"}],
        "plots": {"overaly": {"enabled": True}},
    }
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(WorkflowConfigError, match="Unknown configuration key"):
        load_analysis_config(path)


def test_directory_discovery_filters_metadata_and_skips_manifest(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    filenames = [
        "0001__sample-bp__column-A__row-1.csv",
        "0002__sample-bp__column-B__row-2.csv",
        "0003__sample-bp__column-C__row-3.csv",
        "0004__sample-bp__column-D__row-4.csv",
        "0005__sample-bp__column-E__row-4.csv",
        "0006__sample-control__column-A__row-8.csv",
        "rename_manifest.csv",
    ]
    for filename in filenames:
        (data / filename).touch()
    config = {
        "analysis": {
            "name": "discovery",
            "input_root": "data",
            "output_root": "out",
        },
        "discovery": {
            "enabled": True,
            "file_glob": "*.csv",
            "canonical_filenames_only": True,
            "include": {
                "columns": ["A", "B", "C"],
                "rows": [1, 2, 3, 4],
                "sample_types": ["BP"],
            },
            "exclude": {
                "columns": ["d"],
                "rows": [],
                "sample_types": [],
            },
        },
        "spectra": [],
    }
    path = tmp_path / "discovery.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    cfg = load_analysis_config(path)

    resolved, report = resolve_spectra(cfg, data)

    assert [item["file"] for item in resolved] == filenames[:3]
    assert all(item["include_in_groups"] for item in resolved)
    assert report["matched_files"] == 7
    assert report["selected_files"] == 3
    assert report["filtered_out"] == [filenames[3], filenames[4], filenames[5]]
    assert report["skipped_noncanonical"] == ["rename_manifest.csv"]


def test_discovery_allows_explicit_per_file_override(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    filename = "0001__sample-bp__column-A__row-1.csv"
    (data / filename).touch()
    config = {
        "analysis": {
            "name": "override",
            "input_root": "data",
            "output_root": "out",
        },
        "discovery": {"enabled": True},
        "spectra": [
            {
                "file": filename,
                "label": "Custom A1",
                "include_in_overlay": True,
            }
        ],
    }
    path = tmp_path / "override.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    cfg = load_analysis_config(path)

    resolved, report = resolve_spectra(cfg, data)

    assert len(resolved) == 1
    assert resolved[0]["label"] == "Custom A1"
    assert resolved[0]["include_in_overlay"]
    assert report["selected_files"] == 1
    assert report["selected"][0]["source"] == "discovered+override"


def test_explicit_entry_can_reinclude_filtered_file_without_report_conflict(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    filename = "0001__sample-bp__column-D__row-1.csv"
    (data / filename).touch()
    config = {
        "analysis": {
            "name": "reinclude",
            "input_root": "data",
            "output_root": "out",
        },
        "discovery": {
            "enabled": True,
            "include": {"columns": ["A", "B", "C"]},
        },
        "spectra": [{"file": filename, "include": True}],
    }
    path = tmp_path / "reinclude.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    cfg = load_analysis_config(path)

    resolved, report = resolve_spectra(cfg, data)

    assert [item["file"] for item in resolved] == [filename]
    assert report["filtered_out"] == []
    assert report["explicitly_reincluded"] == [filename]
    assert report["selected"] == [
        {"file": filename, "source": "explicit-reinclude"}
    ]


def test_duplicate_equivalent_explicit_paths_are_rejected(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    filename = "0001__sample-bp__column-A__row-1.csv"
    (data / filename).touch()
    config = {
        "analysis": {
            "name": "duplicates",
            "input_root": "data",
            "output_root": "out",
        },
        "discovery": {"enabled": True},
        "spectra": [
            {"file": filename, "label": "first"},
            {"file": f"unused/../{filename}", "label": "second"},
        ],
    }
    path = tmp_path / "duplicates.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    cfg = load_analysis_config(path)

    with pytest.raises(RuntimeError, match="Duplicate explicit spectrum paths"):
        resolve_spectra(cfg, data)


def test_discovery_fails_clearly_when_filters_select_nothing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "0001__sample-bp__column-A__row-1.csv").touch()
    config = {
        "analysis": {
            "name": "empty",
            "input_root": "data",
            "output_root": "out",
        },
        "discovery": {
            "enabled": True,
            "include": {"columns": ["Z"]},
        },
        "spectra": [],
    }
    path = tmp_path / "empty.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    cfg = load_analysis_config(path)

    with pytest.raises(RuntimeError, match="No spectra matched"):
        resolve_spectra(cfg, data)


def test_nonrecursive_discovery_rejects_recursive_glob(tmp_path: Path) -> None:
    config = {
        "analysis": {
            "name": "bad-glob",
            "input_root": "data",
            "output_root": "out",
        },
        "discovery": {
            "enabled": True,
            "recursive": False,
            "file_glob": "**/*.csv",
        },
        "spectra": [],
    }
    path = tmp_path / "bad-glob.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowConfigError, match="recursive is false"):
        load_analysis_config(path)

    config["discovery"] = {
        "enabled": True,
        "recursive": True,
        "file_glob": "C:*.csv",
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(WorkflowConfigError, match="relative pattern"):
        load_analysis_config(path)


def test_recursive_discovery_controls_nested_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    nested = data / "nested"
    nested.mkdir(parents=True)
    top = "0001__sample-bp__column-A__row-1.csv"
    child = "0002__sample-bp__column-A__row-2.csv"
    (data / top).touch()
    (nested / child).touch()
    config = {
        "analysis": {
            "name": "recursion",
            "input_root": "data",
            "output_root": "out",
        },
        "discovery": {
            "enabled": True,
            "recursive": False,
        },
        "spectra": [],
    }
    path = tmp_path / "recursion.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    flat_cfg = load_analysis_config(path)
    flat, _ = resolve_spectra(flat_cfg, data)
    assert [item["file"] for item in flat] == [top]

    config["discovery"]["recursive"] = True
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    recursive_cfg = load_analysis_config(path)
    recursive, _ = resolve_spectra(recursive_cfg, data)
    assert [item["file"] for item in recursive] == [top, f"nested/{child}"]


def test_group_filters_and_partitions_are_case_insensitive() -> None:
    results = [
        SimpleNamespace(
            spec={"include_in_groups": True},
            metadata={"column": value, "row": "1", "sample_type": "BP"},
        )
        for value in ("A", "a")
    ]
    selection = {
        "name": "case",
        "group_by": "column",
        "columns": ["A"],
        "rows": [1],
        "sample_types": ["bp"],
    }

    partitions = _group_partitions(results, selection)

    assert list(partitions) == ["case__column_A"]
    assert len(partitions["case__column_A"]) == 2


def test_missing_file_and_named_column_handling(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        io_utils.load_spectrum(tmp_path / "missing.csv", {})

    source = tmp_path / "header.csv"
    source.write_text(
        "shift_cm1,intensity,unused\n1081,5,0\n1080,4,0\n1080,6,0\n"
        "1082,4,0\n1083,3,0\n1084,2,0\n",
        encoding="utf-8",
    )
    x, y = io_utils.load_spectrum(
        source,
        {
            "has_header": True,
            "raman_shift_column": "shift_cm1",
            "intensity_column": "intensity",
        },
    )
    assert np.all(np.diff(x) > 0)
    assert x.size == 5
    assert y[x.tolist().index(1080.0)] == pytest.approx(5.0)
    with pytest.raises(ValueError, match="Available columns"):
        io_utils.load_spectrum(
            source,
            {
                "has_header": True,
                "raman_shift_column": "wavelength",
                "intensity_column": "intensity",
            },
        )


def test_arpls_flattens_curved_baseline_without_suppressing_target() -> None:
    x, y, known_baseline = synthetic_spectrum()
    estimated = preprocessing.baseline_arpls(y, lam=1.0e5, ratio=1.0e-6, niter=50)
    corrected = y - estimated
    quiet = ((x > 500) & (x < 950)) | ((x > 1200) & (x < 1350))
    assert np.median(np.abs(corrected[quiet])) < 20.0
    assert np.max(corrected[(x >= 1060) & (x <= 1100)]) > 200.0
    assert np.median(np.abs(estimated[quiet] - known_baseline[quiet])) < 20.0


def test_target_peak_metrics_and_normalization() -> None:
    x, raw, _ = synthetic_spectrum(center=1084.0)
    baseline = preprocessing.baseline_arpls(raw, lam=1.0e5, ratio=1.0e-6, niter=50)
    corrected = raw - baseline
    metrics, warnings = characterize_target_peak(
        x,
        raw,
        baseline,
        corrected,
        target_config(),
        {"method": "mad_derivative", "region_cm1": None},
    )
    assert all("prominence bases" in warning for warning in warnings)
    assert metrics["peak_valid"]
    assert metrics["detected_position_cm1"] == pytest.approx(1084.0, abs=2.0)
    assert metrics["peak_shift_cm1"] == pytest.approx(4.0, abs=2.0)
    assert metrics["local_peak_area"] > 0
    assert metrics["signal_to_noise_ratio"] > 3
    assert metrics["peak_prominence"] > 0
    assert metrics["width_at_half_prominence_cm1"] is not None
    assert metrics["fwhm_cm1"] is None

    scaled, scale, valid, norm_warnings = normalize_spectrum(
        x,
        corrected,
        metrics,
        {"method": "target_peak", "target_value": 1.0, "allow_invalid_target": False},
    )
    assert valid
    assert norm_warnings == []
    assert scale == pytest.approx(metrics["baseline_corrected_peak_height"])
    assert scaled[metrics["detected_index"]] == pytest.approx(1.0)


def test_no_target_peak_is_not_normalized() -> None:
    x, raw, _ = synthetic_spectrum(target_amplitude=0.0)
    baseline = preprocessing.baseline_arpls(raw, lam=1.0e5, ratio=1.0e-6, niter=50)
    corrected = raw - baseline
    target = target_config()
    target["minimum_snr"] = 10.0
    metrics, warnings = characterize_target_peak(
        x,
        raw,
        baseline,
        corrected,
        target,
        {"method": "mad_derivative", "region_cm1": None},
    )
    assert not metrics["peak_valid"]
    assert warnings
    scaled, scale, valid, norm_warnings = normalize_spectrum(
        x,
        corrected,
        metrics,
        {"method": "target_peak", "target_value": 1.0, "allow_invalid_target": False},
    )
    assert not valid
    assert scale is None
    assert np.all(np.isnan(scaled))
    assert norm_warnings


def test_single_point_spike_is_an_unvalidated_candidate() -> None:
    x = np.arange(1000.0, 1161.0, 1.0)
    raw = np.zeros_like(x)
    raw[np.where(x == 1080.0)[0][0]] = 100.0
    metrics, warnings = characterize_target_peak(
        x,
        raw,
        np.zeros_like(raw),
        raw,
        {
            **target_config(),
            "require_resolved_width": True,
            "minimum_width_cm1": 3.0,
            "maximum_width_cm1": 40.0,
            "maximum_shift_cm1": 20.0,
            "minimum_prominence_to_noise": None,
        },
        {"method": "mad_derivative", "region_cm1": None},
    )
    assert metrics["candidate_found"]
    assert not metrics["peak_valid"]
    assert metrics["local_peak_area"] is None
    assert metrics["width_at_half_prominence_cm1"] is None
    assert any("resolved peak width" in warning for warning in warnings)


def test_checked_in_randomized_template_is_not_normalized() -> None:
    cfg = load_analysis_config(RAMAN_ROOT / "configs" / "raman_analysis.yaml")
    spec = {"file": "Randomized_Scan_00649.csv", "include": True}
    result = process_spectrum(RAMAN_ROOT / "raw" / spec["file"], spec, cfg)
    assert result.metrics["candidate_found"]
    assert not result.metrics["peak_valid"]
    assert not result.normalization_valid
    assert np.all(np.isnan(result.scaled))


def test_processing_preserves_all_scientific_arrays(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    x, y, _ = synthetic_spectrum()
    path = data / "0001__sample-bp__column-A__row-1.csv"
    np.savetxt(path, np.column_stack([x, y]), delimiter=",")
    cfg = load_analysis_config(
        minimal_config(tmp_path, [{"file": path.name, "include": True}])
    )
    result = process_spectrum(path, cfg["spectra"][0], cfg)
    assert result.metadata["sample_type"] == "bp"
    assert result.metadata["spot"] == "A1"
    assert result.normalization_valid
    assert all(
        array.shape == x.shape
        for array in (
            result.raw,
            result.smoothed,
            result.baseline,
            result.corrected,
            result.scaled,
        )
    )


def test_end_to_end_directory_discovery_records_resolved_selection(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    for column, sequence in (("A", 1), ("D", 2)):
        x, y, _ = synthetic_spectrum(seed=sequence + 20)
        filename = (
            f"{sequence:04d}__sample-bp__column-{column}__row-1.csv"
        )
        np.savetxt(data / filename, np.column_stack([x, y]), delimiter=",")
    config = {
        "analysis": {
            "name": "discovered",
            "input_root": "data",
            "output_root": "out",
        },
        "discovery": {
            "enabled": True,
            "include": {
                "columns": ["A"],
                "sample_types": ["bp"],
            },
        },
        "spectra": [],
        "plots": {
            "individual": {"enabled": False},
            "overlay": {"enabled": False},
            "groups": {"enabled": False},
            "diagnostics": {"enabled": False},
            "output": {"dpi": 90, "formats": ["png"]},
        },
    }
    config_path = tmp_path / "discovered.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    cfg = load_analysis_config(config_path)

    run_dir, summary = run_analysis(cfg, tmp_path)

    assert summary["n_selected"] == 1
    assert summary["n_processed"] == 1
    assert summary["discovery"]["selected"][0]["source"] == "discovered"
    assert summary["discovery"]["filtered_out"] == [
        "0002__sample-bp__column-D__row-1.csv"
    ]
    resolved = yaml.safe_load(
        (run_dir / "resolved_config.yaml").read_text(encoding="utf-8")
    )
    assert [item["file"] for item in resolved["spectra"]] == [
        "0001__sample-bp__column-A__row-1.csv"
    ]


def test_end_to_end_overlay_groups_and_expected_outputs(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    spectra = []
    for row, center in ((1, 1079.0), (2, 1085.0)):
        x, y, _ = synthetic_spectrum(seed=row + 10, center=center)
        filename = f"000{row}__sample-bp__column-A__row-{row}.csv"
        np.savetxt(data / filename, np.column_stack([x, y]), delimiter=",")
        spectra.append(
            {
                "file": filename,
                "label": f"BP A{row}",
                "include": True,
                "include_in_overlay": True,
                "include_in_groups": True,
            }
        )
    config_path = minimal_config(tmp_path, spectra)
    raw_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw_cfg["plots"]["overlay"] = {
        "enabled": True,
        "title": "Two spectra",
        "x_range_cm1": [1000.0, 1160.0],
        "y_range": None,
        "vertical_offset": 0.0,
        "mark_expected_peak": True,
        "mark_detected_peaks": False,
    }
    raw_cfg["plots"]["groups"] = {
        "enabled": True,
        "x_range_cm1": [1000.0, 1160.0],
        "y_range": None,
        "vertical_offset": 0.0,
        "selections": [
            {
                "name": "column_A_rows_1_2",
                "group_by": "column",
                "columns": ["A"],
                "rows": [1, 2],
            }
        ],
    }
    config_path.write_text(yaml.safe_dump(raw_cfg, sort_keys=False), encoding="utf-8")
    cfg = load_analysis_config(config_path)
    run_dir, summary = run_analysis(cfg, tmp_path)

    assert summary["n_processed"] == 2
    assert summary["n_failed"] == 0
    assert (run_dir / "config_snapshot.yaml").is_file()
    assert (run_dir / "resolved_config.yaml").is_file()
    assert (run_dir / "run_summary.json").is_file()
    assert (run_dir / "peak_metrics.csv").is_file()
    assert (run_dir / "logs" / "analysis.log").is_file()
    assert len(list((run_dir / "processed").glob("*_processed.csv"))) == 2
    assert len(list((run_dir / "plots" / "by_type" / "individual").glob("*.png"))) == 2
    assert len(list((run_dir / "plots" / "by_peak" / "band_1080").glob("*.png"))) == 2
    assert len(list((run_dir / "plots" / "overlay").glob("*.png"))) == 1
    assert len(list((run_dir / "plots" / "groups").glob("*.png"))) == 1

    overlay = pd.read_csv(run_dir / "overlay_data" / "selected_spectra.csv")
    for label in ("BP A1", "BP A2"):
        processed = pd.read_csv(
            run_dir / "processed" / f"{label.replace(' ', '_')}_processed.csv"
        )
        plotted = overlay.loc[overlay["label"] == label, "scaled_intensity"].to_numpy()
        np.testing.assert_allclose(
            plotted,
            processed["scaled_intensity"].to_numpy(),
        )
        assert processed.columns.tolist() == [
            "raman_shift_cm1",
            "raw_intensity",
            "smoothed_intensity",
            "estimated_baseline",
            "baseline_corrected_intensity",
            "scaled_intensity",
            "in_target_region",
        ]
    metrics = pd.read_csv(run_dir / "peak_metrics.csv")
    assert metrics["normalization_valid"].all()
