"""The check every facets endpoint must pass: each count is the `total` its
list gives with that value picked, within the other filters chosen."""


def facets_agree_with_the_list(client, path: str, **chosen) -> int:
    """Checks every count of `{path}/facets` against `path`'s total; returns how
    many were checked."""
    response = client.get(f"{path}/facets", params=chosen)
    assert response.status_code == 200, response.text
    checked = 0
    for facet, counts in response.json().items():
        if counts is None:
            continue
        others = {name: value for name, value in chosen.items() if name != facet}
        for value, count in counts.items():
            listed = client.get(path, params={**others, facet: value})
            assert listed.status_code == 200, listed.text
            assert listed.json()["total"] == count, (facet, value, count)
            checked += 1
    return checked
