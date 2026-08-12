"""Not a test -- `python -m tools` (from simple/) prints each registered
tool's argv for a sample bucket, useful when sketching out a new plan by hand.
"""

from __future__ import annotations

from tools import TOOLS

for name in sorted(TOOLS):
    if TOOLS[name].get("native", "stdout") == "stdout":
        print(name, TOOLS[name]["argv"]("example-bucket", "some/prefix/"))
    else:
        print(name, TOOLS[name]["argv"]("example-bucket", "some/prefix/", "/tmp/attempt"))
