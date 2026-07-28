"""Metadata-rich Raman filenames and safe bulk filename migration."""
from __future__ import annotations

import csv
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CANONICAL_FILENAME_RE = re.compile(
    r"^(?P<sequence>\d+)__sample-(?P<sample_type>.+?)"
    r"__column-(?P<column>[^_]+)__row-(?P<row>[^_]+)$",
    re.IGNORECASE,
)
CANONICAL_FILENAME_PATTERN = (
    "{sequence}__sample-{sample_type}__column-{column}__row-{row}"
)


class RenameError(ValueError):
    """Raised when a bulk filename migration cannot be completed safely."""


@dataclass(frozen=True)
class RenameItem:
    """One preflighted filename change."""

    sequence: int
    source: Path
    destination: Path
    sample_type: str
    column: str
    row: str
    spot: str
    status: str = "planned"

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "source_filename": self.source.name,
            "source_path": str(self.source),
            "new_filename": self.destination.name,
            "destination_path": str(self.destination),
            "sample_type": self.sample_type,
            "column": self.column,
            "row": self.row,
            "spot": self.spot,
            "status": self.status,
        }


def clean_token(value: Any) -> str:
    """Return a filesystem-safe metadata token without ambiguous separators."""
    text = str(value).strip().replace(" ", "-")
    text = re.sub(r"[^A-Za-z0-9.+-]+", "-", text)
    return text.strip("-") or "unknown"


def slug(value: Any) -> str:
    """Return the shared filesystem-safe output identifier."""
    keep = "-_."
    return (
        "".join(
            character if (character.isalnum() or character in keep) else "_"
            for character in str(value)
        ).strip("_")
        or "x"
    )


def parse_filename_metadata(path: str | Path) -> dict[str, Any]:
    """Extract metadata from the canonical Raman filename, if present."""
    source = Path(path)
    match = CANONICAL_FILENAME_RE.match(source.stem)
    if not match:
        return {
            "sequence": None,
            "sample_type": None,
            "column": None,
            "row": None,
            "spot": None,
            "canonical_filename": False,
        }
    values = match.groupdict()
    column = values["column"]
    row = values["row"]
    return {
        "sequence": int(values["sequence"]),
        "sample_type": values["sample_type"],
        "column": column,
        "row": row,
        "spot": f"{column}{row}",
        "canonical_filename": True,
    }


def spectrum_metadata(path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Merge filename metadata with explicit YAML fields (YAML takes priority)."""
    metadata = parse_filename_metadata(path)
    for key in ("sample_type", "column", "row"):
        if spec.get(key) not in (None, ""):
            metadata[key] = str(spec[key])
    metadata["spot"] = (
        f"{metadata['column']}{metadata['row']}"
        if metadata.get("column") is not None and metadata.get("row") is not None
        else None
    )
    metadata["label"] = str(spec.get("label") or path.stem)
    return metadata


def display_title(metadata: dict[str, Any]) -> str:
    """Build a concise title containing every known sample/position field."""
    parts = [str(metadata["label"])]
    if metadata.get("sample_type"):
        parts.append(f"sample: {metadata['sample_type']}")
    if metadata.get("spot"):
        parts.append(f"position: {metadata['spot']}")
    return " | ".join(parts)


def _scan_number(path: Path, pattern: str) -> int | None:
    match = re.search(pattern, path.stem)
    return int(match.group(1)) if match else None


def _ordered_files(files: list[Path], rename: dict[str, Any]) -> list[Path]:
    if rename["sort_by"] == "filename":
        return sorted(files, key=lambda path: path.name.casefold())
    missing = [path.name for path in files if _scan_number(path, rename["scan_regex"]) is None]
    if missing:
        raise RenameError(
            "sort_by=scan_number but these files do not match rename.scan_regex: "
            + ", ".join(missing)
        )
    return sorted(
        files,
        key=lambda path: (_scan_number(path, rename["scan_regex"]), path.name.casefold()),
    )


def _position(index: int, columns: list[Any], rows: list[Any], order: str) -> tuple[str, str] | None:
    capacity = len(columns) * len(rows)
    if index < 0 or index >= capacity:
        return None
    if order == "column_major":
        column_index, row_index = divmod(index, len(rows))
    else:
        row_index, column_index = divmod(index, len(columns))
    return str(columns[column_index]), str(rows[row_index])


def plan_renames(
    input_root: Path,
    output_root: Path,
    rename: dict[str, Any],
) -> list[RenameItem]:
    """Preflight the complete rename batch before touching any files."""
    if not input_root.is_dir():
        raise RenameError(f"Rename input directory not found: {input_root}")
    if input_root.resolve() == output_root.resolve():
        raise RenameError("rename.output_root must differ from rename.input_root.")
    try:
        output_root.resolve().relative_to(input_root.resolve())
        output_nested = True
    except ValueError:
        output_nested = False
    if output_nested and "**" in rename["file_glob"]:
        raise RenameError(
            "Recursive file_glob is not allowed when output_root is inside input_root."
        )
    files = [path for path in input_root.glob(rename["file_glob"]) if path.is_file()]
    files = _ordered_files(files, rename)
    if not files:
        raise RenameError(
            f"No files matching {rename['file_glob']!r} were found in {input_root}."
        )

    sample_type = clean_token(rename["sample_type"])
    start = int(rename["start_sequence"])
    width = int(rename["zero_padding"])
    scan_numbers = [
        _scan_number(path, rename["scan_regex"])
        for path in files
    ] if rename["position_mapping"] == "scan_number_offset" else []
    if scan_numbers:
        numeric_scans = [int(value) for value in scan_numbers if value is not None]
        if len(numeric_scans) != len(set(numeric_scans)):
            raise RenameError("Duplicate scan numbers are not allowed.")
        start_scan = int(rename["start_scan_number"])
        position_indices = [scan - start_scan for scan in numeric_scans]
        if not rename["allow_scan_gaps"]:
            expected_indices = list(range(len(position_indices)))
            if position_indices != expected_indices:
                raise RenameError(
                    "Scan numbers contain a gap or do not begin at "
                    f"start_scan_number={start_scan}. Set allow_scan_gaps: true "
                    "only when preserving intentional empty positions."
                )
    else:
        position_indices = list(range(len(files)))
    items: list[RenameItem] = []
    for offset, source in enumerate(files):
        position_index = position_indices[offset]
        position = _position(
            position_index,
            rename["columns"],
            rename["rows"],
            rename["order"],
        )
        if position is None:
            if rename["on_unmapped"] == "skip":
                continue
            raise RenameError(
                f"{len(files)} input files exceed the configured plate capacity "
                f"of {len(rename['columns']) * len(rename['rows'])}."
            )
        column, row = map(clean_token, position)
        sequence_number = start + offset
        fields = {
            "sequence": f"{sequence_number:0{width}d}",
            "sample_type": sample_type,
            "column": column,
            "row": row,
            "spot": f"{column}{row}",
            "original_stem": clean_token(source.stem),
        }
        try:
            stem = rename["filename_pattern"].format(**fields)
        except (KeyError, ValueError) as exc:
            raise RenameError(f"Invalid rename.filename_pattern: {exc}") from exc
        destination = output_root / f"{stem}{source.suffix.lower()}"
        if destination.resolve().parent != output_root.resolve():
            raise RenameError(
                "rename.filename_pattern must create a filename, not a path "
                f"outside output_root: {destination}"
            )
        items.append(
            RenameItem(
                sequence=sequence_number,
                source=source,
                destination=destination,
                sample_type=sample_type,
                column=column,
                row=row,
                spot=f"{column}{row}",
            )
        )

    destinations = [item.destination.resolve() for item in items]
    if len(destinations) != len(set(destinations)):
        raise RenameError("The configured filename pattern creates duplicate destinations.")
    if not rename["overwrite"]:
        collisions = [str(item.destination) for item in items if item.destination.exists()]
        if collisions:
            raise RenameError(
                "Destination files already exist and overwrite is false: "
                + ", ".join(collisions)
            )
    return items


def execute_renames(
    items: list[RenameItem],
    rename: dict[str, Any],
    *,
    progress_manifest: Path | None = None,
) -> list[RenameItem]:
    """Copy or move every preflighted item, or return a dry-run plan."""
    if rename["dry_run"]:
        return items
    progress = list(items)
    for index, item in enumerate(items):
        temporary = item.destination.with_name(item.destination.name + ".partial")
        try:
            item.destination.parent.mkdir(parents=True, exist_ok=True)
            if temporary.exists():
                temporary.unlink()
            shutil.copy2(item.source, temporary)
            if temporary.stat().st_size != item.source.stat().st_size:
                temporary.unlink(missing_ok=True)
                raise RenameError(f"Size verification failed for {item.source}.")
            temporary.replace(item.destination)
            if rename["operation"] == "move":
                item.source.unlink()
            status = "copied" if rename["operation"] == "copy" else "moved"
            progress[index] = RenameItem(
                    sequence=item.sequence,
                    source=item.source,
                    destination=item.destination,
                    sample_type=item.sample_type,
                    column=item.column,
                    row=item.row,
                    spot=item.spot,
                    status=status,
                )
        except Exception as exc:
            progress[index] = RenameItem(
                sequence=item.sequence,
                source=item.source,
                destination=item.destination,
                sample_type=item.sample_type,
                column=item.column,
                row=item.row,
                spot=item.spot,
                status=f"failed: {type(exc).__name__}: {exc}",
            )
            if progress_manifest is not None:
                write_manifest(
                    progress,
                    progress_manifest.parent,
                    progress_manifest.name,
                    dry_run=False,
                )
            raise
        if progress_manifest is not None:
            write_manifest(
                progress,
                progress_manifest.parent,
                progress_manifest.name,
                dry_run=False,
            )
    return progress


def write_manifest(
    items: list[RenameItem],
    output_root: Path,
    manifest_file: str,
    *,
    dry_run: bool,
) -> Path:
    """Write the old-to-new mapping, including dry-run status."""
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / manifest_file
    if manifest_path.resolve().parent != output_root.resolve():
        raise RenameError("rename.manifest_file must be a filename inside output_root.")
    rows = []
    for item in items:
        row = item.as_dict()
        if dry_run:
            row["status"] = "dry_run"
        rows.append(row)
    if not rows:
        raise RenameError("No rename entries were available for the manifest.")
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return manifest_path
