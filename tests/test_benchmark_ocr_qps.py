import unittest

from benchmark_ocr_qps import core_affinity_mask, percentile, summarize_run


class BenchmarkOCRQPSTest(unittest.TestCase):
    def test_core_affinity_mask_uses_lowest_n_logical_cores(self):
        self.assertEqual(core_affinity_mask(1), 0b1)
        self.assertEqual(core_affinity_mask(4), 0b1111)

    def test_percentile_interpolates_between_sorted_values(self):
        self.assertEqual(percentile([10.0, 20.0, 30.0, 40.0], 50), 25.0)
        self.assertEqual(percentile([10.0, 20.0, 30.0, 40.0], 95), 38.5)

    def test_summarize_run_calculates_qps_and_latency_metrics(self):
        summary = summarize_run(
            core_count=2,
            concurrency=4,
            elapsed_seconds=2.0,
            ok_latencies=[0.10, 0.20, 0.30],
            error_count=1,
        )

        self.assertEqual(summary["core_count"], 2)
        self.assertEqual(summary["concurrency"], 4)
        self.assertEqual(summary["requests"], 4)
        self.assertEqual(summary["success"], 3)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["qps"], 1.5)
        self.assertEqual(summary["success_rate"], 75.0)
        self.assertEqual(summary["avg_ms"], 200.0)
        self.assertEqual(summary["p50_ms"], 200.0)
        self.assertEqual(summary["p95_ms"], 290.0)


if __name__ == "__main__":
    unittest.main()
