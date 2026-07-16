import unittest

from measure_parser import parse_measure_ocr


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


if __name__ == "__main__":
    unittest.main()
