"""Tests for deterministic, non-destructive Raman filename migration."""
from __future__ import annotations

from pathlib import Path

import pytest

from raman_lib.naming import (
    RenameError,
    execute_renames,
    parse_filename_metadata,
    plan_renames,
    write_manifest,
)


def rename_settings() -> dict:
    return {
        "file_glob": "*.csv",
        "operation": "copy",
        "dry_run": False,
        "overwrite": False,
        "sort_by": "scan_number",
        "scan_regex": r"(?i)scan[ _-]?0*(\d+)",
        "start_sequence": 1,
        "zero_padding": 4,
        "sample_type": "bp",
        "columns": ["A"],
        "rows": [1, 2],
        "order": "column_major",
        "position_mapping": "scan_number_offset",
        "start_scan_number": 649,
        "allow_scan_gaps": False,
        "filename_pattern": (
            "{sequence}__sample-{sample_type}__column-{column}__row-{row}"
        ),
        "on_unmapped": "error",
        "manifest_file": "rename_manifest.csv",
    }


def test_sequential_rename_copy_and_metadata_parsing(tmp_path: Path) -> None:
    source = tmp_path / "old"
    destination = tmp_path / "new"
    source.mkdir()
    (source / "Randomized_Scan_00650.csv").write_text("1,2\n", encoding="utf-8")
    (source / "Randomized_Scan_00649.csv").write_text("1,3\n", encoding="utf-8")

    settings = rename_settings()
    planned = plan_renames(source, destination, settings)
    assert [item.source.name for item in planned] == [
        "Randomized_Scan_00649.csv",
        "Randomized_Scan_00650.csv",
    ]
    completed = execute_renames(planned, settings)
    manifest = write_manifest(
        completed,
        destination,
        settings["manifest_file"],
        dry_run=False,
    )
    assert manifest.is_file()
    assert len(list(source.glob("*.csv"))) == 2
    outputs = sorted(destination.glob("*.csv"))
    assert [path.name for path in outputs] == [
        "0001__sample-bp__column-A__row-1.csv",
        "0002__sample-bp__column-A__row-2.csv",
        "rename_manifest.csv",
    ]
    metadata = parse_filename_metadata(outputs[0])
    assert metadata["sequence"] == 1
    assert metadata["sample_type"] == "bp"
    assert metadata["spot"] == "A1"


def test_scan_gap_preserves_position_or_fails_explicitly(tmp_path: Path) -> None:
    source = tmp_path / "old"
    destination = tmp_path / "new"
    source.mkdir()
    (source / "Scan_00649.csv").write_text("1,2\n", encoding="utf-8")
    (source / "Scan_00651.csv").write_text("1,3\n", encoding="utf-8")
    settings = rename_settings()
    settings["rows"] = [1, 2, 3]
    with pytest.raises(RenameError, match="gap"):
        plan_renames(source, destination, settings)
    settings["allow_scan_gaps"] = True
    planned = plan_renames(source, destination, settings)
    assert [item.spot for item in planned] == ["A1", "A3"]


def test_duplicate_scan_numbers_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "old"
    source.mkdir()
    (source / "Scan_00649_first.csv").write_text("1,2\n", encoding="utf-8")
    (source / "Scan_00649_second.csv").write_text("1,3\n", encoding="utf-8")
    settings = rename_settings()
    with pytest.raises(RenameError, match="Duplicate scan"):
        plan_renames(source, tmp_path / "new", settings)
