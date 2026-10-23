#!/usr/bin/env python3
"""Local test double only. Never contacts Apple or accepts real credentials."""
import getpass
import json
import os
from pathlib import Path

counter = Path(os.environ["FINKA_TEST_COUNTER"])
number = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(number))
input("enter Apple ID (email address or phone number): ")
getpass.getpass("enter password: ")
code = input("enter 2FA code: ")
if os.environ.get("FINKA_TEST_WRONG_FIRST") == "1" and number == 1:
    print(json.dumps({"success": False, "error": "Incorrect verification code " + code}), flush=True)
    raise SystemExit(1)
print(json.dumps({"success": True}), flush=True)
