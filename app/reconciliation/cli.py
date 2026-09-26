import argparse
from collections import Counter
from pathlib import Path

from app.reconciliation.files import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.reconciliation.cli")
    parser.add_argument("--locations", type=Path, required=True)
    parser.add_argument("--system-a", type=Path, required=True)
    parser.add_argument("--system-b", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args()

    reconciled, exceptions = run(args.locations, args.system_a, args.system_b, args.out)

    print(
        f"records: {len(reconciled)}  "
        + "  ".join(f"{status}={n}" for status, n in sorted(Counter(r["status"] for r in reconciled).items()))
    )
    print(f"exceptions: {len(exceptions)}")
    for (severity, code, org), n in sorted(
        Counter((e["severity"], e["reason_code"], e["org"] or "(no org)") for e in exceptions).items()
    ):
        print(f"  {severity:<8} {code:<26} {org:<10} {n}")
    print(f"written: {args.out / 'reconciled.csv'}, {args.out / 'exceptions.csv'}")


if __name__ == "__main__":
    main()
