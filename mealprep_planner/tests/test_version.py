import re
import unittest
from pathlib import Path

from mealprep import VERSION


class VersionTest(unittest.TestCase):
    def test_config_yaml_matches_version(self):
        cfg = (Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^version:\s*"?([^"\s]+)"?\s*$', cfg, re.M).group(1), VERSION)


if __name__ == "__main__":
    unittest.main()
