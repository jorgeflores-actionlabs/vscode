import argparse
import json
from pathlib import Path

import requests


DEFAULT_URL = "https://dova.cloudata.solutions/lv/api/DID"
DEFAULT_INPUT_FILE = Path(__file__).resolve().parents[2] / "app" / "input.txt"
REQUEST_TIMEOUT_SECONDS = 30


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a DID array to the LV DID API endpoint."
    )
    parser.add_argument(
        "did",
        nargs="*",
        type=int,
        help="Optional DID values. Defaults to DealerIds read from input.txt.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"POST endpoint. Default: {DEFAULT_URL}",
    )
    parser.add_argument(
        "--wrapped",
        action="store_true",
        help='Send {"did": [...]} instead of the default raw array body.',
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help=f"Input file used when DID values are not provided. Default: {DEFAULT_INPUT_FILE}",
    )
    return parser.parse_args()


def read_dids(input_file: Path) -> list[int]:
    dids: list[int] = []

    with input_file.open(encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue

            dealer_id_text = line.split("|", maxsplit=1)[0].strip()
            try:
                dids.append(int(dealer_id_text))
            except ValueError as error:
                raise ValueError(
                    f"Line {line_number}: DealerId must be an integer."
                ) from error

    if not dids:
        raise ValueError(f"No DealerIds found in {input_file}.")

    return dids


def build_payload(dids: list[int], wrapped: bool):
    if wrapped:
        return {"did": dids}
    return dids


def post_dids(url: str, payload) -> requests.Response:
    return requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)


def main() -> int:
    args = parse_arguments()
    dids = args.did or read_dids(args.input_file)
    payload = build_payload(dids, args.wrapped)

    if not args.did:
        print(f"Reading DealerIds from {args.input_file}")
    print(f"POST {args.url}")
    print(json.dumps(payload, indent=2))

    response = post_dids(args.url, payload)
    print(f"HTTP {response.status_code}")

    try:
        print(json.dumps(response.json(), indent=2))
    except ValueError:
        print(response.text)

    if not response.ok:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
