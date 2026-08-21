"""Deterministic repairs for incremental clustering results."""

from collections.abc import Mapping

from clustering_ids import ComponentId
from static_analyzer.clustering.models import ClusterGroup


def repair_member_ownership(
    groups: list[ClusterGroup],
    previous_member_owner: Mapping[str, Mapping[str, ComponentId]],
) -> None:
    """Keep surviving members with their surviving previous group."""
    group_by_id = {group.group_id: group for group in groups}
    for language, owner_by_member in previous_member_owner.items():
        current_group = {
            qualified_name: group
            for group in groups
            for qualified_name in group.symbol_members_by_language.get(language, set())
        }
        for previous_group_id in sorted(
            {owner_by_member[qualified_name] for qualified_name in current_group if qualified_name in owner_by_member}
            - set(group_by_id)
        ):
            target = ClusterGroup(
                group_id=previous_group_id,
                cluster_ids=[],
                previous_component_id=previous_group_id,
            )
            groups.append(target)
            group_by_id[previous_group_id] = target
        for qualified_name, previous_group_id in owner_by_member.items():
            source = current_group.get(qualified_name)
            target = group_by_id.get(previous_group_id)
            if source is None or target is None or source is target:
                continue
            source.symbol_members_by_language[language].remove(qualified_name)
            if not source.symbol_members_by_language[language]:
                del source.symbol_members_by_language[language]
            target.symbol_members_by_language.setdefault(language, set()).add(qualified_name)
