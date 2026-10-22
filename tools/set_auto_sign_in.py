"""Open the program without the login window on THIS workstation (or go back to the normal login).

    .venv/Scripts/python.exe tools/set_auto_sign_in.py on [username]     (default username: admin)
    .venv/Scripts/python.exe tools/set_auto_sign_in.py off
    .venv/Scripts/python.exe tools/set_auto_sign_in.py status

The choice is local to this Windows user (not in the database). The account must still exist, be active and be allowed to open the program; passwords stay in the database for مراجعات المدير and the
other checks that ask for them."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.workstation_settings import auto_sign_in_user, set_auto_sign_in_user


def main(argv):
    command = argv[0] if argv else ""
    if command == "on":
        name = argv[1] if len(argv) > 1 else "admin"
        set_auto_sign_in_user(name)
        print(f"on: the program opens as «{name}» without the login window")
    elif command == "off":
        set_auto_sign_in_user("")
        print("off: the normal login window is back")
    elif command == "status":
        print("on: " + auto_sign_in_user() if auto_sign_in_user() else "off")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
