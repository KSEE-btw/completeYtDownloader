#!/usr/bin/env python3
"""Print a human table of the simplified format list for a URL.

    python scripts/show_formats.py URL
    python scripts/show_formats.py --json PATH
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

if len(sys.argv) > 2 and sys.argv[1] == "--json":
    data = json.load(open(sys.argv[2], encoding="utf-8-sig"))
else:
    from ytgrab.domain import formats
    from ytgrab.services import catalog
    data = formats.simplify_formats(catalog.run_ytdlp_json(sys.argv[1]))

print(f"{data['title']}  id={data['video_id']}  dur={data['duration']}s")
print(f"{'kind':6} {'h':>5} {'fps':>5} {'ext':4} {'video':7} {'audio':7} "
      f"{'compat':10} {'MB':>8}  id")
for f in data["formats"]:
    mb = f"{f['size']/1048576:.1f}" if f["size"] else "-"
    print(f"{f['kind']:6} {str(f['height'] or '-'):>5} {str(f['fps'] or '-'):>5} "
          f"{f['ext']:4} {str(f['vcodec']):7} {str(f['acodec']):7} "
          f"{f['compat']:10} {mb:>8}  {f['id']}")
