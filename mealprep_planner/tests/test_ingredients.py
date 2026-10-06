import unittest

from mealprep.ingredients import UNITS, fmt_amount, parse_line, parse_lines, scale

# line -> (amount, unit, name, note)
CASES = [
    # §6 examples
    ("500 g Mehl", (500, "g", "Mehl", None)),
    ("1 Dose Tomaten, gehackt", (1, "Dose", "Tomaten", "gehackt")),
    ("2-3 Zehen Knoblauch", (3, "Zehe", "Knoblauch", None)),
    ("½ TL Salz", (0.5, "TL", "Salz", None)),
    ("1 ½ kg Kartoffeln (festkochend)", (1.5, "kg", "Kartoffeln", "festkochend")),
    ("200g Speck", (200, "g", "Speck", None)),
    ("4 Eier", (4, None, "Eier", None)),
    ("n. B. Pfeffer", (None, None, "Pfeffer", "n. B.")),
    ("1 Pck. Backpulver", (1, "Packung", "Backpulver", None)),
    ("Salz", (None, None, "Salz", None)),
    # amounts
    ("1 1/2 l Milch", (1.5, "l", "Milch", None)),
    ("1/2 Zwiebel", (0.5, None, "Zwiebel", None)),
    ("1½ EL Öl", (1.5, "EL", "Öl", None)),
    ("¾ l Brühe", (0.75, "l", "Brühe", None)),
    ("1,5 kg Äpfel", (1.5, "kg", "Äpfel", None)),
    ("0.25 l Sahne", (0.25, "l", "Sahne", None)),
    ("2 bis 3 EL Zucker", (3, "EL", "Zucker", None)),
    ("2–3 Stück Paprika", (3, "Stück", "Paprika", None)),
    ("1l Wasser", (1, "l", "Wasser", None)),
    ("250ml Sahne", (250, "ml", "Sahne", None)),
    ("1,5kg Mehl", (1.5, "kg", "Mehl", None)),
    # units and aliases
    ("3 Essl. Öl", (3, "EL", "Öl", None)),
    ("2 Teelöffel Honig", (2, "TL", "Honig", None)),
    ("1 Msp. Zimt", (1, "Msp.", "Zimt", None)),
    ("2 Prisen Salz", (2, "Prise", "Salz", None)),
    ("2 Gläser Gurken", (2, "Glas", "Gurken", None)),
    ("1 Bund Petersilie", (1, "Bund", "Petersilie", None)),
    ("1 STK Lauch", (1, "Stück", "Lauch", None)),
    ("1 Päckchen Vanillezucker", (1, "Packung", "Vanillezucker", None)),
    # unknown unit words stay in the name
    ("1 kleine Dose Mais", (1, None, "kleine Dose Mais", None)),
    ("2 Gemüsezwiebeln", (2, None, "Gemüsezwiebeln", None)),
    ("Dose Tomaten", (None, None, "Dose Tomaten", None)),
    # notes
    ("Zwiebeln (rot, gehackt)", (None, None, "Zwiebeln", "rot, gehackt")),
    ("3 Möhren (groß), geschält", (3, None, "Möhren", "groß; geschält")),
    # markers
    ("etwas Olivenöl", (None, None, "Olivenöl", "etwas")),
    ("nach Belieben Chili", (None, None, "Chili", "nach Belieben")),
    ("evtl. 1 EL Zucker", (1, "EL", "Zucker", "evtl.")),
    ("einige Zweige Thymian", (None, "Zweig", "Thymian", "einige")),
    # bullets and numbering
    ("- 500 g Hack", (500, "g", "Hack", None)),
    ("• 2 Eier", (2, None, "Eier", None)),
    ("1. 3 Karotten", (3, None, "Karotten", None)),
    ("  2   EL   Senf  ", (2, "EL", "Senf", None)),
    # nothing lost
    ("1 EL", (None, None, "1 EL", None)),
    ("(optional)", (None, None, "(optional)", None)),
    ("???", (None, None, "???", None)),
]


class ParseLineTest(unittest.TestCase):
    def test_table(self):
        for line, (amount, unit, name, note) in CASES:
            with self.subTest(line=line):
                got = parse_line(line)
                self.assertEqual(
                    (got["amount"], got["unit"], got["name"], got["note"]), (amount, unit, name, note))

    def test_never_raises(self):
        for line in ["", "   ", "1/0 EL Mehl", "0/0", "999999999999999999999 g Mehl", "½", "1 2/0 l X", ",", "(", "\x00\t"]:
            with self.subTest(line=line):
                self.assertEqual(set(parse_line(line)), {"amount", "unit", "name", "note"})

    def test_division_by_zero_keeps_text(self):
        self.assertIn("Mehl", parse_line("1/0 EL Mehl")["name"])

    def test_parse_lines_skips_blank_lines(self):
        self.assertEqual([i["name"] for i in parse_lines("2 Eier\n\n  \n100 g Mehl\n")], ["Eier", "Mehl"])

    def test_chefkoch_style_block(self):
        text = "400 g Spaghetti\n4 Eier\n100 g Parmesan, frisch gerieben\n1 Prise Muskat\n2 Zehen Knoblauch\n"
        names = [(i["amount"], i["unit"], i["name"]) for i in parse_lines(text)]
        self.assertEqual(names, [(400, "g", "Spaghetti"), (4, None, "Eier"), (100, "g", "Parmesan"),
                                 (1, "Prise", "Muskat"), (2, "Zehe", "Knoblauch")])


class FormatTest(unittest.TestCase):
    def test_fmt_amount(self):
        for amount, unit, expected in [
            (800, "g", "800 g"), (1.5, "kg", "1,5 kg"), (2, "Dose", "2 Dosen"), (1, "Dose", "1 Dose"),
            (0.5, "Dose", "0,5 Dose"), (1.5, "Glas", "1,5 Gläser"), (4, None, "4"), (0.333, "EL", "0,33 EL"),
            (1000, "ml", "1000 ml"), (2.999, "TL", "3 TL"), (3, "Kopf", "3 Köpfe"), (None, "g", ""), (None, None, ""),
        ]:
            with self.subTest(amount=amount, unit=unit):
                self.assertEqual(fmt_amount(amount, unit), expected)

    def test_scale(self):
        self.assertEqual(scale(200, 1.5), 300)
        self.assertIsNone(scale(None, 2))
        self.assertAlmostEqual(scale(1, 1 / 3), 0.3333)

    def test_every_unit_roundtrips_through_parser(self):
        for unit, (plural, _) in UNITS.items():
            for word in (unit, plural):
                with self.subTest(word=word):
                    self.assertEqual(parse_line(f"2 {word} Zutat")["unit"], unit)


if __name__ == "__main__":
    unittest.main()
