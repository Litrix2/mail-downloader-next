import datetime
import email.header
import re

__all__ = [
    'MAX_SEARCH_DATE',
    'MIN_SEARCH_DATE',
    'MONTHS',
    'RAW_DATE_PATTERN',
    'date_to_times',
    'decode_header',
    'input_str_to_times',
    'times_to_date',
    'times_to_input_str',
    'times_to_search_str'
]


# Date helpers.
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
RAW_DATE_PATTERN = r'(\d{1,4})-(\d{1,2})-(\d{1,2})'
MIN_SEARCH_DATE = datetime.date(1970, 1, 1)
MAX_SEARCH_DATE = datetime.date(2099, 12, 31)


def times_to_input_str(year: int, month: int, day: int):
    return f'{year:04}-{month:02}-{day:02}'


def times_to_search_str(year: int, month: int, day: int):
    return f'{day:02}-{MONTHS[month-1]:02}-{year:04}'


def input_str_to_times(text: str) -> tuple[int, int, int]:
    msg = 'invalid date'
    match_res = re.fullmatch(RAW_DATE_PATTERN, text)
    if match_res is None:
        raise ValueError(msg)
    year, month, day = map(int, match_res.groups())
    return year, month, day


def times_to_date(year: int, month: int, day: int) -> datetime.date:
    msg = 'invalid date'
    try:
        date = datetime.date(year, month, day)
    except ValueError:
        raise ValueError(msg)
    if not MIN_SEARCH_DATE <= date <= MAX_SEARCH_DATE:
        raise ValueError(msg)
    return date


def date_to_times(date: datetime.date) -> tuple[int, int, int]:
    return date.year, date.month, date.day


# Encoders/decoders.
def decode_header(header: str) -> str:
    return str(
        email.header.make_header(
            email.header.decode_header(header)
        )
    )
