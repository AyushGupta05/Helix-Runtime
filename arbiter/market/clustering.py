from __future__ import annotations

from collections import defaultdict

from arbiter.core.contracts import Bid


def strategy_fingerprint(bid: Bid) -> str:
    files = ",".join(sorted(bid.touched_files[:3]))
    validators = ",".join(sorted(bid.validator_plan))
    return "|".join([bid.strategy_family, files, validators, bid.dependency_impact])


def cluster_and_select(bids: list[Bid], per_family: int = 2) -> list[Bid]:
    grouped: dict[str, list[Bid]] = defaultdict(list)
    for bid in bids:
        grouped[strategy_fingerprint(bid)].append(bid)
    selected: list[Bid] = []
    family_first_pass: list[Bid] = []
    for family in grouped.values():
        ordered = sorted(family, key=lambda item: item.score or -999, reverse=True)
        family_first_pass.extend(ordered[:1])
        selected.extend(ordered[:per_family])
    ordered_selected = sorted(selected, key=lambda item: item.score or -999, reverse=True)
    seen_families: set[str] = set()
    diversified: list[Bid] = []
    for bid in sorted(family_first_pass, key=lambda item: item.score or -999, reverse=True):
        if bid.strategy_family in seen_families:
            continue
        diversified.append(bid)
        seen_families.add(bid.strategy_family)
    for bid in ordered_selected:
        if bid.bid_id in {item.bid_id for item in diversified}:
            continue
        diversified.append(bid)
    return diversified
