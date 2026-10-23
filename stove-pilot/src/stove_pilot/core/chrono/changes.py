"""Pure helpers on chrono layouts and configurations used by the repository."""

from dataclasses import dataclass

from stove_pilot.core.chrono.layout import ChronoLayout
from stove_pilot.core.chrono.models import ChronoBlockKind, ChronoConfig


@dataclass(frozen=True, slots=True)
class FieldRef:
    """Name and verification flag of one layout byte."""

    kind: ChronoBlockKind
    name: str
    verified: bool


def field_map(layout: ChronoLayout) -> dict[int, FieldRef]:
    """Map every layout address to its block kind and field name.

    :param layout: Chrono layout.
    :return: Field references keyed by address.
    """
    fields: dict[int, FieldRef] = {}
    for block in layout.blocks:
        fields[block.enable] = FieldRef(block.kind, "enable", block.verified)
        for index, program in enumerate(block.programs, start=1):
            named = (
                ("start", program.start),
                ("stop", program.stop),
                ("power", program.power),
                ("setpoint", program.setpoint),
                ("days", program.days),
            )
            for name, address in named:
                if address is not None:
                    fields[address] = FieldRef(block.kind, f"P{index}.{name}", block.verified)
    return fields


def enabled_kinds(config: ChronoConfig) -> list[ChronoBlockKind]:
    """List the enabled blocks.

    :param config: Chrono configuration.
    :return: Kinds of the enabled blocks, in block order.
    """
    return [block.kind for block in config.blocks if block.enabled]


def newly_enabled(current: ChronoConfig, wanted: ChronoConfig) -> list[ChronoBlockKind]:
    """List the blocks that go from disabled to enabled.

    :param current: Configuration on the stove.
    :param wanted: Configuration to save.
    :return: Kinds enabled in ``wanted`` and not in ``current``.
    """
    already = set(enabled_kinds(current))
    return [kind for kind in enabled_kinds(wanted) if kind not in already]
