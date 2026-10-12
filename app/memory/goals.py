"""One equivalence rule for goal routing and memory preservation."""


def same_goal(first: str | None, second: str | None) -> bool:
    return first is not None and second is not None and (
        " ".join(first.split()).casefold() == " ".join(second.split()).casefold()
    )
