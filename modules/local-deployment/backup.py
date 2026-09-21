#!/usr/bin/env python3
"""Create a new archive of explicitly selected, quiescent local state directories.

Stop writers or use the database's native export before running. A Colima volume
must first be exported from the VM; an empty host directory is not a VM backup.
"""
import argparse
from pathlib import Path
import tarfile

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('directories',nargs='+',type=Path)
    args=parser.parse_args()
    paths=[p.expanduser().resolve() for p in args.directories]
    if any(not p.is_dir() or not any(p.iterdir()) for p in paths):
        parser.error('Every source must be a populated directory; verify VM data paths.')
    if len({p.name for p in paths})!=len(paths):parser.error('Source directory names must be unique.')
    destination=args.output.expanduser().resolve()
    if any(p==destination or p in destination.parents for p in paths):parser.error('Archive must be outside source directories.')
    with destination.open('xb') as stream:
        destination.chmod(0o600)
        with tarfile.open(fileobj=stream,mode='w:gz') as archive:
            for path in paths:archive.add(path,arcname=path.name)
    print('Private backup created: '+str(destination))
