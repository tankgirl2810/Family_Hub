"""Run with:  python -m bookhub            (uses the folders in your settings)
          python -m bookhub --demo     (builds a small sample library first)
          python -m bookhub --resort-library
                                       (sorts the whole library again, once, then stops; see review.py)
"""
import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="bookhub", description="Family Hub server")
    parser.add_argument("--demo", action="store_true", help="create and use a small sample library")
    parser.add_argument("--demo-dir", default="./demo", help="where the sample library and data go")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--resort-library", action="store_true",
                        help="sort every library book again (reads PDFs' first pages), fill the review queue, then stop")
    args = parser.parse_args()
    if args.resort_library:
        # a one-off run next to the real server: its background jobs must not start a second time
        os.environ["BACKGROUND_JOBS"] = "false"

    if args.demo:
        from . import demo
        folders = demo.build(args.demo_dir)
        os.environ["CALIBRE_LIBRARY"] = str(folders["library"])
        os.environ["BOOKHUB_DATA_DIR"] = str(folders["data"])
        os.environ.setdefault("GUEST_LAN_ONLY", "true")
        os.environ.setdefault("TAG_LOOKUPS", "off")  # the sample library stays offline

    from . import create_app
    from .config import Config

    config = Config()
    if args.port:
        config.port = args.port
    app = create_app(config)
    if args.resort_library:
        resort_library(app)
        return
    if args.demo:
        from . import demo
        with app.app_context():
            demo.seed(app.hub)
        print("\nDemo profiles: 'You' (admin, passcode 1234), 'Partner' (admin, 1234), 'Nan' (member, 1234)")
    if not app.hub.library.available:
        print("Warning: no Calibre library found at %s (metadata.db is missing)." % config.library_dir, file=sys.stderr)
    print("Family Hub is running: http://localhost:%d\n" % config.port)
    try:
        from waitress import serve
        serve(app, host=args.host, port=config.port, threads=8)
    except ImportError:
        app.run(host=args.host, port=config.port)


def resort_library(app):
    """Sort the whole library again, saying how far it has got. The queue on Fix book details fills with it."""
    def say(done, total):
        if done % 50 == 0 or done == total:
            print("Sorted %d of %d books" % (done, total), flush=True)

    with app.app_context():
        looked = app.hub.review.resort(progress=say)
        waiting = len(app.hub.review.items())
    print("Done: %d books sorted again, %d waiting in the review queue." % (looked, waiting))


if __name__ == "__main__":
    main()
