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
    for family in grouped.values():
        ordered = sorted(family, key=lambda item: item.score or -999, reverse=True)
        selected.extend(ordered[:per_family])
    return sorted(selected, key=lambda item: item.score or -999, reverse=True)

