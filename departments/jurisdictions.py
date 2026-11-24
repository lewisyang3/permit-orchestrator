"""Per-city rule pack registry for one department service.

Each city lives in its own folder under the rules root:

    rules/<jurisdiction_id>/city.yaml          # jurisdiction metadata
    rules/<jurisdiction_id>/<department>.yaml  # that department's rule pack

A department service loads every city pack for its department at startup and picks
one per request from the permit's property address. A permit whose city has no pack
is never evaluated against another city's rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

from contract.models import DepartmentName, PropertyAddress
from contract.normalize import normalize_city, normalize_state
from departments.evaluator import load_and_validate_rule_pack


@dataclass(frozen=True)
class Jurisdiction:
    jurisdiction_id: str
    city: str
    state: str
    aliases: Tuple[str, ...]
    code_cycle: Optional[str]
    pack: Dict[str, Any]

    def summary(self) -> Dict[str, Any]:
        return {
            "jurisdiction_id": self.jurisdiction_id,
            "city": self.city,
            "state": self.state,
            "code_cycle": self.code_cycle,
            "rule_set_id": self.pack["rule_set_id"],
            "version": self.pack.get("version"),
            "rule_count": len(self.pack["rules"]),
            "scope": self.pack.get("scope", {}),
        }


class JurisdictionRegistry:
    """The rule packs one department service can evaluate, keyed by (state, city)."""

    def __init__(self, department: DepartmentName, jurisdictions: List[Jurisdiction]):
        self.department = department
        self.jurisdictions = {j.jurisdiction_id: j for j in jurisdictions}
        self._by_place: Dict[Tuple[str, str], Jurisdiction] = {}
        for j in jurisdictions:
            for name in (j.city, *j.aliases):
                key = (j.state, normalize_city(name) or "")
                other = self._by_place.get(key)
                if other is not None and other.jurisdiction_id != j.jurisdiction_id:
                    raise ValueError(
                        f"City name '{name}, {j.state}' is claimed by both "
                        f"'{other.jurisdiction_id}' and '{j.jurisdiction_id}'"
                    )
                self._by_place[key] = j

    def resolve(self, address: Optional[PropertyAddress]) -> Optional[Jurisdiction]:
        """Return the pack for the permit's city, or None when no pack covers it."""
        if address is None:
            return None
        state = normalize_state(address.state)
        city = normalize_city(address.city)
        if not state or not city:
            return None
        return self._by_place.get((state, city))


def _read_city(city_file: Path) -> Dict[str, Any]:
    with city_file.open("r", encoding="utf-8") as handle:
        meta = yaml.safe_load(handle)
    if not isinstance(meta, dict):
        raise ValueError(f"{city_file} must be a YAML mapping")
    folder_id = city_file.parent.name
    if meta.get("jurisdiction_id") != folder_id:
        raise ValueError(
            f"{city_file}: jurisdiction_id '{meta.get('jurisdiction_id')}' must match its folder '{folder_id}'"
        )
    for key in ("city", "state"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            raise ValueError(f"{city_file}: missing required string '{key}'")
    state = normalize_state(meta["state"])
    if state is None:
        raise ValueError(f"{city_file}: unrecognized state '{meta['state']}'")
    meta["state"] = state
    aliases = meta.get("aliases") or []
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise ValueError(f"{city_file}: 'aliases' must be a list of strings")
    departments = meta.get("departments") or []
    if not isinstance(departments, list):
        raise ValueError(f"{city_file}: 'departments' must be a list")
    unknown = [d for d in departments if d not in {n.value for n in DepartmentName}]
    if unknown:
        raise ValueError(f"{city_file}: unknown departments {unknown}")
    return meta


def load_jurisdiction(city_dir: Path, department: DepartmentName) -> Optional[Jurisdiction]:
    """Load one city's pack for a department; None if the city doesn't cover it."""
    meta = _read_city(city_dir / "city.yaml")
    pack_file = city_dir / f"{department.value}.yaml"
    if department.value not in meta.get("departments", []):
        if pack_file.exists():
            raise ValueError(f"{pack_file} exists but '{department.value}' is not listed in city.yaml departments")
        return None
    if not pack_file.exists():
        raise ValueError(f"{city_dir / 'city.yaml'} lists '{department.value}' but {pack_file} is missing")

    pack = load_and_validate_rule_pack(pack_file, expected_department=department)
    if pack.get("jurisdiction_id") != meta["jurisdiction_id"]:
        raise ValueError(
            f"{pack_file}: jurisdiction_id '{pack.get('jurisdiction_id')}' must be '{meta['jurisdiction_id']}'"
        )
    return Jurisdiction(
        jurisdiction_id=meta["jurisdiction_id"],
        city=meta["city"],
        state=meta["state"],
        aliases=tuple(meta.get("aliases") or ()),
        code_cycle=meta.get("code_cycle"),
        pack=pack,
    )


def load_registry(rules_root: Union[str, Path], department: DepartmentName) -> JurisdictionRegistry:
    """Load every city pack for a department. Fails fast on any malformed pack."""
    root = Path(rules_root)
    if not root.is_dir():
        raise ValueError(f"Rules root not found: {root}")
    jurisdictions = []
    for city_file in sorted(root.glob("*/city.yaml")):
        j = load_jurisdiction(city_file.parent, department)
        if j is not None:
            jurisdictions.append(j)
    if not jurisdictions:
        raise ValueError(f"No city rule packs for department '{department.value}' under {root}")
    return JurisdictionRegistry(department, jurisdictions)


def single_pack_registry(rule_file: Union[str, Path], department: DepartmentName) -> "SinglePackRegistry":
    """Registry for one explicit rule file (tests and local experiments only)."""
    return SinglePackRegistry(department, load_and_validate_rule_pack(Path(rule_file), expected_department=department))


class SinglePackRegistry(JurisdictionRegistry):
    """Applies one rule file to every permit, whatever its city.

    Used only when a rule file is passed explicitly (create_app(rule_file=...) or RULE_FILE).
    Deployments set RULES_ROOT and get strict per-city resolution.
    """

    def __init__(self, department: DepartmentName, pack: Dict[str, Any]):
        self.department = department
        self.pack = pack
        jid = pack.get("jurisdiction_id") or "single-pack"
        self._only = Jurisdiction(
            jurisdiction_id=jid, city=jid, state="", aliases=(), code_cycle=None, pack=pack
        )
        self.jurisdictions = {jid: self._only}
        self._by_place = {}

    def resolve(self, address: Optional[PropertyAddress]) -> Optional[Jurisdiction]:
        return self._only
