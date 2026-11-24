"""Run by the assistant launcher: python -I <this zip> --kit-home <folder> puts this kit in place.
python -I <this zip> remove ... runs remove straight from the zip and never puts the kit in place (0.1.5)."""
import os
import sys

sys.path.insert(0, os.path.join(sys.argv[0], "plugins", "ml-seat-kit", "scripts"))
if sys.argv[1:2] == ["remove"]:
    import seat_setup  # noqa: E402
    raise SystemExit(seat_setup.main(sys.argv[1:]))
import kit_place  # noqa: E402

raise SystemExit(kit_place.main(sys.argv[1:] + ["--zip", os.path.abspath(sys.argv[0])]))
