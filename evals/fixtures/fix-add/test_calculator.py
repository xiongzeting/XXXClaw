import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_adds_positive_numbers(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_adds_negative_numbers(self) -> None:
        self.assertEqual(add(-4, -7), -11)


if __name__ == "__main__":
    unittest.main()
