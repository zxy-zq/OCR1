import unittest

from measure_parser import debug_parse_measure_ocr, parse_measure_ocr


class MeasureParserTest(unittest.TestCase):
    def test_extracts_4g_fields_and_joins_split_cell_id(self):
        lines = [
            "网络 LTE",
            "位置 107.77353/31.5321",
            "ECl 139146511",
            "(543541-15)",
            "RSRP -76 dBm",
        ]

        self.assertEqual(
            parse_measure_ocr(lines),
            {
                "network": "LTE",
                "location": "107.77353/31.5321",
                "cell_id": "139146511(543541-15)",
                "signal": -76,
            },
        )

    def test_extracts_5g_fields_with_nr_cl_misread(self):
        lines = [
            "NR-Cl 38698274839",
            "(9447821-23)",
            "位置: 107.7237/31.35492",
            "SS-RSRP -110",
        ]

        self.assertEqual(
            parse_measure_ocr(lines),
            {
                "network": "NR",
                "location": "107.7237/31.35492",
                "cell_id": "38698274839(9447821-23)",
                "signal": -110,
            },
        )


    def test_extracts_signal_when_rsrp_value_is_on_next_line(self):
        result = parse_measure_ocr(["LTE", "RSRP", "-111"])

        self.assertEqual(result["signal"], -111)

    def test_extracts_signal_when_colon_is_split_from_value(self):
        result = parse_measure_ocr(["LTE", "RSRP", ":", "-105 dBm"])

        self.assertEqual(result["signal"], -105)

    def test_extracts_signal_with_dbm_label_and_split_minus(self):
        result = parse_measure_ocr(["LTE", "RSRP(dBm)", "- 105"])

        self.assertEqual(result["signal"], -105)

    def test_signal_is_empty_when_rsrp_is_absent(self):
        result = parse_measure_ocr(["LTE", "SINR", "15", "RSRQ", "-8"])

        self.assertEqual(result["signal"], "")

    def test_signal_uses_rsrp_context_not_any_negative_number(self):
        result = parse_measure_ocr(["LTE", "RSRQ", "-8", "SINR", "15", "RSRP", "-105"])

        self.assertEqual(result["signal"], -105)

    def test_extracts_signal_from_lte_metric_table(self):
        result = parse_measure_ocr(
            ["信号强度", "RSSI", "RSRP", "RSRQ", "SINR", "-77", "-76", "-5", "1.2"]
        )

        self.assertEqual(result["signal"], -76)

    def test_extracts_signal_from_lte_metric_table_with_rxlev(self):
        result = parse_measure_ocr(
            [
                "信号强度",
                "RSSI",
                "RSRP",
                "RSRQ",
                "SINR",
                "RXLEV",
                "-73",
                "-137",
                "-4",
                "24.0",
                "-65",
            ]
        )

        self.assertEqual(result["signal"], -137)

    def test_extracts_signal_from_5g_split_ss_metric_table(self):
        result = parse_measure_ocr(
            [
                "NR-CI 38907699250",
                "信号强度",
                "SS",
                "SS",
                "SS",
                "RSRP",
                "RSRQ",
                "SINR",
                "-92",
                "-10",
                "12",
            ]
        )

        self.assertEqual(result["signal"], -92)

    def test_debug_parse_reports_empty_signal_reason(self):
        debug = debug_parse_measure_ocr(["LTE", "SINR", "15"])

        self.assertEqual(debug["parsed"]["signal"], "")
        self.assertEqual(debug["raw_lines"], ["LTE", "SINR", "15"])
        self.assertIn("signal", debug["empty_reasons"])


if __name__ == "__main__":
    unittest.main()
