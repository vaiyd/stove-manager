"""``stove-pilot`` console entrypoint (thin shim over ``service.cli``)."""

import sys
from collections.abc import Sequence

from pydantic import ValidationError

from stove_pilot.common.exceptions import StovePilotError
from stove_pilot.common.logging import SecretRedactionFilter, configure_logging
from stove_pilot.common.settings import Settings, get_settings
from stove_pilot.lib.navel import NavelClient
from stove_pilot.service.cli.commands import build_parser
from stove_pilot.service.composition import build_navel_client


class _ClientHolder:
    """Build the Navel client on first use and own its lifetime."""

    def __init__(self, settings: Settings) -> None:
        """Store settings; nothing is built yet.

        :param settings: Application settings.
        """
        self._settings = settings
        self._client: NavelClient | None = None

    def __call__(self) -> NavelClient:
        """Return the shared client, building it on first call.

        :return: Navel client.
        """
        if self._client is None:
            self._client = build_navel_client(self._settings)
        return self._client

    def close(self) -> None:
        """Close the client if it was built."""
        if self._client is not None:
            self._client.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the discovery CLI.

    :param argv: Arguments (default: ``sys.argv[1:]``).
    :return: Process exit code.
    """
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    try:
        settings = get_settings()
    except ValidationError as exc:
        fields = ", ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors())
        print(f"error: invalid configuration ({fields})", file=sys.stderr)
        return 1
    SecretRedactionFilter.register(settings.navel_security.get_secret_value())
    configure_logging("WARNING")
    holder = _ClientHolder(settings)
    try:
        return args.handler(args, holder)
    except StovePilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        holder.close()


if __name__ == "__main__":
    sys.exit(main())
