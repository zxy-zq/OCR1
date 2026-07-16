import unittest

from benchmark_ocr_qps import (
    build_server_command,
    build_server_env,
    core_affinity_mask,
    markdown_table,
    percentile,
    summarize_run,
)


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
            workers=2,
            ort_threads=1,
            det_limit_side_len=512,
            det_model_type="tiny",
        )

        self.assertEqual(summary["core_count"], 2)
        self.assertEqual(summary["workers"], 2)
        self.assertEqual(summary["ort_threads"], 1)
        self.assertEqual(summary["det_limit_side_len"], 512)
        self.assertEqual(summary["det_model_type"], "tiny")
        self.assertEqual(summary["concurrency"], 4)
        self.assertEqual(summary["requests"], 4)
        self.assertEqual(summary["success"], 3)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["qps"], 1.5)
        self.assertEqual(summary["success_rate"], 75.0)
        self.assertEqual(summary["avg_ms"], 200.0)
        self.assertEqual(summary["p50_ms"], 200.0)
        self.assertEqual(summary["p95_ms"], 290.0)

    def test_build_server_command_sets_worker_count(self):
        command = build_server_command(port=9876, workers=3)

        self.assertIn("--workers", command)
        self.assertEqual(command[command.index("--workers") + 1], "3")

    def test_build_server_env_sets_ocr_runtime_controls(self):
        env = build_server_env(
            base_env={},
            core_count=4,
            ort_threads=2,
            max_concurrency=3,
            preload=True,
            det_limit_side_len=512,
            det_model_type="tiny",
        )

        self.assertEqual(env["OMP_NUM_THREADS"], "4")
        self.assertEqual(env["OCR_ORT_INTRA_THREADS"], "2")
        self.assertEqual(env["OCR_MAX_CONCURRENCY"], "3")
        self.assertEqual(env["OCR_PRELOAD"], "true")
        self.assertEqual(env["OCR_DET_LIMIT_SIDE_LEN"], "512")
        self.assertEqual(env["OCR_DET_MODEL_TYPE"], "tiny")

    def test_markdown_table_uses_readable_chinese_headers(self):
        row = summarize_run(
            core_count=8,
            concurrency=2,
            elapsed_seconds=1.0,
            ok_latencies=[0.1],
            error_count=0,
            det_limit_side_len=512,
        )

        table = markdown_table([row])

        self.assertIn("CPU核心数", table)
        self.assertIn("检测尺寸", table)
        self.assertNotIn("鏍稿績", table)


if __name__ == "__main__":
    unittest.main()
