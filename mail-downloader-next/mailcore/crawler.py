import asyncio
import json
import operator
import urllib.parse as urlparse
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypeVar, Union, cast

import aiofiles
import httpx
import parsel
from config import Account, get_config
from path import TMP_PATH
from textual.widget import Widget
from utils.imap_structure import Envelope
from widgets.issue_viewer import (DetailsType, IssueLevel, envelope_to_dict,
                                  exc_to_dict)

from . import (BaseError, ErrorOcurred, ErrorTypes, LargeAttachmentTask,
               Result, ResultOcurred, States, is_error_retryable)

cfg = get_config()
_T = TypeVar('_T')


@dataclass
class CrawlerError(BaseError[LargeAttachmentTask]):
    resp: httpx.Response | None = None


LargeAttachmentDataType: TypeAlias = tuple[
    Account, str, str,
    Envelope,
    str
]
RequestMethodType: TypeAlias = Literal['get', 'post']
_ResultType: TypeAlias = Union[
    Result[
        LargeAttachmentTask,
        tuple[RequestMethodType, str]
    ],
    CrawlerError
]
_HandlerType: TypeAlias = Callable[
    [httpx.AsyncClient, LargeAttachmentTask],
    Coroutine[Any, Any, _ResultType]
]

valid_urls: dict[
    tuple[str, Path],
    _HandlerType
] = {}


def register(netloc: str, path: str):
    def inner(func: _HandlerType) -> _HandlerType:
        valid_urls[netloc, Path(path)] = func
        return func
    return inner


def get_url_handler(url: str) -> _HandlerType | None:

    for (netloc, path), handler in valid_urls.items():
        if _url_path_equal(url, netloc, path):
            return handler
    return None


def _url_path_equal(url: str, netloc: str, path: str | Path):
    if isinstance(path, str):
        path = Path(path)
    parse_res = urlparse.urlparse(url)
    return (
        (parse_res.netloc, Path(parse_res.path))
        == (netloc, path)
    )


def make_selector(html: str) -> parsel.Selector:
    return parsel.Selector(html, 'html')


def get_urls(html: str) -> set[str]:
    return set(map(
        operator.methodcaller('get'),
        make_selector(html)
        .xpath('//a/@href')
    ))


async def launch_crawler_tasks(
    task_data: Iterable[_T],
    unpacker: Callable[
        [_T],
        LargeAttachmentTask
    ],
    handler: _HandlerType,
    state: States,
    bound_widget: Widget,
):
    async def handler_wrapper(
        handler: _HandlerType,
        client: httpx.AsyncClient,
        task: LargeAttachmentTask
    ) -> _ResultType:
        try:
            res = await handler(client, task)
        except Exception as exc:
            return CrawlerError(
                task,
                ErrorTypes.UNKNOWN,
                IssueLevel.ERROR,
                exc_to_dict(exc)
            )
        else:
            return res

    def create_aio_tasks() -> bool:
        res = False
        for task in tasks:
            res = True
            task.retries += 1
            aio_task = asyncio.create_task(
                handler_wrapper(handler, client, task)
            )
            aio_task.add_done_callback(aio_tasks.discard)
            aio_tasks.add(aio_task)
        tasks.clear()
        return res
    from collections import deque
    tasks: deque[LargeAttachmentTask] = deque()
    for data in task_data:
        task = unpacker(data)
        tasks.append(task)
    aio_tasks: set[asyncio.Task[_ResultType]] = set()
    client = cfg._async_client
    while True:
        if not create_aio_tasks():
            break
        for fut in asyncio.as_completed(aio_tasks):
            res = await fut
            if isinstance(res, CrawlerError):
                if is_error_retryable(res) and res.task.retries < cfg.max_retries:
                    tasks.append(res.task)
                else:
                    bound_widget.post_message(ErrorOcurred(res, state))
            else:
                bound_widget.post_message(ResultOcurred(res, state))


async def search_large_attachments(
    task_data: Iterable[
        LargeAttachmentDataType
    ],
    bound_widget: Widget,
):
    async def handler(
        client: httpx.AsyncClient,
        task: LargeAttachmentTask,
    ) -> _ResultType:
        handler = get_url_handler(task.url)
        assert handler is not None
        return await handler(client, task)

    await launch_crawler_tasks(
        task_data,
        lambda data: LargeAttachmentTask(*data),
        handler,
        States.SEARCHING_LARGE_ATTACHMENTS,
        bound_widget
    )


async def _request(
    client: httpx.AsyncClient,
    url: str,
    method: RequestMethodType,
    task: LargeAttachmentTask,
    *,
    json: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None
) -> httpx.Response | CrawlerError:
    try:
        async with cfg._semaphore:
            resp = await client.request(
                method,
                url,
                json=json,
                headers=headers
            )
    except httpx.RequestError as exc:
        return CrawlerError(
            task,
            ErrorTypes.CONNECTION_FAILED,
            IssueLevel.ERROR,
            exc_to_dict(exc)
        )
    else:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return CrawlerError(
                task,
                ErrorTypes.REQUEST_ERROR,
                IssueLevel.ERROR,
                exc_to_dict(exc),
                resp=resp
            )
        else:
            return resp


# Handlers.
@register('mail.qq.com', '/cgi-bin/ftnExs_download')
async def handle_qq_mail1(
    client: httpx.AsyncClient,
    task: LargeAttachmentTask
) -> _ResultType:
    valid_netlocs = {
        'gzc-dfsdown.mail.ftn.qq.com',
        'njc-download.ftn.qq.com'
    }
    async with aiofiles.open(TMP_PATH, 'ab') as file:  # DEBUG
        resp = await _request(
            client,
            task.url,
            'get',
            task
        )
        if isinstance(resp, CrawlerError):
            resp.details = _make_details(
                task,
                resp.details
            )
            return resp
        # await file.write(resp.content)
        res_url = None
        try:
            urls = get_urls(resp.text)
        except ValueError:
            return CrawlerError(
                task,
                ErrorTypes.HTML_PARSING_ERROR,
                IssueLevel.ERROR,
                _make_details(task),
                resp=resp
            )
        for url in urls:
            parse_res = urlparse.urlparse(url)
            if parse_res.netloc in valid_netlocs:
                res_url = url
        if res_url is None:
            return CrawlerError(
                task,
                ErrorTypes.LINK_SEARCHING_ERROR,
                IssueLevel.INFO,
                _make_details(
                    task,
                    {'无法定位目标链接, 可能文件已被删除或已过期': None}
                ),
                resp=resp
            )
    return Result(
        task,
        ('get', res_url)
    )


@register('wx.mail.qq.com', '/ftn/download')
async def handle_qq_mail2(
    client: httpx.AsyncClient,
    task: LargeAttachmentTask
) -> _ResultType:
    async with aiofiles.open(TMP_PATH, 'ab') as file:  # DEBUG
        resp = await _request(
            client,
            task.url,
            'get',
            task
        )
        if isinstance(resp, CrawlerError):
            resp.details = _make_details(
                task,
                resp.details
            )
            return resp
        try:
            selector = make_selector(resp.text)
        except ValueError:
            return CrawlerError(
                task,
                ErrorTypes.HTML_PARSING_ERROR,
                IssueLevel.ERROR,
                _make_details(task),
                resp=resp
            )
        script_data = selector.xpath(
            '/html/body/script[@nonce='
            '"14540bb353ac024b89bb712b2e42cb28"]/text()'
        )
        code = script_data.re_first(r'var status\s*=\s*(\d+)')
        assert code is not None
        code = int(code)
        ok = False
        extra: DetailsType | None = None
        match code:
            case 1:
                ok = True
            case _:
                extra = {'文件因已被删除或已过期而无法下载': None}
        if ok:
            res_url = script_data.re_first(r'var url\s*=\s*"(.*)"')
            assert res_url is not None
            return Result(
                task,
                ('get', res_url)
            )
        else:
            assert extra is not None
            extra.update({'状态码': str(code)})
            return CrawlerError(
                task,
                ErrorTypes.LINK_SEARCHING_ERROR,
                IssueLevel.INFO,
                _make_details(
                    task,
                    extra
                ),
                resp=resp
            )


async def _handle_163_mail(
    client: httpx.AsyncClient,
    task: LargeAttachmentTask,
    key_of_link_key: str,
    prepare_url: str,
    key_of_prepare_resp_data: str,
    code_validator: Callable[[int], DetailsType | None],
    refresh_url: str
) -> _ResultType:
    async with aiofiles.open(TMP_PATH, 'ab') as file:  # DEBUG
        query = urlparse.parse_qs(urlparse.urlparse(task.url).query)
        link_key = query[key_of_link_key][0]
        prepare_resp = await _request(
            client,
            prepare_url,
            'post',
            task,
            json={'linkKey': link_key}
        )
        if isinstance(prepare_resp, CrawlerError):
            prepare_resp.details = _make_details(
                task,
                prepare_resp.details
            )
            return prepare_resp
        data = json.loads(prepare_resp.text)
        ok = False
        extra: DetailsType | None = None
        code = cast(int, data['code'])
        validate_res = code_validator(code)
        if validate_res is not None:
            extra = validate_res
        else:
            ok = True
        if ok:
            token = cast(str, data[
                key_of_prepare_resp_data
            ]['token'])
            await _request(
                client,
                refresh_url,
                'post',
                task,
                json={
                    'linkKey': link_key,
                    'token': token
                }
            )
            res_url = cast(str, data[
                key_of_prepare_resp_data
            ]['downloadUrl'])
            return Result(
                task,
                ('get', res_url)
            )
        else:
            assert extra is not None
            extra.update({'状态码': str(code)})
            return CrawlerError(
                task,
                ErrorTypes.LINK_SEARCHING_ERROR,
                IssueLevel.INFO,
                _make_details(
                    task,
                    extra
                ),
                resp=prepare_resp
            )


@register('dashi.163.com', '/html/cloud-attachment-download')
async def handle_163_mail1(
    client: httpx.AsyncClient,
    task: LargeAttachmentTask
) -> _ResultType:
    def validator(code: int) -> DetailsType | None:
        match code:
            case 200:
                return None
            case 404:
                return {'文件已被删除': None}
            case 400:
                return {'文件已过期': None}
            case _:
                return {'文件无法下载': None}
    return await _handle_163_mail(
        client,
        task,
        'key',
        'https://dashi.163.com/filehub-master/file/dl/prepare2',
        'result',
        validator,
        'https://dashi.163.com/filehub-master/file/dl/refresh'
    )


@register('mail.163.com', '/large-attachment-download/index.html')
async def handle_163_mail2(
    client: httpx.AsyncClient,
    task: LargeAttachmentTask
) -> _ResultType:
    def validator(code: int) -> DetailsType | None:
        match code:
            case 200:
                return None
            case 404:
                return {'文件已被删除': None}
            case 400:
                return {'文件已过期': None}
            case _:
                return {'文件无法下载': None}
    return await _handle_163_mail(
        client,
        task,
        'file',
        'https://mail.163.com/filehub/bg/dl/prepare',
        'data',
        validator,
        'https://mail.163.com/filehub/bg/dl/refresh'
    )


@register('mail.sina.com.cn', '/filecenter/download.php')
async def handle_sina_mail(
    client: httpx.AsyncClient,
    task: LargeAttachmentTask
) -> _ResultType:
    resp = await _request(
        client,
        task.url,
        'get',
        task
    )
    if isinstance(resp, CrawlerError):
        return resp
    try:
        selector = make_selector(resp.text)
    except ValueError:
        return CrawlerError(
            task,
            ErrorTypes.HTML_PARSING_ERROR,
            IssueLevel.ERROR,
            _make_details(task),
            resp=resp
        )
    if not selector.xpath('/html/body//input'):
        return CrawlerError(
            task,
            ErrorTypes.LINK_SEARCHING_ERROR,
            IssueLevel.INFO,
            _make_details(
                task,
                {'文件因已被删除或已过期而无法下载': None}
            ),
            resp=resp
        )
    return Result(
        task,
        ('post', task.url)
    )


def _make_details(
    task: LargeAttachmentTask,
    base_details: DetailsType | None = None
) -> DetailsType:
    if base_details is None:
        base_details = dict()
    res = dict(base_details)
    res.update(
        envelope_to_dict(task.envelope)
    )
    res.update(
        {'链接': task.url}
    )
    return res


def _placeholder_error(
    task: LargeAttachmentTask,
    base_details: DetailsType | None = None
):
    return CrawlerError(
        task,
        ErrorTypes.HANDLE_ERROR,
        IssueLevel.ERROR,
        _make_details(task, base_details)
    )
