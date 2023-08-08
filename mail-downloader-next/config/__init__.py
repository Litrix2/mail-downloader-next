import datetime
import tomllib
from collections.abc import Mapping, MutableSequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from gettext import NullTranslations, translation
from typing import Any, Protocol, TypeAlias, TypeVar

import utils.mail_helpers as mail_helpers
from utils.imap_structure import Address

_PosType: TypeAlias = MutableSequence[str | int]
_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)

LANGUAGES = [
    'zh',
    'en'
]
NAME = 'MailDownloaderNext'
MESSAGE_TYPES = ('all', 'unseen', 'seen')
VERSION = '0.1.0a1'
IMAP4_ID = {
    'name': 'mail2_asyncio',
    'version': '1.0'
}


class SupportsGetItem(Protocol[_T_co]):
    def __getitem__(self, __name: Any) -> _T_co: ...


@dataclass(frozen=True)
class Account:
    host: str
    port: int
    address: Address
    password: str


@dataclass
class Config:
    accounts: list[Account]
    mailboxes: list[list[str]]
    message_type: str
    begin_search_date: datetime.date | None
    end_search_date: datetime.date | None
    timeout: int
    max_imap_connection_count: int
    max_crawler_connection_count: int
    max_retries: int
    max_occupancy_size: int
    trans: NullTranslations

    def __post_init__(self):
        import asyncio

        import httpx
        from utils.occupancy_limiter import OccupancyLimiter
        self._occupancy_limiter = OccupancyLimiter(
            self.max_occupancy_size*1024**2)
        self._semaphore = asyncio.Semaphore(self.max_crawler_connection_count)
        self._async_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.timeout
        )

    async def finalize(self):
        await self._async_client.aclose()


class ConfigParseError(Exception):
    def __init__(self, pos: _PosType | None, reason: str, *items: Any) -> None:
        self.reason = reason
        if pos is not None:
            self.pos = '.'.join(map(str, pos))
            super().__init__(f'"{self.pos}": {reason.format(*items)}')
        else:
            self.pos = None
            super().__init__(reason.format(*items))


backup_config: Config | None = None
config: Config | None = None


def _load_config_from_dict(cfg: Mapping[str, Any]) -> Config:

    def raise_lookup():
        raise ConfigParseError(pos_list, 'item was not found') from None

    def raise_invalid_value(excepted: Any, real: Any):
        raise ConfigParseError(
            pos_list, 'the value should {}, got {}', excepted, real) from None

    def raise_invalid_length(excepted: Any, real: Any):
        raise ConfigParseError(
            pos_list, 'the length should be {}, got {}', excepted, real) from None

    def raise_invalid_type(excepted: type | tuple[type, ...], real: type):
        raise ConfigParseError(
            pos_list, 'excepted type {}, got {}', (
                excepted.__name__
                if not isinstance(excepted, tuple)
                else tuple(typ.__name__ for typ in excepted)
            ), real.__name__) from None

    def lookup(obj: SupportsGetItem[Any]) -> Any:
        try:
            res = obj[pos_list[-1]]
        except LookupError:
            raise_lookup()
        else:
            return res

    def check_type(obj: _T, excepted: type | tuple[type, ...]) -> _T:
        if not isinstance(obj, excepted):
            raise_invalid_type(excepted, type(obj))
        return obj

    @contextmanager
    def pos(*sub_positions: str | int):
        pos_list.extend(sub_positions)
        yield
        for _ in sub_positions:
            pos_list.pop()

    pos_list: _PosType = []

    with pos('mail'):
        cfg_mail = check_type(lookup(cfg), dict)
        with pos('accounts'):
            res_accounts: list[Account] = []
            cfg_mail_accounts = lookup(cfg_mail)
            check_type(cfg_mail_accounts, list)
            for i, cfg_mail_account in enumerate(cfg_mail_accounts):
                with pos(i):
                    with pos('host'):
                        cfg_mail_account_host = check_type(
                            lookup(cfg_mail_account), str)
                    with pos('address'):
                        cfg_mail_account_address: str = check_type(
                            lookup(cfg_mail_account), str)
                        split = cfg_mail_account_address.find('@')
                        if split == -1:
                            raise_invalid_value(
                                'be an e-mail address', cfg_mail_account_address)
                        username = cfg_mail_account_address[:split]
                        domain = cfg_mail_account_address[split+1:]
                    with pos('port'):
                        cfg_mail_account_port = check_type(
                            lookup(cfg_mail_account), int
                        )
                    with pos('password'):
                        cfg_mail_account_password = check_type(
                            lookup(cfg_mail_account), str
                        )
                    res_accounts.append(Account(
                        host=cfg_mail_account_host,
                        port=cfg_mail_account_port,
                        address=Address(
                            username,
                            domain
                        ),
                        password=cfg_mail_account_password
                    ))

        with pos('mailboxes'):
            res_mailboxes: list[list[str]] = []
            cfg_mail_mailboxes = check_type(lookup(cfg_mail), list)
            if len(cfg_mail_mailboxes) != len(res_accounts):
                raise ConfigParseError(
                    pos_list, 'mailboxes and accounts could not correspond one by one')
            for i1, mailbox_list in enumerate(cfg_mail_mailboxes):
                with pos(i1):
                    check_type(mailbox_list, list)
                    for i2, mailbox in enumerate(mailbox_list):
                        with pos(i2):
                            check_type(mailbox, str)
                    res_mailboxes.append(list(mailbox_list))
    with pos('search'):
        res_begin_search_date: datetime.date | None = None
        res_end_search_date: datetime.date | None = None
        cfg_search = check_type(lookup(cfg), dict)
        with pos('begin_search_date'):
            try:
                cfg_search_begin_search_date = lookup(cfg_search)
            except ConfigParseError:
                pass
            else:
                begin_search_date: list[int] = []
                cfg_search_begin_search_date = check_type(
                    cfg_search_begin_search_date, list
                )
                if len(cfg_search_begin_search_date) != 3:
                    raise_invalid_length(3, len(cfg_search_begin_search_date))
                for i, date in enumerate(
                        cfg_search_begin_search_date):
                    with pos(i):
                        check_type(date, int)
                        begin_search_date.append(date)
                try:
                    res_begin_search_date = mail_helpers.times_to_date(
                        *begin_search_date)
                except ValueError as exc:
                    raise ConfigParseError(pos_list, exc.args[0]) from None
        with pos('end_search_date'):
            try:
                cfg_search_end_search_date = lookup(cfg_search)
            except ConfigParseError:
                pass
            else:
                end_search_date: list[int] = []
                cfg_search_end_search_date = check_type(
                    cfg_search_end_search_date, list
                )
                if len(cfg_search_end_search_date) != 3:
                    raise_invalid_length(3, len(cfg_search_end_search_date))
                for i, date in enumerate(
                        cfg_search_end_search_date):
                    with pos(i):
                        check_type(date, int)
                        end_search_date.append(date)
                try:
                    res_end_search_date = mail_helpers.times_to_date(
                        *end_search_date)
                except ValueError as exc:
                    raise ConfigParseError(pos_list, exc.args[0]) from None
        with pos('message_type'):
            res_search_message_type = check_type(lookup(cfg_search), str)
            if res_search_message_type not in MESSAGE_TYPES:
                raise_invalid_value(
                    f'be in {MESSAGE_TYPES}',
                    res_search_message_type
                )
    with pos('connection'):
        cfg_connection = check_type(lookup(cfg), dict)
        with pos('timeout'):
            res_timeout = check_type(lookup(cfg_connection), int)
            if res_timeout <= 0:
                raise_invalid_value(
                    'be greater than 0',
                    res_timeout
                )
        with pos('max_imap_connection_count'):
            res_max_imap_connection_count = check_type(
                lookup(cfg_connection), int)
            if res_max_imap_connection_count < 1:
                raise_invalid_value(
                    'not be less than 1',
                    res_max_imap_connection_count
                )
        with pos('max_crawler_connection_count'):
            res_max_crawler_connection_count = check_type(
                lookup(cfg_connection), int)
            if res_max_crawler_connection_count < 1:
                raise_invalid_value(
                    'not be less than 1',
                    res_max_crawler_connection_count
                )
        with pos('max_retries'):
            res_max_retries = check_type(lookup(cfg_connection), int)
            if res_max_retries < 1:
                raise_invalid_value('not be less than 1',
                                    res_max_retries)
    with pos('download'):
        cfg_download = check_type(lookup(cfg), dict)
        with pos('max_occupancy_size'):
            res_max_occupancy_size = check_type(
                lookup(cfg_download), int)
            if res_max_occupancy_size <= 0:
                raise_invalid_value('be greater than 0',
                                    res_max_occupancy_size)
    with pos('display'):
        cfg_display = check_type(lookup(cfg), dict)
        with pos('current_language'):
            cfg_display_current_language = check_type(lookup(cfg_display), str)
            if cfg_display_current_language not in LANGUAGES:
                raise_invalid_value(f'be in {LANGUAGES}',
                                    cfg_display_current_language)
            try:
                res_trans = translation('messages', 'i18n', [
                                        cfg_display_current_language])
            except Exception as exc:
                raise ConfigParseError(pos_list, '\n'.join(exc.args)) from None

    return Config(
        accounts=res_accounts,
        mailboxes=res_mailboxes,
        message_type=res_search_message_type,
        begin_search_date=res_begin_search_date,
        end_search_date=res_end_search_date,
        timeout=res_timeout,
        max_imap_connection_count=res_max_imap_connection_count,
        max_crawler_connection_count=res_max_crawler_connection_count,
        max_retries=res_max_retries,
        max_occupancy_size=res_max_occupancy_size,
        trans=res_trans
    )


def load_config(path: str) -> None:
    try:
        file = open(path, 'r', encoding='utf-8')
    except OSError:
        raise OSError(f'could not open config "{path}"')
    try:
        cfg_dict = tomllib.loads(file.read())
    except tomllib.TOMLDecodeError:
        raise ConfigParseError(None, 'invalid config syntax') from None
    global backup_config, config
    config = _load_config_from_dict(cfg_dict)
    backup_config = _load_config_from_dict(cfg_dict)


def get_config():
    if config is not None:
        return config
    else:
        raise RuntimeError('no available config')


def get_backup_config():
    if backup_config is not None:
        return backup_config
    else:
        raise RuntimeError('no available config')


async def sync_config(source: Config, dest: Config):
    await dest.finalize()
    from copy import deepcopy
    for field in fields(source):
        setattr(dest, field.name, deepcopy(getattr(source, field.name)))
    dest.__post_init__()
