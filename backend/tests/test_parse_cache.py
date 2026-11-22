from ranker.search import parse


def test_location_cache_is_bounded() -> None:
    info = parse._location.cache_info()  # pyright: ignore[reportPrivateUsage]
    assert info.maxsize == parse.LOCATION_CACHE_SIZE
    for i in range(parse.LOCATION_CACHE_SIZE + 50):
        parse.parse_rules(f"zzword{i}")
    after = parse._location.cache_info()  # pyright: ignore[reportPrivateUsage]
    assert after.currsize <= parse.LOCATION_CACHE_SIZE
