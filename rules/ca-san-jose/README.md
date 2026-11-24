# San José department rules

The four YAML files contain **24 concrete starter checks**, replacing the earlier research-only entries. They contain numeric limits, conditional branches, units, source locations, exceptions/limitations, and small boundary examples. The original [research draft](san-jose-draft.md) remains background material.

## Supported scope

The first supported category is conventional single-family primary dwellings in San José, using the 2025 code cycle. Apply checks to new work and affected elements only after confirming the applicable alteration requirements. Zoning baseline branches cover R-1-8, R-1-5, R-1-2, R-1-1, and R-1-RR.

Commercial/assembly, multifamily, ADU/JADU, SB 9, Planned Development, historic, and alternative design pathways require additional rules. Do not substitute a generic R-1 label for a verified city district. Each pack lists remaining coverage gaps. These are the application's four review categories, not the city's organizational chart.

## Concrete examples

| File | Selected requirements |
| --- | --- |
| zoning.yaml | R-1-8 baseline: front 20 ft, interior side 5 ft, corner side 12.5 ft, rear 20 ft; height 35 ft and 2.5 stories. Other supported districts have their own branches. |
| fire.yaml | Required dwelling egress door: at least one, clear width 32 in and clear height 78 in. Required emergency openings: clear width 20 in, height 24 in, sill height at most 44 in; a separate area check also applies. |
| water.yaml | Water closet 1.28 gpf; residential faucet and showerhead limits. Detached single-family stormwater trigger 10,000 sqft of new/replaced impervious area, with separate small-project and redevelopment checks. |
| structural.yaml | Conventional sill anchors: diameter at least 0.5 in and embedment at least 7 in; spacing depends on stories. Other checks cover plate bolt count, end distances, slab thickness, and T-footing geometry. |

These values are conditional requirements, not universal rules. For example, the dwelling egress-door count is not a commercial exit-count table, and an emergency escape window is not the required dwelling door. A 20-by-24-inch clear opening passes the dimension minima but fails the separate area requirement.

## Evaluation convention

1. Confirm the pack scope, applicable code cycle, project evidence, and rule prerequisites. Resolve claimed exceptions and stricter approved-plan requirements. An unresolved exception requires human review before declaring a violation.
2. Evaluate applies_when as AND. Unknown applicability yields NEEDS_INFORMATION; evidence proving it false yields NOT_APPLICABLE. An empty list means the rule applies within the declared pack scope.
3. Select exactly one requirement.cases branch by its when conditions. Unknown required conditions need information; overlapping branches or an uncovered known condition need review. Empty when means the default unconditional branch.
4. All checks in the selected branch must pass. Operators are eq, in, gte, gt, lte, and lt. The value is the comparison target, in the stated unit. Missing facts need information; they do not default to zero or false.
5. Apply checks separately to every relevant affected object and retain its document/page evidence. A missing inventory or empty collection cannot establish compliance. Example facts assume scope, prerequisites, and exception screening are already satisfied; their outcomes concern one rule only.

Gemini can interpret the evidence and applicability and explain findings, using these supplied requirements and sources. It must not invent thresholds or requirements from memory. Normalized fact names are an intake vocabulary, not fields already added to contract/models.py. The current contract lacks several necessary facts and unresolved-review states; integration still needs coordinated changes.

Evaluation is enabled for these individual checks. **Final department approval from this partial pack alone is disabled.** Passing a lower-bound structural detail does not establish engineering adequacy; passing a document-presence check does not establish design performance. No evaluator service has been added by this change.

## Sources and verification

- [San José Residential Construction Guidelines, 2025 codes](https://www.sanjoseca.gov/home/showpublisheddocument/94721/638511967421800000): full PDF read; cover identifies the 2025 codes. Printed pages and named sections are recorded in rules. The city guide itself directs readers to adopted codes for complete requirements.
- [City plumbing guidance](https://www.sanjoseca.gov/businesses/development-services-permit-center/start-your-project/plumbing-projects), [window guidance](https://www.sanjoseca.gov/businesses/development-services-permit-center/start-your-project/window-replacements), and [basement guidance](https://www.sanjoseca.gov/businesses/development-services-permit-center/start-your-project/basements): official indexed excerpts read; full-page fetches returned HTTP 403. Those source records explicitly say indexed_official_page_excerpt.
- [San José Stormwater Regulations and Evaluation Form](https://www.sanjoseca.gov/home/showpublisheddocument/32849/638947338114830000): full PDF read, including Form #138 dated May 15, 2025. Rules cite the relevant form sections rather than assuming all project categories have the same trigger.

Checked November 14, 2026. These are sourced city-guidance checks, not a claim that the entire adopted code and all amendments have been encoded. Do not turn ambiguous guide entries into universal limits. In particular, this set does not encode the guide's inconsistent soil-bearing units or its vapor-barrier summary; those need verification against operative provisions. The five full sample permits in T3 remain separate work; inline rule examples are not substitutes for them.
