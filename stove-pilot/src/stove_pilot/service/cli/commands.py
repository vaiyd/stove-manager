"""Discovery commands: argument parsing and handlers for the Navel module."""

import argparse
import csv
import functools
import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from stove_pilot.common.exceptions import (
    GatewayError,
    GatewayNackError,
    GatewayProtocolError,
)
from stove_pilot.lib.navel import Memory, NavelClient, encode_item

ClientGetter = Callable[[], NavelClient]
Handler = Callable[[argparse.Namespace, ClientGetter], int]
Register = tuple[Memory, int]
Snapshot = dict[Memory, dict[int, Any]]

# Reason: the module returns blank replies for larger batches (long request URLs).
_CHUNK_SIZE = 8
_ADDRESS_SPACE = 0x100
_CLOCK_START = 0x7A
_CLOCK_FIELDS = ("second", "weekday", "hour", "minute", "day", "month", "year")
_EXIT_USAGE = 2


def _parse_int(text: str, *, low: int, high: int, what: str) -> int:
    """Parse a decimal or hex integer within bounds.

    :param text: Raw text (``17``, ``0x11``).
    :param low: Inclusive lower bound.
    :param high: Inclusive upper bound.
    :param what: Label used in error messages.
    :return: Parsed integer.
    """
    try:
        value = int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid {what}: {text!r}") from None
    if not low <= value <= high:
        raise argparse.ArgumentTypeError(f"{what} out of range {low}-{high}: {text!r}")
    return value


def _parse_register(text: str) -> Register:
    """Parse ``ram:0x01`` or ``eeprom:0x7D`` into a memory and an address.

    :param text: Register reference.
    :return: Memory area and address.
    """
    name, sep, raw_address = text.partition(":")
    try:
        memory = Memory(name.strip().lower())
    except ValueError:
        memory = None
    if not sep or memory is None:
        raise argparse.ArgumentTypeError(
            f"invalid register {text!r}; expected ram:<addr> or eeprom:<addr>"
        )
    address = _parse_int(raw_address.strip(), low=0, high=0xFF, what="address")
    return memory, address


def _parse_byte(text: str) -> int:
    return _parse_int(text, low=0, high=0xFF, what="value")


def _parse_interval(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid interval: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than 0")
    return value


def _label(register: Register) -> str:
    return f"{register[0].value}:0x{register[1]:02X}"


def _hex_key(address: int) -> str:
    return f"0x{address:02X}"


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _read_chunk(
    client: NavelClient, registers: Sequence[Register], *, strict: bool
) -> list[int | None]:
    """Read one chunk; on NACK or protocol error fall back to single reads.

    :param client: Navel client.
    :param registers: Registers of one chunk (at most ``_CHUNK_SIZE``).
    :param strict: Re-raise a failing single read instead of returning ``None``.
    :return: One value (or ``None`` when not strict) per register, in order.
    """
    try:
        return list(client.read([encode_item(mem, addr) for mem, addr in registers]))
    except (GatewayNackError, GatewayProtocolError):
        pass
    values: list[int | None] = []
    for memory, address in registers:
        try:
            values.append(client.read([encode_item(memory, address)])[0])
        except (GatewayNackError, GatewayProtocolError):
            if strict:
                raise
            values.append(None)
    return values


def _read_registers(client: NavelClient, registers: Sequence[Register]) -> list[int]:
    """Read registers in requests of at most ``_CHUNK_SIZE`` items (strict).

    :param client: Navel client.
    :param registers: Registers to read.
    :return: One value per register, in order.
    """
    values: list[int] = []
    for start in range(0, len(registers), _CHUNK_SIZE):
        chunk = _read_chunk(client, registers[start : start + _CHUNK_SIZE], strict=True)
        values.extend(value for value in chunk if value is not None)
    return values

def _load_snapshot(path: str) -> Snapshot:
    """Load a nested (``ram``/``eeprom``) or flat EEPROM-only snapshot.

    :param path: JSON file path.
    :return: Memory to address-to-value map.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    if "ram" in data or "eeprom" in data:
        blocks = {Memory.RAM: data.get("ram") or {}, Memory.EEPROM: data.get("eeprom") or {}}
    else:
        blocks = {Memory.EEPROM: data}
    snapshot: Snapshot = {}
    for memory, block in blocks.items():
        if not isinstance(block, dict):
            raise ValueError(f"{path}: {memory.value} must be a JSON object")
        snapshot[memory] = {int(key, 0): value for key, value in block.items()}
    return snapshot


def cmd_identify(args: argparse.Namespace, get_client: ClientGetter) -> int:
    """Print the module device type and revision.

    :param args: Parsed arguments.
    :param get_client: Lazy client getter.
    :return: Exit code.
    """
    identity = get_client().identify()
    _emit({"device_type": identity.device_type, "revision": identity.revision})
    return 0


def cmd_read(args: argparse.Namespace, get_client: ClientGetter) -> int:
    """Read registers and print ``{"ram:0x01": 34, ...}``.

    :param args: Parsed arguments (``registers``).
    :param get_client: Lazy client getter.
    :return: Exit code.
    """
    try:
        values = _read_registers(get_client(), args.registers)
    except GatewayError as exc:
        return _fail(str(exc))
    _emit({_label(reg): value for reg, value in zip(args.registers, values, strict=True)})
    return 0


def cmd_dump(args: argparse.Namespace, get_client: ClientGetter) -> int:
    """Dump RAM and EEPROM 0x00-0xFF to JSON (file or stdout).

    :param args: Parsed arguments (``out``).
    :param get_client: Lazy client getter.
    :return: Exit code.
    """
    client = get_client()
    dump: dict[str, Any] = {"taken_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    for memory in (Memory.RAM, Memory.EEPROM):
        block: dict[str, int | None] = {}
        for start in range(0, _ADDRESS_SPACE, _CHUNK_SIZE):
            addresses = range(start, start + _CHUNK_SIZE)
            chunk = _read_chunk(client, [(memory, a) for a in addresses], strict=False)
            for address, value in zip(addresses, chunk, strict=True):
                block[_hex_key(address)] = value
        dump[memory.value] = block
    if args.out is None:
        _emit(dump)
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dump, indent=2) + "\n", encoding="utf-8")
    nulls = sum(value is None for key in ("ram", "eeprom") for value in dump[key].values())
    _emit({"out": str(out), "ram": len(dump["ram"]), "eeprom": len(dump["eeprom"]), "null": nulls})
    return 0


def cmd_diff(args: argparse.Namespace, get_client: ClientGetter) -> int:
    """Print ``memory:0xNN old -> new`` for changed addresses (offline).

    :param args: Parsed arguments (``a``, ``b``, ``memory``).
    :param get_client: Unused; diff never touches the network.
    :return: Exit code.
    """
    try:
        old, new = _load_snapshot(args.a), _load_snapshot(args.b)
    except (OSError, ValueError) as exc:
        return _fail(f"cannot load snapshot: {exc}")
    memories = [Memory(args.memory)] if args.memory else [Memory.RAM, Memory.EEPROM]
    skipped = 0
    for memory in memories:
        old_block, new_block = old.get(memory, {}), new.get(memory, {})
        for address in sorted(old_block.keys() | new_block.keys()):
            before, after = old_block.get(address), new_block.get(address)
            if before is None or after is None:
                skipped += 1
            elif before != after:
                print(f"{_label((memory, address))} {before} -> {after}")
    if skipped:
        print(f"skipped {skipped} address(es) with a null or missing value", file=sys.stderr)
    return 0


def cmd_watch(args: argparse.Namespace, get_client: ClientGetter) -> int:
    """Print one row per interval until Ctrl+C, optionally appending to a CSV file.

    :param args: Parsed arguments (``registers``, ``interval``, ``csv``).
    :param get_client: Lazy client getter.
    :return: Exit code (0 on Ctrl+C).
    """
    client = get_client()
    header = ["timestamp", *(_label(reg) for reg in args.registers)]
    writers = [csv.writer(sys.stdout, lineterminator="\n")]
    csv_file = None
    if args.csv:
        path = Path(args.csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists() or path.stat().st_size == 0
        csv_file = path.open("a", newline="", encoding="utf-8")
        writers.append(csv.writer(csv_file, lineterminator="\n"))
        if is_new:
            writers[-1].writerow(header)
    writers[0].writerow(header)
    sys.stdout.flush()
    try:
        while True:
            started = time.monotonic()
            try:
                values = _read_registers(client, args.registers)
            except GatewayError as exc:
                print(f"error: {exc}", file=sys.stderr)
            else:
                row = [datetime.now().astimezone().isoformat(timespec="seconds"), *values]
                for writer in writers:
                    writer.writerow(row)
                sys.stdout.flush()
                if csv_file is not None:
                    csv_file.flush()
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        if csv_file is not None:
            csv_file.close()


def cmd_write(args: argparse.Namespace, get_client: ClientGetter) -> int:
    """Write one register after reading it; refuses without ``--confirm-attended``.

    :param args: Parsed arguments (``register``, ``value``, ``confirm_attended``).
    :param get_client: Lazy client getter (not called when the write is refused).
    :return: Exit code (2 when the confirmation flag is absent).
    """
    if not args.confirm_attended:
        print(
            "error: refusing to write without --confirm-attended "
            "(real writes must be attended); no request was sent",
            file=sys.stderr,
        )
        return _EXIT_USAGE
    client = get_client()
    memory, address = args.register
    item = encode_item(memory, address)
    before = client.read([item])[0]
    echoed = client.write([item], [args.value])[0]
    after = client.read([item])[0]
    _emit({"register": _label(args.register), "before": before, "written": echoed, "after": after})
    return 0


def cmd_clock(args: argparse.Namespace, get_client: ClientGetter) -> int:
    """Compare the stove clock (RAM 0x7A-0x80) with the PC clock.

    :param args: Parsed arguments.
    :param get_client: Lazy client getter.
    :return: Exit code.
    """
    registers = [(Memory.RAM, _CLOCK_START + offset) for offset in range(len(_CLOCK_FIELDS))]
    pc_before = datetime.now()
    values = _read_registers(get_client(), registers)
    pc_after = datetime.now()
    raw = dict(zip(_CLOCK_FIELDS, values, strict=True))
    try:
        stove = datetime(
            2000 + raw["year"], raw["month"], raw["day"], raw["hour"], raw["minute"], raw["second"]
        )
    except ValueError:
        raise GatewayProtocolError(f"Invalid stove clock registers: {raw}") from None
    pc = pc_before + (pc_after - pc_before) / 2
    _emit(
        {
            "stove_time": stove.isoformat(timespec="seconds"),
            "pc_time": pc.isoformat(timespec="seconds"),
            "drift_s": round((stove - pc).total_seconds()),
            "raw": raw,
        }
    )
    return 0


def _add_command(
    sub: Any, name: str, handler: Handler, help_text: str
) -> argparse.ArgumentParser:
    command = sub.add_parser(name, help=help_text, description=help_text)
    command.set_defaults(handler=handler)
    return command


def build_parser() -> argparse.ArgumentParser:
    """Build the ``stove-pilot`` argument parser.

    :return: Parser whose subcommands set a ``handler`` default.
    """
    parser = argparse.ArgumentParser(
        prog="stove-pilot", description="Discovery tools for the Navel stove module."
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    add = functools.partial(_add_command, sub)
    add("identify", cmd_identify, "Print the module device type and revision.")

    read = add("read", cmd_read, "Read registers, e.g. ram:0x01 eeprom:0x7D.")
    read.add_argument("registers", nargs="+", type=_parse_register, metavar="REGISTER")

    dump = add("dump", cmd_dump, "Dump RAM and EEPROM 0x00-0xFF as JSON.")
    dump.add_argument("--out", help="Output file (default: stdout).")

    diff = add("diff", cmd_diff, "Compare two dumps (offline); flat files are EEPROM maps.")
    diff.add_argument("a", help="Older dump.")
    diff.add_argument("b", help="Newer dump.")
    diff.add_argument("--memory", choices=[m.value for m in Memory], help="Limit to one memory.")

    watch = add("watch", cmd_watch, "Print register values every interval until Ctrl+C.")
    watch.add_argument("registers", nargs="+", type=_parse_register, metavar="REGISTER")
    watch.add_argument("--interval", type=_parse_interval, default=10.0, help="Seconds (10).")
    watch.add_argument("--csv", help="Append rows to this CSV file.")

    write = add("write", cmd_write, "Write one register (attended only).")
    write.add_argument("register", type=_parse_register, metavar="REGISTER")
    write.add_argument("value", type=_parse_byte, help="Decimal or hex, 0-255.")
    write.add_argument(
        "--confirm-attended", action="store_true", help="Required: confirms you are present."
    )

    add("clock", cmd_clock, "Compare the stove clock with the PC clock (drift in seconds).")
    return parser
