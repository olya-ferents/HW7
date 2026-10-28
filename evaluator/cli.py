import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def parse(argv):
    p = argparse.ArgumentParser(
        prog="docker compose -f evaluator/docker-compose.yml run --rm eval",
        description="Run the L05 set against the PayPilot stand.")
    p.add_argument("--profile", metavar="NAME",
                   help="switch the stand to this profile before the run")
    p.add_argument("--only", metavar="IDS",
                   help="run only these cases, comma-separated")
    p.add_argument("--set", dest="set_name", metavar="NAME",
                   help="read <NAME>.jsonl from the repository root")
    p.add_argument("--gate", choices=["daily", "release", "all"],
                   help="which gate's cases to run, daily by default")
    p.add_argument("--summarize-after", type=int, metavar="N",
                   help="set the summarize threshold (steps) before every case; "
                        "overrides summarize_after in the cases")
    p.add_argument("--repeat", type=int, metavar="N",
                   help="run every selected case N times (overrides runs in the case)")
    p.add_argument("--traces", action="store_true",
                   help="save the full trace of every turn to reports/traces/<stamp>/")
    p.add_argument("--brief", action="store_true",
                   help="one line per case, without the case details")
    p.add_argument("--dry-run", action="store_true",
                   help="print the set hash and coverage, call nothing")
    return p.parse_args(argv)


def main(argv):
    if argv and not argv[0].startswith("-"):
        program = sys.executable if argv[0] == "python" else argv[0]
        return subprocess.call([program, *argv[1:]])

    args = parse(argv)
    if args.only is not None:
        os.environ["CASE"] = args.only
    if args.set_name is not None:
        os.environ["SET"] = args.set_name
    if args.gate is not None:
        os.environ["GATE"] = "" if args.gate == "all" else args.gate

    if args.summarize_after is not None:
        os.environ["SUMMARIZE_AFTER"] = str(args.summarize_after)

    if args.repeat is not None:
        os.environ["REPEAT"] = str(args.repeat)
    if args.traces:
        os.environ["TRACES"] = "1"

    if args.brief:
        os.environ["BRIEF"] = "1"

    if args.dry_run:
        return subprocess.call([sys.executable, str(HERE / "validate.py")])

    import stand

    if args.profile is not None:
        try:
            stand.wait_until_ready()
            stand.set_profile(args.profile)
        except stand.StandError as e:
            print(e)
            return 2

    import runner

    return runner.main()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
