"""Fix endpoint count in backend-boundaries.md from 23 to 21."""
import pathlib

p = pathlib.Path("docs/backend-boundaries.md")
content = p.read_text(encoding="utf-8")

old = "### \u5df2\u5b9e\u73b0\uff0823 \u4e2a\u7aef\u70b9\uff09"
new = "### \u5df2\u5b9e\u73b0\uff0821 \u4e2a\u7aef\u70b9\uff09"
if old in content:
    content = content.replace(old, new)
    p.write_text(content, encoding="utf-8")
    print("Fixed: 23 -> 21 endpoints")
else:
    print("Pattern not found, checking...")
    # Try to find the actual text
    for i, line in enumerate(content.splitlines(), 1):
        if "23" in line and "\u7aef\u70b9" in line:
            print(f"  Line {i}: {line}")
