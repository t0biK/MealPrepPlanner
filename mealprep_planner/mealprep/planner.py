"""Taste prediction (M6): pure functions on plain data, no DB access.

ratings: {user_id: {recipe_id: stars 0-5}}   tags: {recipe_id: [tag names]}   users: [user_id]
"""

PRIOR = 3.0  # household mean while nobody has rated anything
K = 2  # pseudo-ratings pulling a tag affinity towards the user's own mean


def global_mean(ratings):
    """Mean of all stars of all users (zeros count); PRIOR if there are none."""
    stars = [s for rated in ratings.values() for s in rated.values()]
    return sum(stars) / len(stars) if stars else PRIOR


def user_means(users, ratings, g):
    """{user: mean of the user's stars, or g if the user has none}"""
    out = {}
    for u in users:
        stars = list(ratings.get(u, {}).values())
        out[u] = sum(stars) / len(stars) if stars else g
    return out


def tag_affinities(user_ratings, tags, mu):
    """{tag: a_u(t)} for the tags the user has rated at least once; any other tag has affinity mu."""
    sums, counts = {}, {}
    for recipe_id, stars in user_ratings.items():
        for tag in tags.get(recipe_id, ()):
            sums[tag] = sums.get(tag, 0) + stars
            counts[tag] = counts.get(tag, 0) + 1
    return {t: (sums[t] + K * mu) / (counts[t] + K) for t in sums}


def build_model(users, ratings, tags):
    """Everything the per-recipe functions need, computed once per request."""
    g = global_mean(ratings)
    mu = user_means(users, ratings, g)
    return {
        "g": g, "mu": mu, "ratings": ratings, "tags": tags, "users": list(users),
        "affinity": {u: tag_affinities(ratings.get(u, {}), tags, mu[u]) for u in users},
    }


def predict(model, user, recipe_id):
    """pred_u(r): mean affinity of the recipe's tags, or the user's mean if it has no tags."""
    recipe_tags = model["tags"].get(recipe_id, ())
    mu = model["mu"][user]
    if not recipe_tags:
        return mu
    affinity = model["affinity"][user]
    return sum(affinity.get(t, mu) for t in recipe_tags) / len(recipe_tags)


def household_score(model, recipe_id):
    """score(r) and details {user: {"stars": int | None, "value": s_u(r)}} (G if there are no users)."""
    details = {}
    for u in model["users"]:
        stars = model["ratings"].get(u, {}).get(recipe_id)
        details[u] = {"stars": stars, "value": predict(model, u, recipe_id) if stars is None else stars}
    if not details:
        return model["g"], details
    return sum(d["value"] for d in details.values()) / len(details), details


def is_vetoed(model, recipe_id):
    """Some user rated the recipe 0 ("Nie wieder")."""
    return any(rated.get(recipe_id) == 0 for rated in model["ratings"].values())
