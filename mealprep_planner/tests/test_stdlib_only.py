import ast
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "mealprep"


class StdlibOnlyTest(unittest.TestCase):
    def test_imports(self):
        for path in PACKAGE.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    self.assertTrue(
                        top in sys.stdlib_module_names or top == "mealprep",
                        f"{path.name} imports non-stdlib module {name}",
                    )


if __name__ == "__main__":
    unittest.main()
