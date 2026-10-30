# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

import unittest

from mapstyle_ai.grid import build_grid
from mapstyle_ai.seams import dependencies, ready_tiles, blocked_tiles, overlap_windows, continuation_prompt, DEFAULT_PROMPT


class SeamPlanTests(unittest.TestCase):
    def setUp(self):
        self.tiles, _, _ = build_grid((0, 0, 1536, 1536), 1)

    def test_diagonal_frontier_waits_for_both_neighbors(self):
        parents = dependencies(self.tiles)
        self.assertEqual(parents, {'A1': [], 'B1': ['A1'], 'A2': ['A1'], 'B2': ['B1', 'A2']})
        names = [t.name for t in self.tiles]
        self.assertEqual(ready_tiles(names, parents, set()), ['A1'])
        self.assertEqual(ready_tiles(names[1:], parents, {'A1'}), ['B1', 'A2'])
        self.assertEqual(ready_tiles(['B2'], parents, {'A1', 'B1'}), [])
        self.assertEqual(ready_tiles(['B2'], parents, {'A1', 'B1', 'A2'}), ['B2'])

    def test_existing_anchor_continues_partial_selection(self):
        parents = dependencies(self.tiles[1:], {'A1': {'row': 0, 'column': 0}})
        self.assertEqual(parents['B1'], ['A1'])
        self.assertEqual(parents['A2'], ['A1'])

    def test_disconnected_starts_are_rejected_before_any_requests(self):
        with self.assertRaises(ValueError):
            dependencies([self.tiles[0], self.tiles[3]])

    def test_neighbor_failure_blocks_all_descendants(self):
        parents = dependencies(self.tiles)
        self.assertEqual(blocked_tiles(['B1', 'A2', 'B2'], parents, {'A1'}), {'B1', 'A2', 'B2'})
        self.assertEqual(blocked_tiles(['B2'], parents, {'B1'}), {'B2'})

    def test_full_neighbor_overlaps_halo_and_new_core(self):
        first, right, down, corner = self.tiles
        target, source = overlap_windows(right.source, first.source, 1)
        self.assertEqual(target, (0, 0, 256, 1024))
        self.assertEqual(source, (768, 0, 256, 1024))
        self.assertEqual(overlap_windows(down.source, first.source, 1),
                         ((0, 0, 1024, 256), (0, 768, 1024, 256)))
        self.assertEqual(overlap_windows(corner.source, right.source, 1)[0], (0, 0, 1024, 256))
        self.assertEqual(overlap_windows(corner.source, down.source, 1)[0], (0, 0, 256, 1024))

    def test_overlap_preserves_fractional_crs_precision(self):
        tiles, _, _ = build_grid((560000, 6000000, 560153.6, 6000153.6), 0.1)
        target, source = overlap_windows(tiles[1].source, tiles[0].source, 0.1)
        self.assertEqual(target, (0, 0, 256, 1024))
        self.assertEqual(source, (768, 0, 256, 1024))

    def test_per_tile_prompt_names_reference_and_locked_pixels(self):
        prompt = continuation_prompt('General style reference', ['A1'], [(0, 0, 256, 1024)])
        self.assertNotIn('Image 3', prompt)
        self.assertIn('already embedded in Image 1', prompt)
        self.assertIn('A1', prompt)
        self.assertIn('width=256', prompt)
        self.assertIn('unchanged', prompt)
        self.assertIn('entire base map', continuation_prompt('General', [], []))

    def test_start_prompt_has_no_neighbor_or_crop_instructions(self):
        prompt = continuation_prompt(DEFAULT_PROMPT, [], [])
        self.assertTrue(prompt.startswith('Illustrate the entire base map'))
        for word in ('strips', 'neighbor', 'context margin', 'core', 'top-down'):
            self.assertNotIn(word, prompt)
        self.assertIn('{tile_px}', prompt)
        self.assertIn('spatial arrangement from Image 1', prompt)

    def test_direction_matches_actual_overlap_even_with_one_top_neighbor(self):
        cases = [(['A1'], [(0, 0, 256, 1024)], 'left'),
                 (['A1'], [(0, 0, 1024, 256)], 'top'),
                 (['B1', 'A2'], [(0, 0, 1024, 256), (0, 0, 256, 1024)], 'top and left')]
        for neighbors, windows, direction in cases:
            with self.subTest(direction=direction):
                prompt = continuation_prompt('My custom addition', neighbors, windows)
                self.assertTrue(prompt.startswith(f'Continue the existing illustration at the {direction} of Image 1'))
                self.assertIn('My custom addition', prompt)
                self.assertIn('take priority over style references', prompt)
                self.assertIn('without gaps or offsets', prompt)
                self.assertEqual(prompt.count('Unchanged strip in Image 1:'), len(neighbors))


if __name__ == '__main__':
    unittest.main()
