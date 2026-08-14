"""Build a standalone viewer page with a model preloaded.

Usage: python3 scripts/build_viewer.py <viewer.json> <out.html> [title]
(The bare template at viewer/template.html also works standalone:
open it in a browser and drop any *_viewer.json onto it.)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build(json_path, out_path, title=None):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Explicit contexts keep repeated agent builds from leaking file handles;
    # UTF-8 also makes the generated page independent of the Windows locale.
    with open(os.path.join(root, "viewer", "template.html"),
              encoding="utf-8") as source:
        tpl = source.read()
    with open(json_path, encoding="utf-8") as source:
        d = source.read()
    name = title or json.loads(d).get("name", "model").capitalize()
    h = tpl.replace("<title>WAM Viewer</title>",
                    "<title>WAM Viewer — %s</title>" % name)
    h = h.replace("null /*__DATA__*/", d)
    with open(out_path, "w", encoding="utf-8", newline="\n") as output:
        output.write(h)
    return out_path


if __name__ == "__main__":
    p = build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    print("built", p)
