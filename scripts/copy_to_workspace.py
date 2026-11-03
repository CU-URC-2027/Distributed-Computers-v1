#!/usr/bin/env python3
"""Copy this project's source snapshot into a conventional ROS workspace (no build artifacts)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('destination', nargs='?', default=str(Path.home()/'weeny_ws'))
args=parser.parse_args()
root=Path(__file__).resolve().parent.parent
destination=Path(args.destination).expanduser().resolve()
if destination==root or destination in root.parents:
    parser.error('Destination must be separate from the source project')
destination.mkdir(parents=True,exist_ok=True)
source_ws=root/'ros2_ws' if (root/'ros2_ws/src').is_dir() else root
skip={'__pycache__','.pytest_cache','build','install','log','arduino_secrets.h','wifi_credentials.h'}
manifest={}
def copy_tree(source,target):
    for p in source.rglob('*'):
        relative=p.relative_to(source)
        if any(part in skip or part.endswith('.egg-info') for part in relative.parts) or not p.is_file():
            continue
        out=target/relative
        out.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,out)
        manifest[str(out.relative_to(destination))]=hashlib.sha256(out.read_bytes()).hexdigest()
copy_tree(source_ws/'src',destination/'src')
for directory in ('firmware','reference','scripts'):
    copy_tree(root/directory,destination/directory)
for path in list(root.glob('*.md'))+[root/'teleop_controller.py']:
    shutil.copy2(path,destination/path.name)
    manifest[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
(destination/'SOURCE_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(f'Copied {len(manifest)} source/document files into {destination}')
