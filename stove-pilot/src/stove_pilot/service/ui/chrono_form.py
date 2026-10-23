"""Turn the chrono editor form into the ``PUT /chrono`` body (the ``ChronoConfigDto`` shape)."""

from typing import Any

from werkzeug.datastructures import MultiDict

from stove_pilot.service.ui.chrono_view import BLOCK_ORDER

DAY_BLOCK = "day"


def _program(form: MultiDict[str, str], kind: str, index: int) -> dict[str, Any]:
    """Read one program of a block.

    :param form: Submitted form (``<block>.<index>.<field>`` names).
    :param kind: Block kind.
    :param index: 1-based program index.
    :return: The program body.
    :raises ValueError: The setpoint or the power is not an integer.
    """
    prefix = f"{kind}.{index}."
    days = form.getlist(prefix + "day")
    return {
        "index": index,
        "start": form.get(prefix + "start") or None,
        "stop": form.get(prefix + "stop") or None,
        # Reason: the day block has no day mask; only a forged form can send days for it.
        "days": days if (days or kind != DAY_BLOCK) else None,
        "setpoint_c": int(form.get(prefix + "setpoint", "")),
        "power_level": int(form.get(prefix + "power", "")),
    }


def build_config_body(form: MultiDict[str, str]) -> dict[str, Any]:
    """Build the full configuration body from the submitted form.

    :param form: Submitted form.
    :return: Body of ``PUT /chrono`` (blocks in day, week, weekend order).
    :raises ValueError: A setpoint or a power is not an integer.
    """
    blocks = []
    for kind in BLOCK_ORDER:
        programs = []
        while f"{kind}.{len(programs) + 1}.setpoint" in form:
            programs.append(_program(form, kind, len(programs) + 1))
        blocks.append(
            {
                "kind": kind,
                "enabled": form.get(f"{kind}.enabled") == "true",
                "programs": programs,
            }
        )
    return {"blocks": blocks}
