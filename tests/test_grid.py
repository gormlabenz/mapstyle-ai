# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

import unittest

from mapstyle_ai.grid import build_grid, column_name, crop_window, fitted_size


class GridTests(unittest.TestCase):
    def test_neighbor_cores_meet_but_inputs_overlap(self):
        tiles, rows, columns = build_grid((0, 0, 1536, 1536), 1)
        self.assertEqual((rows, columns), (2, 2))
        self.assertEqual([tile.name for tile in tiles], ['A1', 'B1', 'A2', 'B2'])
        a, b = tiles[:2]
        self.assertEqual(a.core[2], b.core[0])
        self.assertEqual(a.source[2] - b.source[0], 256)
        self.assertEqual(a.source[2] - a.source[0], 1024)
        self.assertEqual(tiles[0].core[1], tiles[2].core[3])

    def test_coverage_and_containment_on_incomplete_edges(self):
        bounds = (10, 20, 1010, 1020)
        cover, rows, columns = build_grid(bounds, 1)
        contained, inside_rows, inside_columns = build_grid(bounds, 1, contained=True)
        self.assertEqual((rows, columns), (2, 2))
        self.assertEqual((inside_rows, inside_columns), (1, 1))
        self.assertLessEqual(cover[-1].core[1], bounds[1])
        self.assertGreaterEqual(cover[-1].core[2], bounds[2])
        self.assertTrue(all(bounds[0] <= t.core[0] and bounds[1] <= t.core[1]
                            and t.core[2] <= bounds[2] and t.core[3] <= bounds[3]
                            for t in contained))

    def test_no_full_tile_and_limit(self):
        tiles, _, _ = build_grid((0, 0, 100, 100), 1, contained=True)
        self.assertEqual(tiles, [])
        with self.assertRaises(ValueError):
            build_grid((0, 0, 100000, 100000), 1)

    def test_float_precision_does_not_add_edge_tiles(self):
        tiles, rows, columns = build_grid((0, 0, 153.600000000001, 153.600000000001), 0.1)
        self.assertEqual((len(tiles), rows, columns), (4, 2, 2))

    def test_invalid_geometry_and_halo(self):
        for bounds, resolution, halo in [((0, 0, 1, 1), 0, 128),
                                         ((0, 0, 0, 1), 1, 128),
                                         ((0, 0, 1, float('inf')), 1, 128),
                                         ((0, 0, 1, 1), 1, 512)]:
            with self.assertRaises(ValueError):
                build_grid(bounds, resolution, halo=halo)

    def test_result_crop_excludes_context_and_preserves_resolution(self):
        tiles, _, _ = build_grid((0, 0, 1000, 1000), 1)
        window, bounds = crop_window(tiles[-1], 1, 128, 1024, (0, 0, 1000, 1000))
        self.assertEqual(window, (128, 128, 232, 232))
        self.assertEqual(bounds, (768, 0, 1000, 232))
        window, bounds = crop_window(tiles[-1], 1, 128, 1024)
        self.assertEqual(window, (128, 128, 768, 768))
        self.assertEqual(bounds, tiles[-1].core)

    def test_fractional_clip_snaps_outward_without_stretching(self):
        tiles, _, _ = build_grid((0.2, 0.2, 1000.5, 1000.5), 1)
        window, bounds = crop_window(tiles[-1], 1, 128, 1024, (0.2, 0.2, 1000.5, 1000.5))
        self.assertEqual(bounds[2] - bounds[0], window[2])
        self.assertEqual(bounds[3] - bounds[1], window[3])
        self.assertLessEqual(bounds[1], 0.2)
        self.assertLess(bounds[2] - 1000.5, 1)

    def test_metric_size_is_independent_of_pixels_halo_and_zoom(self):
        bounds = (0, 0, 11000, 7000)
        expected = None
        for resolution, pixels, halo in [(1, 1024, 128), (20, 2048, 128), (0.1, 1024, 64)]:
            tiles, rows, columns = build_grid(bounds, resolution, pixels, halo, tile_size=2000)
            self.assertEqual((rows, columns), (4, 6))
            cores = [tile.core for tile in tiles]
            if expected is None:
                expected = cores
            self.assertEqual(cores, expected)
            self.assertAlmostEqual(tiles[0].source[2] - tiles[0].source[0],
                             pixels * 2000 / (pixels - 2 * halo))

    def test_metric_origin_survives_area_expansion_and_negative_coordinates(self):
        small, _, _ = build_grid((-1100, -100, 1400, 1900), 1, tile_size=1000)
        large, _, _ = build_grid((-2500, -2200, 3500, 4200), 1, tile_size=1000)
        self.assertTrue({t.core for t in small} <= {t.core for t in large})
        self.assertTrue(all(v % 1000 == 0 for t in small for v in t.core))

    def test_metric_containment_and_exact_edges(self):
        tiles, rows, columns = build_grid((100, 100, 2100, 3100), 1,
                                          contained=True, tile_size=1000)
        self.assertEqual((rows, columns), (2, 1))
        self.assertTrue(all(100 <= t.core[0] and 100 <= t.core[1] and
                            t.core[2] <= 2100 and t.core[3] <= 3100 for t in tiles))
        _, rows, columns = build_grid((0, 0, 2000.00000001, 3000), 1, tile_size=1000)
        self.assertEqual((rows, columns), (3, 2))

    def test_metric_crop_retains_georeferencing(self):
        bounds = (100, 200, 11000, 7000)
        tiles, _, _ = build_grid(bounds, 1, tile_size=2000)
        resolution = 2000 / 768
        for tile in tiles:
            window, extent = crop_window(tile, resolution, 128, 1024, bounds)
            self.assertAlmostEqual(extent[2] - extent[0], window[2] * resolution)
            self.assertAlmostEqual(extent[3] - extent[1], window[3] * resolution)

    def test_metric_size_validation_and_limit(self):
        for side in [0, -1, float('inf'), float('nan'), 0.001]:
            with self.assertRaises(ValueError):
                build_grid((0, 0, 1000, 1000), 1, tile_size=side)

    def test_fit_square_to_fewer_or_more_tiles(self):
        bounds = (500100, 5300200, 500700, 5300800)
        for larger, expected in [(True, (600, 1, 1)), (False, (300, 2, 2))]:
            side, rows, columns = fitted_size(bounds, 400, larger)
            self.assertEqual((side, rows, columns), expected)
            tiles, actual_rows, actual_columns = build_grid(
                bounds, 1, tile_size=side, origin=(bounds[0], bounds[3]))
            self.assertEqual((actual_rows, actual_columns), (rows, columns))
            self.assertEqual(min(t.core[0] for t in tiles), bounds[0])
            self.assertEqual(min(t.core[1] for t in tiles), bounds[1])
            self.assertEqual(max(t.core[2] for t in tiles), bounds[2])
            self.assertEqual(max(t.core[3] for t in tiles), bounds[3])

    def test_fit_steps_and_limits(self):
        bounds = (0, 0, 600, 600)
        self.assertEqual(fitted_size(bounds, 600, False), (300, 2, 2))
        self.assertEqual(fitted_size(bounds, 300, True), (600, 1, 1))
        self.assertIsNone(fitted_size(bounds, 600, True))
        self.assertIsNone(fitted_size(bounds, 300, False, limit=4))
        for side in [0, -1, float('inf')]:
            with self.assertRaises(ValueError):
                fitted_size(bounds, side, True)

    def test_fit_portrait_uses_squares_and_covers_area(self):
        bounds = (100, 200, 800, 1300)
        side, rows, columns = fitted_size(bounds, 400, False)
        self.assertAlmostEqual(side, 1100 / 3)
        self.assertEqual((rows, columns), (3, 2))
        tiles, _, _ = build_grid(bounds, 1, tile_size=side, origin=(100, 1300))
        self.assertAlmostEqual(min(t.core[1] for t in tiles), 200)
        self.assertGreaterEqual(max(t.core[2] for t in tiles), 800)
        for tile in tiles:
            self.assertAlmostEqual(tile.core[2] - tile.core[0], tile.core[3] - tile.core[1])
        larger, rows, columns = fitted_size(bounds, 400, True)
        self.assertEqual((larger, rows, columns), (550, 2, 2))

    def test_fitted_fractional_size_keeps_pixel_geometry(self):
        bounds = (500100, 5300200, 501100, 5301200)
        side, rows, columns = fitted_size(bounds, 400, False)
        self.assertEqual((rows, columns), (3, 3))
        tiles, rows, columns = build_grid(bounds, 1, tile_size=side,
                                          origin=(bounds[0], bounds[3]))
        self.assertEqual((rows, columns), (3, 3))
        for tile in tiles:
            window, extent = crop_window(tile, side / 768, 128, 1024, bounds)
            self.assertEqual(window, (128, 128, 768, 768))
            self.assertAlmostEqual(extent[2] - extent[0], side)

    def test_excel_columns(self):
        self.assertEqual([column_name(i) for i in (0, 25, 26, 51, 52, 701, 702)],
                         ['A', 'Z', 'AA', 'AZ', 'BA', 'ZZ', 'AAA'])


if __name__ == '__main__':
    unittest.main()
