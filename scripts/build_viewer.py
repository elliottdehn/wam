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
    tpl = open(os.path.join(root, "viewer", "template.html")).read()
    d = open(json_path).read()
    name = title or json.loads(d).get("name", "model").capitalize()
    h = tpl.replace("<title>WAM Viewer</title>",
                    "<title>WAM Viewer — %s</title>" % name)
    h = h.replace("null /*__DATA__*/", d)
    open(out_path, "w").write(h)
    return out_path


if __name__ == "__main__":
    p = build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    print("built", p)
