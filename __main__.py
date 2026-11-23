"""Run by the assistant launcher: python -I <this zip> --kit-home <folder>. Puts this kit in place."""
import os
import sys

sys.path.insert(0, os.path.join(sys.argv[0], "plugins", "ml-seat-kit", "scripts"))
import kit_place  # noqa: E402

raise SystemExit(kit_place.main(sys.argv[1:] + ["--zip", os.path.abspath(sys.argv[0])]))
