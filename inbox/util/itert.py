import itertools
from builtins import filter


def chunk(iterable, size):
    """ Yield chunks of an iterable.

        If len(iterable) is not evenly divisible by size, the last chunk will
        be shorter than size.
    """
    it = iter(iterable)
    while True:
        group = tuple(itertools.islice(it, None, size))
        if not group:
            break
        yield group


def partition(pred, iterable):
    """ Use a predicate to partition entries into false entries and true
        entries.

        e.g.:

            partition(is_odd, range(10)) --> 0 2 4 6 8   and  1 3 5 7 9
    """
    t1, t2 = itertools.tee(iterable)
    return list(itertools.filterfalse(pred, t1)), list(filter(pred, t2))


def flatten(iterable):
    # Flatten a list of lists.
    # http://stackoverflow.com/a/953097
    return list(itertools.chain(*iterable))
