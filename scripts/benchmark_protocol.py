"""Synthetic parser/registry benchmark; not a wallpad latency benchmark.

Pass a repository path to compare revisions using identical HA boundary doubles.
"""
import argparse
import cProfile
import json
from pathlib import Path
import statistics
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--profile', action='store_true')
args = parser.parse_args()
sys.path.insert(0, str(args.repo / 'tests'))
from support import gateway, packet

frames = [packet(payload=(255, 0, 255, 0, 0, 0, 0, 0)),
          packet(payload=(0, 255, 0, 0, 0, 0, 0, 0)),
          packet(0x36, (0x10, 0, 24, 0, 22)),
          packet(0x48, (0x11, 1, 0x40, 0, 5, 20)),
          packet(0x44, (3, 0, 0)), packet(0x98, (10, 5, 1, 244, 0, 5, 22, 40))]
chunk = b''.join(frames)

def run():
    g = gateway()
    for _ in range(5000):
        g.controller.feed(chunk)
    assert not g.stats["packet_errors"], dict(g.stats)
    assert len(g.registry._states) == 16
    return g

if args.profile:
    cProfile.run('run()', sort='cumtime')
else:
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        g = run()
        samples.append(time.perf_counter() - start)
    snapshot = sorted((d.key.unique_id, str(d.state), str(d.attribute))
                      for d in g.registry._states.values())
    print(json.dumps({'frames':30000, 'median_seconds':statistics.median(samples),
                      'samples':samples, 'final_states':snapshot}))
