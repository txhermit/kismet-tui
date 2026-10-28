"""CLI entry:  python -m kismet_tui [--demo | --db FILE | --url URL]"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kismet-tui", description="Kismet terminal UI")
    ap.add_argument("--demo", action="store_true",
                    help="synthetic wardrive, no radio / no Kismet needed")
    ap.add_argument("--db", metavar="FILE", help="offline .kismet SQLite log")
    ap.add_argument("--url", default="http://localhost:2501",
                    help="Kismet REST base URL (default http://localhost:2501)")
    ap.add_argument("--user", default="", help="Kismet REST username")
    ap.add_argument("--password", default="", help="Kismet REST password")
    ap.add_argument("--refresh", type=float, default=2.0,
                    help="poll interval seconds (default 2)")
    args = ap.parse_args(argv)

    from .app import KismetTUI
    if args.demo:
        from .demo import DemoSource
        source, label = DemoSource(), "DEMO -- synthetic drive, San Antonio"
    elif args.db:
        from .client import KismetDB
        source, label = KismetDB(args.db), f"file {args.db}"
    else:
        from .client import KismetClient
        source = KismetClient(args.url, args.user, args.password)
        label = args.url
    KismetTUI(source, refresh=args.refresh, source_label=label).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
