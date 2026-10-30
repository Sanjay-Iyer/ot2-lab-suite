"""Semantic patch operations: the router says only what changes; Python applies it, generically.

    PlanOperation(op="set", path="print.replicates", value=5)          "make that 5 replicates"
    PlanOperation(op="set", path="print.source", value=["dilution:2"]) "actually use the second dilution"
    PlanOperation(op="append", path="dilution.factors", value=[20])    "add a 20x"
    PlanOperation(op="remove", path="print.paper_rows")                "print on the default rows again"
    PlanOperation(op="set", path="print.source", value={"count": 4}, params={"source": "B11"})
                                                                       "actually make B11 four prints" (that source only)

Operations: set, remove (a nullable field back to its default, or items out of a list), append (items onto a list),
and the relative operations whose arithmetic Python owns (scale, add, scale_each, set_count, none). A path is an
editable field (model.EDITABLE_FIELDS); the router's older selection names (rows, paper_rows, paper_columns, print_map,
drops_at) are accepted and become the same fields.

EDIT SEMANTICS. The router also says how a request relates to what the plan already has, per scope (printing,
dilution, plan): add (keep everything, add or update the items named), modify (change only the items named; the rest
stays exactly as it is) or replace (the items named are the whole new selection of that scope). Python reads no words
to decide this: it executes the mode the router stamped on each operation ("edit_mode"), deterministically
(_ScopeEdit). An operation without a mode keeps the plain meaning of its op (set replaces the field's value).

apply_operations() applies each operation on its own: one that cannot be applied (a well that does not exist, a
lab-owned field, a stale "from 2 drops") is reported in `failed` with its reason and the others still apply. Whether
the result may become the plan is decided afterwards, as a whole, by the hard invariants (validation.HARD_CODES) - a
deck collision or a drop over the P20 limit keeps the whole edit out. Nothing here reads the scientist's words.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from src.agents.dye_demo.model import (
    EDITABLE_FIELDS,
    FieldError,
    canonicalize_path,
    field_label,
    fmt_factor,
    get_path,
    lab_owned_reason,
    normalize_source_map,
    paper_position,
    resolve_path,
    set_path,
)

OPERATIONS = frozenset({"set", "remove", "append", "scale", "add", "scale_each", "set_count", "none"})
# fields whose value is a list: remove and append work on their items
LIST_FIELDS = frozenset({"dilution.factors", "dilution.rows", "print.paper_rows", "print.columns", "print.source",
                         "print.droplet_volume_ul"})
# fields that may be cleared back to "not set" (their default)
NULLABLE_FIELDS = frozenset({"dilution.prepared_volume_ul", "materials.sample.label", "materials.solvent.label",
                             "dilution.rows", "print.source_map", "print.paper_rows", "print.columns", "print.source",
                             "print.drops_at"})
# where the samples print, said semantically; a dictated print map names every position itself
PLACEMENT_FIELDS = frozenset({"print.source", "print.paper_rows", "print.columns", "print.paper_start_column",
                              "print.replicates"})
_SELECTIONS = {"rows", "paper_rows", "paper_columns", "print_map", "drops_at"}

ADD, MODIFY, REPLACE = "add", "modify", "replace"
EDIT_MODES = frozenset({ADD, MODIFY, REPLACE})
PRINTING, DILUTION, PLAN = "printing", "dilution", "plan"
SCOPES = (PRINTING, DILUTION, PLAN)
_SCOPE_ALIASES = {"print": PRINTING, "prints": PRINTING, "printing": PRINTING, "dilution": DILUTION,
                  "dilutions": DILUTION, "series": DILUTION, "plan": PLAN, "deck": PLAN, "tips": PLAN, "other": PLAN}


def scope_of(path: str) -> str:
    """The scope an operation edits, from its path alone: printing (print.*, paper rows/columns, print map, drops),
    dilution (dilution.*, mixing.*, plate rows) or plan (deck, tips, materials, liquid handling)."""
    name = str(path).strip().lower()
    if name.startswith("print.") or name in {"paper_rows", "paper_columns", "print_map", "drops_at", "print_rows"}:
        return PRINTING
    if name.startswith(("dilution.", "mixing.")) or name == "rows":
        return DILUTION
    return PLAN


def scope_name(value: Any) -> str:
    """A scope as the router may write it ("print", "dilutions") -> printing | dilution | plan ("" when unknown)."""
    return _SCOPE_ALIASES.get(str(value or "").strip().lower(), "")


@dataclass(frozen=True)
class PlanOperation:
    op: str
    path: str
    value: Any = None
    params: dict[str, Any] = field(default_factory=dict)       # factor / amount / count / mode / target
    expected_before: Any = None
    evidence: str = ""
    kind: str = "requested"                                    # requested, or dependent (Python's consequence)
    why: str = ""
    mode: str = ""                                             # add | modify | replace ("" = the op's plain meaning)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "PlanOperation":
        """One operation as the router (or a GUI control, a rollback, a saved run) writes it."""
        params = {key: raw[key] for key in ("factor", "amount", "count", "mode", "target", "source", "group", "item")
                  if raw.get(key) is not None}
        mode = str(raw.get("edit_mode") or "").strip().lower()
        return cls(op=str(raw.get("op") or "set").strip().lower(), path=str(raw.get("path", "")).strip(),
                   value=deepcopy(raw.get("value")), params=params, expected_before=raw.get("expected_before"),
                   evidence=str(raw.get("evidence") or ""),
                   kind="dependent" if str(raw.get("kind") or "").lower() == "dependent" else "requested",
                   why=str(raw.get("why") or ""), mode=mode if mode in EDIT_MODES else "")

    @property
    def label(self) -> str:
        path = self.path.strip().lower()
        return field_label(path) if path in EDITABLE_FIELDS else self.path


@dataclass
class PatchResult:
    after: dict[str, Any]
    changes: list[dict[str, Any]] = field(default_factory=list)   # {path, before, after, kind, evidence, why}
    failed: list[tuple[str, str]] = field(default_factory=list)   # (what, why it could not be applied)
    notes: list[str] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [change["path"] for change in self.changes]


def _same(a: Any, b: Any) -> bool:
    return a == b or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(float(a) - float(b)) < 1e-9)


def _tidy(value: float) -> int | float:
    value = round(float(value), 4)
    return int(value) if value.is_integer() else value


def relative_value(canonical: str, op: str, params: dict[str, Any], current: Any, normalize: Callable[[Any], Any],
                   *, startup_factors: Iterable[float] = ()) -> Any:
    """The value a relative operation gives (the arithmetic is Python's, never the model's)."""
    if op == "none" and canonical == "print.replicates":
        return 1
    if current is None:
        raise FieldError("it has no current value to work from")
    if op in {"scale", "multiply"}:
        factor = float(params.get("factor"))
        if factor <= 0:
            raise FieldError("the scale factor must be positive")
        if isinstance(current, list):
            return normalize([_tidy(item * factor) for item in current])
        return normalize(_tidy(float(current) * factor))
    if op == "add":
        amount = float(params.get("amount"))
        if isinstance(current, list):
            raise FieldError("cannot add to a list of values")
        return normalize(_tidy(float(current) + amount))
    if op == "scale_each":
        if canonical != "dilution.factors":
            raise FieldError("scale_each only applies to the dilution factors")
        factor = float(params.get("factor"))
        if factor <= 0:
            raise FieldError("the scale factor must be positive")
        return normalize([_tidy(item * factor) for item in current])
    if op == "set_count":
        if canonical != "dilution.factors":
            raise FieldError("set_count only applies to the number of dilutions")
        count = int(float(params.get("count")))
        factors = [float(item) for item in current]
        if count < 1:
            raise FieldError("at least one dilution is needed")
        if count > 8:
            raise FieldError(f"a series has 1 to 8 dilutions (one per row of a single plate column), so {count} "
                             "dilutions are not possible in one run")
        if count <= len(factors):
            return normalize([_tidy(item) for item in factors[:count]])
        ratios = {round(b / a, 6) for a, b in zip(factors, factors[1:]) if a > 0}
        if len(factors) >= 2 and len(ratios) == 1 and next(iter(ratios)) > 1:
            ratio = next(iter(ratios))
            while len(factors) < count:
                factors.append(factors[-1] * ratio)
            return normalize([_tidy(item) for item in factors])
        startup = [float(item) for item in startup_factors]
        example = (" - for example " + ", ".join(fmt_factor(item) for item in startup[:count])
                   + " (the start of the standard series)") if len(startup) >= count else ""
        raise FieldError(f"I can't choose the extra dilution factors for you; say all {count} factors{example}")
    raise FieldError(f"unknown change operation {op!r}")


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _one(normalize: Callable[[Any], Any], item: Any) -> Any:
    """One item of a list field, normalized the way the field normalizes its list."""
    value = normalize([item])
    return value[0] if isinstance(value, list) else value


# A newer placement of ONE source supersedes its older, less specific one: exact positions replace everything else;
# a count or paper rows replace exact positions and each other; columns replace exact positions. Fields given together
# in one operation are kept together ("3 prints on rows B and C").
_SUPERSEDES = {"positions": ("count", "rows", "columns"), "count": ("positions", "rows"),
               "rows": ("positions", "count"), "columns": ("positions",)}


def merge_intent(item: Any, value: Any) -> dict[str, Any]:
    """One source's print intent after an edit of it: the fields `value` gives replace those fields (None removes one),
    a "source" in it re-points the intent at another liquid, and the source's older placement gives way to the newer
    one (_SUPERSEDES). Every other field - and every other source - stays as it was."""
    from src.agents.dye_demo.references import intent_parts

    ref, fields = intent_parts(item)
    if not isinstance(value, dict):
        value = {"source": value}                       # "use dilution 3 for that print group instead"
    given = {key: field_value for key, field_value in value.items() if key != "source"}
    merged = dict(fields)
    for key, field_value in given.items():
        if field_value is None:
            continue                                    # taking a field out supersedes nothing
        for older in _SUPERSEDES.get(key, ()):
            if older not in given:
                merged.pop(older, None)
    for key, field_value in given.items():
        if field_value is None:
            merged.pop(key, None)
        else:
            merged[key] = field_value
    return {"source": value.get("source") or ref, **merged}


def _matches(item: Any, selector: Any, config: dict[str, Any]) -> bool:
    """A print.source item is the one a selector names: the same reference, or the same plate well now ("A11" for the
    dilution made in A11)."""
    from src.agents.dye_demo.references import UnresolvedRef, intent_parts, parse_ref, resolve

    ref = intent_parts(item)[0]
    try:
        wanted = str(parse_ref(selector))
        if ref == wanted:
            return True
        return resolve(ref, config).well == resolve(wanted, config).well
    except (FieldError, UnresolvedRef):
        return False


def _source_value(operation: PlanOperation, current: Any, normalize: Callable[[Any], Any],
                  config: dict[str, Any]) -> Any:
    """print.source edited one print group at a time - named by its source (operation.params["source"]) or by its
    place in print order (params["group"], 1 = the first): set merges into that group's intent (adding the source when
    it is not printing yet), remove takes the group out - or only the intent fields named in the value. Without a
    selector, print.source is a list field like any other."""
    from src.agents.dye_demo.references import INTENT_FIELDS, dilution_outputs

    selector = operation.params.get("source")
    group = operation.params.get("group")
    value = operation.value
    if value is None and operation.op != "remove" and operation.params.get("count") is not None:
        value = {"count": operation.params["count"]}         # the count written beside the selector
    if current is None or current == "":
        # every dilution prints now: name them, so the one edited can differ from the others
        current = [str(output.ref) for output in dilution_outputs(config)]
    items = list(current)
    if group not in (None, ""):
        if isinstance(group, bool) or not str(group).strip().isdigit() or not 1 <= int(group) <= len(items):
            raise FieldError(f"there are {len(items)} print groups, so there is no group {group}")
        index = int(group) - 1
        selector = selector or items[index]
    else:
        index = next((position for position, item in enumerate(items) if _matches(item, selector, config)), None)
    if operation.op == "remove":
        if index is None:
            raise FieldError(f"{selector} is not printing")
        names = [] if operation.value in (None, "", []) else [str(name) for name in _items(operation.value)]
        if names and all(name in INTENT_FIELDS for name in names):
            items[index] = merge_intent(items[index], {name: None for name in names})
        else:
            del items[index]
        if not items:
            raise FieldError("that would leave nothing to print; to stop printing, turn printing off")
        return normalize(items)
    if operation.op not in {"set", "append"}:
        raise FieldError(f"{operation.op} does not apply to one print source; give its new count, rows, columns, "
                         "positions or drops")
    if index is None:
        items.append(merge_intent(selector, value or {}))
    else:
        items[index] = merge_intent(items[index], value or {})
    return normalize(items)


@dataclass
class _ScopeEdit:
    """What edit semantics did in one patch: the scopes whose selection was replaced (their first replacing operation
    started from an empty selection) and the notes that say so on the proposal."""

    replaced: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def _upsert(items: list[Any], new: Any, config: dict[str, Any]) -> tuple[list[Any], bool]:
    """One print group added to a selection - or, when its source already prints, merged into that group (never a
    second group for the same liquid by accident). Returns (items, whether it was new)."""
    from src.agents.dye_demo.references import intent_parts, normalize_intent

    ref, fields = intent_parts(normalize_intent(new))
    index = next((position for position, item in enumerate(items) if _matches(item, ref, config)), None)
    if index is None:
        return items + [new], True
    updated = list(items)
    updated[index] = merge_intent(updated[index], fields)
    return updated, False


def _default_selection(config: dict[str, Any]) -> list[str]:
    """What prints when print.source is not set: every dilution output, named by reference."""
    from src.agents.dye_demo.references import dilution_outputs

    return [str(output.ref) for output in dilution_outputs(config)]


def _moded_value(operation: PlanOperation, canonical: str, current: Any, normalize: Callable[[Any], Any],
                 startup_factors: Iterable[float], config: dict[str, Any], edit: _ScopeEdit) -> Any:
    """The field's new value under the operation's edit mode (see the module docstring)."""
    from src.agents.dye_demo.references import intent_parts, normalize_intent

    mode, scope = operation.mode, scope_of(canonical)
    selector = operation.params.get("source") not in (None, "") or operation.params.get("group") not in (None, "")
    if mode == MODIFY and canonical == "print.source" and operation.params.get("source") not in (None, "") \
            and operation.op == "set":
        base = current if current not in (None, "") else _default_selection(config)
        if not any(_matches(item, operation.params["source"], config) for item in base):
            edit.note(f"{operation.params['source']} was not printing, so this adds it.")
    if mode == REPLACE and scope not in edit.replaced and canonical in {"print.source", "dilution.factors"} \
            and operation.op in {"set", "append"}:
        # the first replacing operation of the scope starts the new selection from nothing
        edit.replaced.add(scope)
        current = []
        if operation.op == "append":
            operation = PlanOperation("set", operation.path, operation.value, operation.params,
                                      evidence=operation.evidence, kind=operation.kind, why=operation.why, mode=mode)
    if canonical == "print.source" and mode in {ADD, MODIFY, REPLACE} and operation.op in {"set", "append"} \
            and not selector and not (mode == REPLACE and operation.op == "set" and current == []):
        # add / modify the groups named, one by one; every other group stays as it is
        items = list(current) if current not in (None, "", []) else ([] if scope in edit.replaced
                                                                     else _default_selection(config))
        for new in _items(operation.value):
            normalized = normalize_intent(new)
            items, added = _upsert(items, normalized, config)
            ref = intent_parts(normalized)[0]
            if added and mode == MODIFY:
                edit.note(f"{ref} was not printing, so this adds it.")
            elif not added and mode == ADD:
                edit.note(f"{ref} was already printing; its print intent was updated, not duplicated.")
        return normalize(items)
    if canonical in LIST_FIELDS and canonical != "print.source" and mode == ADD and operation.op in {"set", "append"}:
        # keep what the list has and add the items named that it does not have yet
        base = list(current) if isinstance(current, list) else ([] if current is None else [current])
        merged = list(base)
        for item in _items(operation.value):
            normalized = _one(normalize, item)
            if any(_same(normalized, existing) for existing in merged):
                edit.note(f"{field_label(canonical)}: {normalized} is already there, so it was not added twice.")
            else:
                merged.append(normalized)
        return normalize(merged)
    return _field_value(operation, canonical, current, normalize, startup_factors, config)


def _field_value(operation: PlanOperation, canonical: str, current: Any, normalize: Callable[[Any], Any],
                 startup_factors: Iterable[float], config: dict[str, Any] | None = None) -> Any:
    op = operation.op
    if operation.params.get("item") not in (None, "") and canonical in LIST_FIELDS and canonical != "print.source":
        # one item of a list named by its value: "change the 5x dilution to 6x" -> item 5, value 6
        base = list(current) if isinstance(current, list) else ([] if current is None else [current])
        target = _one(normalize, operation.params["item"])
        index = next((position for position, item in enumerate(base) if _same(item, target)), None)
        if index is None:
            raise FieldError(f"{target} is not in it")
        if op == "remove":
            del base[index]
            if not base:
                raise FieldError("removing every item would leave it empty")
            return normalize(base)
        if op == "set":
            base[index] = _one(normalize, operation.value)
            return normalize(base)
    if canonical == "print.source" and (operation.params.get("source") not in (None, "")
                                        or operation.params.get("group") not in (None, "")):
        return _source_value(operation, current, normalize, config or {})
    if canonical == "print.source" and op == "remove" and isinstance(current, list) and operation.value not in (None, ""):
        from src.agents.dye_demo.references import INTENT_FIELDS

        names = [str(name) for name in _items(operation.value)]
        if all(name in INTENT_FIELDS for name in names):
            # "forget the per-source drops": those intent fields go from every print group, the groups stay
            return normalize([merge_intent(item, {name: None for name in names}) for item in current])
    if op == "set":
        if operation.value is None:
            if canonical in NULLABLE_FIELDS:
                return None
            raise FieldError("no value was given")
        return normalize(operation.value)
    if op == "remove":
        if operation.value is None:
            if canonical in NULLABLE_FIELDS:
                return None
            raise FieldError("it cannot be removed; give the value it should have instead")
        if canonical == "print.drops_at":
            gone = {paper_position(item) for item in _items(operation.value)}
            left = {position: count for position, count in (current or {}).items() if position not in gone}
            return left or None
        if not isinstance(current, list) and canonical not in LIST_FIELDS:
            raise FieldError("only a list of values can have items removed")
        items = [_one(normalize, item) for item in _items(operation.value)]
        remaining = [item for item in (current if isinstance(current, list) else [current] if current is not None
                                       else []) if not any(_same(item, gone) for gone in items)]
        if remaining == (current if isinstance(current, list) else [current]):
            raise FieldError(f"{', '.join(map(str, items))} {'is' if len(items) == 1 else 'are'} not in it")
        if not remaining:
            if canonical in NULLABLE_FIELDS:
                return None
            raise FieldError("removing every item would leave it empty")
        return normalize(remaining)
    if op == "append":
        if canonical == "print.drops_at":
            return normalize({**(current or {}), **(normalize(operation.value) or {})})
        if canonical not in LIST_FIELDS:
            raise FieldError("only a list of values can have items appended")
        base = current if isinstance(current, list) else ([] if current is None else [current])
        return normalize(list(base) + _items(operation.value))
    return relative_value(canonical, op, operation.params, current, normalize, startup_factors=startup_factors)


def _selection_fields(operation: PlanOperation, config: dict[str, Any], occupied: Iterable[str]
                      ) -> list[PlanOperation]:
    """The router's selection names, as the fields they set. Values are read structurally (the selection parsers of
    natural.py), never from the scientist's words."""
    from src.agents.dye_demo.natural import (
        SelectionError,
        expand_print_map,
        expand_rows,
        selected_columns,
        selected_paper_rows,
        selected_rows,
    )
    from src.agents.dye_demo.plan import droplet_volumes

    name = operation.path.strip().lower()

    def fields(*pairs: tuple[str, Any]) -> list[PlanOperation]:
        return [PlanOperation("set", path, value, evidence=operation.evidence, kind=operation.kind, why=operation.why)
                for path, value in pairs]

    try:
        if name == "rows":
            rows = selected_rows(operation.value)
            factors = (config.get("dilution") or {}).get("factors") or []
            if len(rows) == len(factors):
                return fields(("dilution.rows", rows), ("dilution.start_row", rows[0]))
            changes, _ = expand_rows(rows, config)              # the dilutions already in those plate rows
            return fields(*((change["path"], change["value"]) for change in changes))
        if name == "paper_rows":
            return fields(("print.paper_rows", selected_paper_rows(operation.value)))
        if name == "paper_columns":
            columns = selected_columns(operation.value)
            mode = str(operation.params.get("mode") or ("anchor" if len(columns) == 1 else "exact")).lower()
            if len(columns) == 1 and mode == "anchor":
                return fields(("print.columns", None), ("print.paper_start_column", columns[0]))
            volumes = max(1, len(droplet_volumes(config)))
            if columns == list(range(columns[0], columns[0] + len(columns))) and len(columns) % volumes == 0:
                return fields(("print.columns", None), ("print.paper_start_column", columns[0]),
                              ("print.replicates", len(columns) // volumes))
            return fields(("print.columns", columns))
        if name == "drops_at":
            return [PlanOperation("append", "print.drops_at", operation.value, evidence=operation.evidence,
                                  kind=operation.kind, why=operation.why)]
        # print_map: exact positions are the dictated map itself; counts and "all" are laid out by the allocator
        try:
            return fields(("print.source_map", normalize_source_map(operation.value)))
        except (FieldError, TypeError, AttributeError):
            changes, _ = expand_print_map(operation.value, config, occupied=occupied)
            return fields(*((change["path"], change["value"]) for change in changes))
    except SelectionError as exc:
        raise FieldError(str(exc)) from exc


def apply_operations(spec: dict[str, Any], operations: Iterable[PlanOperation], *, kept: Iterable[str] = (),
                     occupied: Iterable[str] = (), startup_factors: Iterable[float] = ()) -> PatchResult:
    """Apply operations to a copy of `spec`, each on its own (see the module docstring). `kept` are fields the
    scientist asked to keep as they are: a change to one is left out with a note."""
    from src.agents.dye_demo.plan import steps_enabled

    after = deepcopy(spec)
    before = deepcopy(spec)
    result = PatchResult(after)
    kept = set(kept)
    occupied = set(occupied)
    applied: dict[str, dict[str, Any]] = {}
    edit = _ScopeEdit()
    queue = list(operations)
    while queue:
        operation = queue.pop(0)
        if operation.op == "drop":
            continue                                    # only meaningful while a clarification answer is merged
        if operation.op not in OPERATIONS:
            result.failed.append((operation.path or "?", f"unknown change operation {operation.op!r}"))
            continue
        raw_path = operation.path.strip().lower()
        canonical = canonicalize_path(after, operation.path)
        if canonical == "print.paper_columns" and isinstance(operation.value, (list, tuple)):
            raw_path, canonical = "paper_columns", "paper_columns"      # a column list, not the lab-owned paper width
        if raw_path in _SELECTIONS or canonical in _SELECTIONS:
            try:
                queue[:0] = [PlanOperation(item.op, item.path, item.value, item.params, item.expected_before,
                                           item.evidence, item.kind, item.why, mode=operation.mode)
                             for item in _selection_fields(PlanOperation(operation.op, raw_path if raw_path in
                                                                         _SELECTIONS else canonical, operation.value,
                                                                         operation.params, evidence=operation.evidence,
                                                                         kind=operation.kind, why=operation.why),
                                                           after, occupied)]
            except FieldError as exc:
                result.failed.append((_SELECTION_LABELS.get(raw_path, raw_path), str(exc)))
            continue
        reason = lab_owned_reason(canonical)
        if reason:
            result.failed.append((canonical, f"{reason}."))
            continue
        if canonical == "print.source":
            _map_as_intents(after, applied)
        label, normalize = EDITABLE_FIELDS[canonical]
        if canonical in kept and operation.kind == "requested":
            note = f"You asked to keep the {label.lower()} as it is, so I left it unchanged."
            if note not in result.notes:
                result.notes.append(note)
            continue
        try:
            actual = resolve_path(after, canonical)
            current = get_path(after, actual)
            value = _moded_value(operation, canonical, current, normalize, startup_factors, after, edit)
            if operation.expected_before not in (None, ""):
                try:
                    expected = normalize(operation.expected_before)
                except FieldError:
                    expected = operation.expected_before
                if not _same(expected, get_path(before, actual)) and not _same(expected, current):
                    raise FieldError(f"it is currently {_shown(canonical, get_path(before, actual))}, not "
                                     f"{_shown(canonical, expected)}; tell me the new value you want")
        except (FieldError, TypeError, ValueError) as exc:
            result.failed.append((label, str(exc)))
            continue
        set_path(after, actual, value)
        first = applied.get(canonical)
        applied[canonical] = {"path": canonical, "before": first["before"] if first else current, "after": value,
                              "kind": operation.kind if not first or operation.kind == "requested" else first["kind"],
                              "evidence": operation.evidence or (first or {}).get("evidence", ""),
                              "why": operation.why or (first or {}).get("why", "")}
    _replaced_scope_cleanup(before, after, applied, edit)
    _placement_consistency(before, after, applied, occupied, result.failed)
    _intent_consistency(before, after, applied)
    result.notes.extend(note for note in edit.notes if note not in result.notes)
    if any(path in PLACEMENT_FIELDS | {"print.source_map"} for path, change in applied.items()
           if change["kind"] == "requested" and change["after"] is not None) \
            and "print.enabled" not in applied and not steps_enabled(after)[1]:
        _dependent(after, applied, "print.enabled", True, "you said what to print, and printing was off")
    result.changes = [change for change in applied.values() if not _same(change["before"], change["after"])]
    return result


def _replaced_scope_cleanup(before: dict[str, Any], after: dict[str, Any], applied: dict[str, dict[str, Any]],
                            edit: _ScopeEdit) -> None:
    """A replaced selection takes the placement that belonged to the old one with it - a dictated map, plan-wide
    paper rows and columns, drops on particular positions - unless this same request set them again. Replacing the
    prints never touches the dilutions (and replacing the dilutions never touches the prints: a print of a dilution the
    new series no longer has stays, unresolved, and the run says so). The proposal says what no longer prints."""
    from src.agents.dye_demo.references import intent_parts

    if PRINTING in edit.replaced:
        for path in ("print.source_map", "print.paper_rows", "print.columns", "print.drops_at"):
            if path not in applied and get_path(after, path) not in (None, "", [], {}):
                _dependent(after, applied, path, None, "it belonged to the print selection you replaced")
        earlier = (before.get("print") or {}).get("source")
        old = [intent_parts(item)[0] for item in earlier] if earlier not in (None, "", []) else \
            _default_selection(before)
        now = (after.get("print") or {}).get("source") or []
        gone = [ref for ref in old if not any(_matches(item, ref, after) for item in now)]
        if gone:
            edit.note("The print selection is replaced; no longer printing: "
                      + ", ".join(ref.replace("dilution:", "dilution ") for ref in gone) + ".")
    if DILUTION in edit.replaced and "dilution.rows" not in applied \
            and get_path(after, "dilution.rows") not in (None, "", []):
        _dependent(after, applied, "dilution.rows", None, "the plate rows belonged to the series you replaced")


_SELECTION_LABELS = {"rows": "plate rows", "paper_rows": "paper rows", "paper_columns": "paper columns",
                     "print_map": "print map", "drops_at": "drops per position"}


def _shown(canonical: str, value: Any) -> str:
    from src.agents.dye_demo.render import format_value

    return format_value(canonical, value)


def _dependent(after: dict[str, Any], applied: dict[str, dict[str, Any]], path: str, value: Any, why: str) -> None:
    actual = resolve_path(after, path)
    current = get_path(after, actual)
    if _same(current, value):
        return
    first = applied.get(path)
    set_path(after, actual, value)
    applied[path] = {"path": path, "before": first["before"] if first else current, "after": value,
                     "kind": "dependent", "evidence": "", "why": why}


def _placement_consistency(before: dict[str, Any], after: dict[str, Any], applied: dict[str, dict[str, Any]],
                           occupied: set[str], failed: list[tuple[str, str]]) -> None:
    """One way of saying where the samples print at a time.

    A dictated print map names every source and position, so the semantic placement fields step aside when one is set.
    With a dictated map in force, a replicate count or a first paper column applies to the map itself - each source
    keeps its dictated positions and its extra prints go on the nearest free ones (placement.py; the map only exists
    because the scientist dictated positions) - while a print source, paper rows or exact columns replace the map; the
    map's sources keep printing unless a source is named."""
    from src.agents.dye_demo.placement import PlacementError, map_at_anchor, map_with_count

    printing = after.get("print") or {}
    mapped = printing.get("source_map") not in (None, "", [])
    if "print.source_map" in applied and mapped:
        for path in ("print.source", "print.paper_rows", "print.columns"):
            if path not in applied and get_path(after, path) not in (None, "", []):
                _dependent(after, applied, path, None, "the print map names every source and paper position")
        return
    asked = [path for path in sorted(PLACEMENT_FIELDS) if path in applied and applied[path]["kind"] == "requested"]
    if mapped and asked and "print.source_map" not in applied:
        replacing = [path for path in asked if path in {"print.source", "print.paper_rows", "print.columns"}]
        if not replacing:
            width = int(printing.get("paper_columns", 12) or 12)
            try:
                if "print.paper_start_column" in asked:
                    entries, _ = map_at_anchor(after, occupied, width=width)
                else:
                    entries, _ = map_with_count(before, after, occupied, width=width)
            except PlacementError as exc:
                failed.append((field_label(asked[0]), str(exc)))
                for path in asked:                      # the count cannot be laid out on this map: left as it was
                    set_path(after, resolve_path(after, path), applied[path]["before"])
                    applied.pop(path)
                return
            sources = {entry["source"]: raw["source"] for entry, raw in zip(entries, printing["source_map"])}
            _dependent(after, applied, "print.source_map",
                       [{**entry, "source": sources.get(entry["source"], entry["source"])} for entry in entries],
                       f"the {field_label(asked[0]).lower()} you asked for, applied to the print map: its positions "
                       "stay and new prints go on the nearest free ones")
            return
        if "print.source" not in applied:
            kept = list(dict.fromkeys(str(entry["source"]) for entry in printing["source_map"]))
            _dependent(after, applied, "print.source", kept, "the samples the print map printed keep printing")
        _dependent(after, applied, "print.source_map", None,
                   f"the {field_label(replacing[0]).lower()} you asked for replaces the print map")
    side_by_side = [path for path in ("print.paper_start_column", "print.replicates") if path in asked]
    if side_by_side and "print.columns" not in applied and printing.get("columns") not in (None, "", []):
        _dependent(after, applied, "print.columns", None,
                   f"the {field_label(side_by_side[0]).lower()} you asked for prints side by side again, not in the "
                   "exact paper columns")


# a plan-wide setting and the per-source overrides it updates: the newest plan-wide command applies to every source,
# unless the same request also says what a source keeps (then the router wrote that source's intent too). Paper rows or
# columns for everything are the newest word on WHERE the prints go, so they also replace per-source counts; exact
# positions are never replaced by a plan-wide setting.
_GLOBAL_OVERRIDES = {"print.replicates": ("count",), "print.droplets_per_spot": ("drops",),
                     "print.paper_rows": ("rows", "count"), "print.columns": ("columns", "count"),
                     "print.paper_start_column": ("columns",)}


def _map_as_intents(after: dict[str, Any], applied: dict[str, dict[str, Any]]) -> None:
    """An older dictated print map (print.source_map, e.g. from a saved run) becomes print intents with the same exact
    positions before one source is edited, so editing one source never loses where the others print. Per-position drop
    counts move to print.drops_at. A map that prints several drop volumes stays a map (an intent has one volume)."""
    from src.agents.dye_demo.plan import droplet_volumes

    printing = after.get("print") or {}
    entries = printing.get("source_map")
    if entries in (None, "", []) or printing.get("source") not in (None, "", []):
        return
    try:
        entries = normalize_source_map(entries)
    except FieldError:
        return
    volumes = droplet_volumes(after)
    if len(volumes) > 1 or any("volume_ul" in entry and volumes and abs(float(entry["volume_ul"]) - volumes[0]) > 1e-9
                               for entry in entries):
        return
    intents = [{"source": entry["source"], "positions": list(entry["positions"])} for entry in entries]
    drops = {position: count for entry in entries for position, count in (entry.get("drops") or {}).items()}
    why = "the print map's exact positions, kept as each source's print intent"
    _dependent(after, applied, "print.source", intents, why)
    if drops:
        _dependent(after, applied, "print.drops_at", {**(printing.get("drops_at") or {}), **drops}, why)
    _dependent(after, applied, "print.source_map", None, why)


def _intent_consistency(before: dict[str, Any], after: dict[str, Any], applied: dict[str, dict[str, Any]]) -> None:
    """"Now make all of them twice" after "make A11 three times": the plan-wide count is the newest instruction, so the
    per-source counts it updates go - except on a source this same request also set ("all twice, but C11 three times").
    Exact positions are never undone by a plan-wide setting."""
    from src.agents.dye_demo.references import intent_parts

    items = (after.get("print") or {}).get("source")
    fields = sorted({name for path, names in _GLOBAL_OVERRIDES.items()
                     if path in applied and applied[path]["kind"] == "requested" for name in names})
    if not fields or not isinstance(items, list):
        return
    earlier = list((before.get("print") or {}).get("source") or [])
    # a print group this same request set keeps what it says; the others take the plan-wide value
    touched = [len(earlier) != len(items) or earlier[index] != item for index, item in enumerate(items)]
    if all(touched):
        return
    updated = [item if changed else merge_intent(item, {name: None for name in fields})
               for item, changed in zip(items, touched)]
    updated = [intent_parts(item)[0] if isinstance(item, dict) and len(item) == 1 else item for item in updated]
    if updated != items:
        _dependent(after, applied, "print.source", updated,
                   f"the plan-wide setting you asked for now applies to every source (per-source {', '.join(fields)} "
                   "replaced)")


def _made_wells(after: dict[str, Any]) -> dict[str, str]:
    """{plate well: the reference of the dilution this run makes in it}."""
    from src.agents.dye_demo.references import DILUTION, dilution_outputs

    return {output.well: str(output.ref) for output in dilution_outputs(after) if output.made_here
            and output.ref.kind == DILUTION}


def made_wells_named(result: PatchResult) -> list[str]:
    """Plate wells this patch names as print sources that the resulting plan fills with one of its own dilutions."""
    from src.agents.dye_demo.references import intent_parts

    made = _made_wells(result.after)
    printing = result.after.get("print") or {}
    named = []
    if "print.source" in result.paths and isinstance(printing.get("source"), list):
        named += [intent_parts(item)[0] for item in printing["source"] if intent_parts(item)[0] in made]
    if "print.source_map" in result.paths and isinstance(printing.get("source_map"), list):
        named += [entry["source"] for entry in printing["source_map"] if entry.get("source") in made]
    return list(dict.fromkeys(named))


def refer_to_made_wells(result: PatchResult) -> None:
    """A print source written as a plate well that this run fills with one of its dilutions IS that dilution: it is
    stored as the dilution's reference, so the print follows the dilution if it moves later (and never prints from a
    well the series has left). The caller first makes sure it is not the scientist's own sample (made_not_printed)."""
    from src.agents.dye_demo.references import intent_parts

    made = _made_wells(result.after)
    printing = result.after.get("print") or {}
    changes = {change["path"]: change for change in result.changes}
    if "print.source" in changes and isinstance(printing.get("source"), list):
        printing["source"] = [made.get(item, item) if isinstance(item, str) else
                              {**item, "source": made.get(intent_parts(item)[0], intent_parts(item)[0])}
                              for item in printing["source"]]
        changes["print.source"]["after"] = deepcopy(printing["source"])
    if "print.source_map" in changes and isinstance(printing.get("source_map"), list):
        printing["source_map"] = [{**entry, "source": made.get(entry["source"], entry["source"])}
                                  for entry in printing["source_map"]]
        changes["print.source_map"]["after"] = deepcopy(printing["source_map"])
