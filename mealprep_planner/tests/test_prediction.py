import unittest

from mealprep import planner

USERS = ["A", "B"]
TAGS = {1: ["Pasta", "Quick"], 2: ["Pasta"], 3: ["Soup"], 4: ["Soup", "Quick"], 5: ["Pasta", "Soup"], 6: []}
RATINGS = {"A": {1: 5, 2: 4, 3: 1}, "B": {1: 2, 4: 0}}

# Worked example (K = 2):
#   G    = (5 + 4 + 1 + 2 + 0) / 5 = 2.4         mu_A = 10 / 3 = 3.3333     mu_B = 2 / 2 = 1.0
#   A:   Pasta (9 + 2 * 3.3333) / 4 = 3.9167     Quick (5 + 6.6667) / 3 = 3.8889    Soup (1 + 6.6667) / 3 = 2.5556
#   B:   Pasta (2 + 2 * 1) / 3 = 1.3333          Quick (2 + 0 + 2) / 4 = 1.0        Soup (0 + 2) / 3 = 0.6667


def model(users=USERS, ratings=RATINGS, tags=TAGS):
    return planner.build_model(users, ratings, tags)


class PredictionTest(unittest.TestCase):
    def test_means(self):
        self.assertAlmostEqual(planner.global_mean(RATINGS), 2.4)
        means = planner.user_means(["A", "B", "C"], RATINGS, 2.4)
        self.assertAlmostEqual(means["A"], 10 / 3)
        self.assertAlmostEqual(means["B"], 1.0)  # the zero counts
        self.assertAlmostEqual(means["C"], 2.4)  # no ratings -> G

    def test_tag_affinities(self):
        aff = planner.tag_affinities(RATINGS["A"], TAGS, 10 / 3)
        self.assertEqual(set(aff), {"Pasta", "Quick", "Soup"})
        self.assertAlmostEqual(aff["Pasta"], 3.9167, places=2)
        self.assertAlmostEqual(aff["Quick"], 3.8889, places=2)
        self.assertAlmostEqual(aff["Soup"], 2.5556, places=2)
        aff = planner.tag_affinities(RATINGS["B"], TAGS, 1.0)
        self.assertAlmostEqual(aff["Pasta"], 1.3333, places=2)
        self.assertAlmostEqual(aff["Quick"], 1.0, places=2)
        self.assertAlmostEqual(aff["Soup"], 0.6667, places=2)

    def test_predict_unrated_recipe(self):
        m = model()
        self.assertAlmostEqual(planner.predict(m, "A", 5), 3.2361, places=2)  # (3.9167 + 2.5556) / 2
        self.assertAlmostEqual(planner.predict(m, "B", 5), 1.0, places=2)  # (1.3333 + 0.6667) / 2
        self.assertAlmostEqual(planner.predict(m, "A", 4), 3.2222, places=2)  # (2.5556 + 3.8889) / 2

    def test_untagged_recipe_gets_user_mean(self):
        m = model()
        self.assertAlmostEqual(planner.predict(m, "A", 6), 3.33, places=2)
        self.assertAlmostEqual(planner.predict(m, "B", 6), 1.0, places=2)
        self.assertAlmostEqual(planner.household_score(m, 6)[0], 2.17, places=2)
        self.assertAlmostEqual(planner.predict(m, "A", 99), 3.33, places=2)  # unknown recipe = no tags

    def test_household_score_uses_stars_else_prediction(self):
        m = model()
        score, details = planner.household_score(m, 1)
        self.assertAlmostEqual(score, 3.5)
        self.assertEqual({u: d["stars"] for u, d in details.items()}, {"A": 5, "B": 2})
        score, details = planner.household_score(m, 5)
        self.assertAlmostEqual(score, 2.12, places=2)
        self.assertEqual({u: d["stars"] for u, d in details.items()}, {"A": None, "B": None})
        for recipe_id, expected in {2: 2.67, 3: 0.83, 4: 1.61}.items():
            self.assertAlmostEqual(planner.household_score(m, recipe_id)[0], expected, places=2)

    def test_no_ratings_gives_prior(self):
        m = model(ratings={})
        self.assertEqual(planner.global_mean({}), planner.PRIOR)
        for recipe_id in TAGS:
            self.assertEqual(planner.household_score(m, recipe_id)[0], planner.PRIOR)
        self.assertEqual(planner.household_score(model(users=[], ratings={}), 1)[0], planner.PRIOR)

    def test_no_known_users_gives_g(self):
        self.assertAlmostEqual(planner.household_score(model(users=[]), 1)[0], 2.4)

    def test_users_without_ratings_keep_the_ranking(self):
        base = model()
        more = model(users=["A", "B", "C", "D"])
        ids = sorted(TAGS)
        self.assertEqual(sorted(ids, key=lambda r: planner.household_score(base, r)[0]),
                         sorted(ids, key=lambda r: planner.household_score(more, r)[0]))
        # an unrated user contributes G to every recipe: (2 * score + 2 * G) / 4
        for r in ids:
            self.assertAlmostEqual(planner.household_score(more, r)[0],
                                   (2 * planner.household_score(base, r)[0] + 2 * 2.4) / 4)

    def test_zeros_count_in_means_and_veto(self):
        m = model()
        self.assertTrue(planner.is_vetoed(m, 4))
        self.assertFalse(planner.is_vetoed(m, 1))
        self.assertFalse(planner.is_vetoed(m, 5))  # unrated is not 0
        # B's 0 pulls its mean (and the tag affinities) down; without it B's mean would be 2.0
        self.assertAlmostEqual(m["mu"]["B"], 1.0)

    def test_veto_by_any_user(self):
        for who in USERS:
            m = model(ratings={who: {3: 0}, **{u: {} for u in USERS if u != who}})
            self.assertTrue(planner.is_vetoed(m, 3), who)
            self.assertFalse(planner.is_vetoed(m, 2), who)
        # a 0 from one user is not outweighed by 5 stars from the other
        m = model(ratings={"A": {3: 5}, "B": {3: 0}})
        self.assertTrue(planner.is_vetoed(m, 3))
        self.assertAlmostEqual(planner.household_score(m, 3)[0], 2.5)

    def test_taste_follows_shared_tags(self):
        ratings = {"A": {1: 5, 2: 5, 3: 0, 4: 1}}
        m = model(users=["A"], ratings=ratings, tags={1: ["Pasta"], 2: ["Pasta"], 3: ["Soup"], 4: ["Soup"], 5: ["Pasta"], 6: ["Soup"]})
        self.assertGreater(planner.predict(m, "A", 5), planner.predict(m, "A", 6))


if __name__ == "__main__":
    unittest.main()
