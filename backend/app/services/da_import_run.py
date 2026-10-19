"""Run one DirectAdmin import in its own process.

The panel used to run imports in a ThreadPoolExecutor inside spanel-api. A
multi-GB import takes minutes, and anything that restarts that service during
one - an update, a crash, an operator - killed it halfway. What was left was a
half-created account: the panel user and the Linux user existed, the website,
the database and the files did not, and the job record was an in-memory dict
that went with the process, so the page had nothing left to report.

Systemd starts this instead, through the helper, and it outlives spanel-api.
The unit's own state is the job state, which is why it survives a restart:
there is nothing in memory to lose.
"""

import logging
import os
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: da_import_run.py <archive> [force]", file=sys.stderr)
        return 2

    # Settings reads .env relative to the working directory, and the spanel user
    # cannot traverse the directory systemd would otherwise leave us in.
    os.chdir("/opt/spanel/backend")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    from app.services import da_import

    archive = argv[0]
    force = len(argv) > 1 and argv[1] == "force"
    print(f"importing {archive} force={force}", flush=True)
    try:
        result = da_import.import_da_backup(archive, force=force)
    except Exception as exc:  # the unit's exit code is what the panel reads
        logging.exception("import failed")
        print(f"FAILED: {exc}", flush=True)
        return 1
    print(f"RESULT: {result}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
