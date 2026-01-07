from typing import Any
import re


def query_to_records(db, sql: str) -> list[dict[str, Any]]:
    """Run a SQL query and return a list of dict records.

    Args:
        db: Database connection object exposing `.query(sql).fetchdf()`.
        sql: SQL query to execute.

    Returns:
        List[Dict[str, Any]]: Each row represented as a dictionary.
    """
    return db.query(sql).fetchdf().to_dict(orient="records")


def parse_location(location: str):
    """Parse a string location e.g. 1-1000:+ into the constituent parts

    Args:
        location (str): Location string formatted start-end:strand

    Raises:
        ValueError: If the location string is invalid

    Returns:
        Tuple[int,int,str]: Tuple of (start, end, strand) where start and end are integers and strand is either '+' or '-' or None
    """
    loc_re = re.compile(r"^(\d+)-(\d+)(?::([+-]))?$")
    m = loc_re.match(location)
    if not m:
        raise ValueError(f"Invalid location filter value: {location!r}")
    start, end, strand = m.groups()
    return int(start), int(end), strand


_binFirstShift = 17
_binNextShift = 3
_binOffsetOldToExtended = 4681
binOffsetsExtended = [
    4096 + 512 + 64 + 8 + 1,
    512 + 64 + 8 + 1,
    64 + 8 + 1,
    8 + 1,
    1,
    0,
]


def bins_for_range_extended(start: int, end: int) -> list[int]:
    """Code for generating a range of locations. Assumes UCSC style indexing

    Args:
        start (int): Start of the bounds. Must be 0 based
        end (int): End of the bounds. Must be half open i.e. don't -1 from your end

    Raises:
        ValueError: Thrown if given a bad range

    Returns:
        list[int]: The possible bins that overlap the given range
    """
    if start < 0 or end <= start:
        raise ValueError(f"Invalid range: start={start}, end={end}")

    bins = []
    start_bin = start >> _binFirstShift
    end_bin = (end - 1) >> _binFirstShift

    for offset in binOffsetsExtended:
        bins.extend(
            _binOffsetOldToExtended + offset + b for b in range(start_bin, end_bin + 1)
        )
        start_bin >>= _binNextShift
        end_bin >>= _binNextShift

    return bins
