#!/usr/bin/env python3
"""
Build timestamp recorder pages for songs.

Usage:
  python tools/build_timestamp_recorder.py songs/silhouette/data.json
  python tools/build_timestamp_recorder.py --all

Copies the timestamp recorder template from experiments/timestamp-recorder/index.html
into songs/{slug}/timestamp-recorder/index.html. The recorder dynamically loads
../data.json at runtime, so the same HTML works for any song.
"""

import json
import os
import shutil
import sys
import glob

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO_ROOT, 'experiments', 'timestamp-recorder', 'index.html')


def build_for_song(data_json_path):
    """Copy the timestamp recorder template next to a song's data.json."""
    song_dir = os.path.dirname(os.path.abspath(data_json_path))

    # Verify data.json exists and has required fields
    with open(data_json_path) as f:
        data = json.load(f)

    slug = data.get('slug', os.path.basename(song_dir))
    youtube_id = data.get('youtube_id')
    sections = data.get('sections', [])
    has_lyrics = any(s.get('context_lines') for s in sections)

    if not youtube_id:
        print(f"  SKIP {slug}: no youtube_id in data.json")
        return False

    if not has_lyrics:
        print(f"  SKIP {slug}: no context_lines in any section")
        return False

    # Create timestamp-recorder directory
    recorder_dir = os.path.join(song_dir, 'timestamp-recorder')
    os.makedirs(recorder_dir, exist_ok=True)

    # Copy template
    dest = os.path.join(recorder_dir, 'index.html')
    shutil.copy2(TEMPLATE, dest)

    line_count = sum(len(s.get('context_lines', [])) for s in sections)
    print(f"  OK {slug}: {len(sections)} sections, {line_count} lyric lines → {os.path.relpath(dest, REPO_ROOT)}")
    return True


def main():
    if not os.path.exists(TEMPLATE):
        print(f"ERROR: Template not found at {TEMPLATE}")
        sys.exit(1)

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == '--all':
        # Find all data.json files under songs/
        pattern = os.path.join(REPO_ROOT, 'songs', '*', 'data.json')
        data_files = sorted(glob.glob(pattern))
        if not data_files:
            print("No data.json files found under songs/")
            sys.exit(1)

        print(f"Building timestamp recorders for {len(data_files)} songs:\n")
        built = 0
        for df in data_files:
            if build_for_song(df):
                built += 1

        print(f"\nDone: {built}/{len(data_files)} recorders built.")
    else:
        data_json_path = sys.argv[1]
        if not os.path.exists(data_json_path):
            print(f"ERROR: {data_json_path} not found")
            sys.exit(1)
        build_for_song(data_json_path)


if __name__ == '__main__':
    main()
