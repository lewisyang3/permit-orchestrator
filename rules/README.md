# City rule packs

Each city is a self-contained folder. Cities are added one at a time and never share rules.

```
rules/
  ca-san-jose/
    city.yaml        # jurisdiction metadata
    README.md        # scope, sources and caveats for this city's checks
    zoning.yaml  fire.yaml  water.yaml  structural.yaml
```

| City | Folder | Departments | Notes |
| --- | --- | --- | --- |
| San José, CA | [ca-san-jose](ca-san-jose/README.md) | zoning, fire, water, structural | Single-family residential starter checks, 2025 codes |

## How a permit finds its pack

Each department service loads every city's pack for its own department at startup (`RULES_ROOT`, default `rules`). For each permit it normalizes `property_address.city` (accents, case and punctuation are ignored) and `property_address.state` (name or USPS code), then looks them up against each city's `city` and `aliases`.

If no city matches, the department doesn't evaluate the permit. It answers HTTP 422 with `code: UNSUPPORTED_JURISDICTION` and one `REQUIRES_HUMAN_REVIEW` reason, and the orchestrator shows that message. A permit is never checked against another city's rules.

Startup fails if any pack is malformed: an id that doesn't match its folder, a department listed with no file (or a file not listed), or two cities claiming the same name.

## Adding a city

1. Create `rules/<state>-<city>/` with a lowercase, hyphenated id, e.g. `ca-sunnyvale`.
2. Write `city.yaml`:
   ```yaml
   jurisdiction_id: "ca-sunnyvale"   # must equal the folder name
   city: "Sunnyvale"
   state: "CA"
   aliases: []                        # other spellings that should resolve here
   code_cycle: "2025 California codes"
   departments: ["zoning", "fire"]    # only the departments you have rules for
   ```
   A department you leave out reports this city as unsupported until its pack exists.
3. For each listed department add `<department>.yaml` in the same format as the San José packs, with `jurisdiction_id` set to the folder id and a city-specific `rule_set_id`. Every rule needs `source` entries that point to the city's own documents (URL, section, printed page).
4. Add a `README.md` covering scope, sources, the date you checked them, and known gaps.
5. Add the embedded `examples` to each rule, and boundary tests if needed. `tests/test_department_evaluator.py` runs every example in every city folder.
6. Run `uv run pytest`. No service or compose changes are needed.
