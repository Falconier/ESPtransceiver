# Olivia beacon at ~30 m, boards with heat sinks (2026-11-09)

The beacon (Olivia 16/500, `DE W4DQB/EXP`, 2304.350 MHz) was about 100 ft (30 m) away in the tree, as in the
earlier weak-signal test. Both station boards had heat sinks fitted. Each run is
`scripts/beacon_check.py - PORT 100 - olivia-16/500` (receive only) with `RX_SHIFT=2` and the gain shown;
`sweep.sh PORT TAG GAIN...` runs a list of gains.

## Minimum receive gain

| Gain | Board A (COM4) | Board B (COM5) |
|---|---|---|
| 0 | 0 IDs (150 s) | not run |
| 10 | 0 | 0 |
| 12–18 | 0 | not run |
| **20** | **3/3 clean, four runs** | 0 |
| 22–28 | not run | 0 (one fragment, `/EXP`, at 26) |
| **30** | 3/3 clean | **3/3 clean, four runs** |
| 40 | full IDs | 5/5 clean (150 s) |

- Minimum gain at this distance: **20 on board A, 30 on board B**. Both held 12/12 IDs over four runs.
- The cutoff is sharp: every ID clean at the threshold, nothing one step (2) below it.
- Board A is about 10 gain steps more sensitive than board B here. This could be the boards themselves, their
  antennas' orientation or their USB cables; not yet separated.
- The earlier test from the same spot, without heat sinks, decoded about one clean ID in three. One session
  cannot tell whether the heat sinks or the conditions made the difference.
