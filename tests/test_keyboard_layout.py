import unittest
from services.keyboard_layout import to_latin_keys


class KeyboardLayoutTests(unittest.TestCase):
    def test_arabic_layout_barcode_maps_back_to_latin_keys(self):
        self.assertEqual(to_latin_keys("ٌ’01116003900107"), "RM01116003900107")

    def test_text_without_arabic_is_unchanged(self):
        self.assertEqual(to_latin_keys("RM0111"), "RM0111")

    def test_lowercase_keys(self):
        self.assertEqual(to_latin_keys("ق"), "r")
        self.assertEqual(to_latin_keys("ة"), "m")


if __name__ == "__main__":
    unittest.main()
