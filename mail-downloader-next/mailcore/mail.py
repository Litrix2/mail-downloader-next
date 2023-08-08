import asyncio
import socket
from collections.abc import (Callable, Coroutine, Iterable, Iterator,
                             MutableMapping)
from dataclasses import dataclass
from typing import (Any, Generic, NamedTuple, Optional, TypeAlias, TypeVar,
                    Union, cast)

import aiofiles
import aioimaplib as aioimap
import utils.mail_helpers as mail_helpers
from config import IMAP4_ID, Account, get_config
from path import TMP_PATH
from textual.message import Message as TextualMessage
from textual.widget import Widget
from utils.imap_structure import (BodyStructureType, Envelope, MultiPart,
                                  NonMultiPart, load_bodystructure,
                                  load_envelope)
from widgets.issue_viewer import IssueLevel, envelope_to_dict, exc_to_dict

from . import (BaseError, ErrorOcurred, ErrorTypes, MessageTask,
               NormalAttachmentTask, Result, ResultOcurred, ResultT, States,
               TaskT, URLTask, crawler, is_error_retryable)

cfg = get_config()
_T = TypeVar('_T')


MessageDataType: TypeAlias = tuple[Account, str]
NormalAttachmentDataType: TypeAlias = tuple[*MessageDataType, str]
URLDataType: TypeAlias = tuple[*NormalAttachmentDataType,
                               Envelope, NonMultiPart]

IMAP_ERRORS = (
    socket.gaierror,
    aioimap.AioImapException,
    TimeoutError,
)


@dataclass
class Attachments:
    envelope: Envelope
    normal_attachments: list[NonMultiPart]
    html_parts: list[NonMultiPart]

    def __post_init__(self):
        self.error_ocurred = False
        self.large_attachments: list[
            tuple[crawler.RequestMethodType, str]
        ] = []
        self._valid_urls = set[str]()


@dataclass
class MailError(BaseError[TaskT]):
    resp: aioimap.Response | None = None


_TaskQueueType: TypeAlias = asyncio.Queue[TaskT | None]
_ResultType: TypeAlias = Union[
    Result[TaskT, ResultT],
    MailError[
        TaskT
    ]
]
_ResultQueueType: TypeAlias = asyncio.Queue[
    _ResultType[
        TaskT,
        ResultT
    ]
]
_HandlerType: TypeAlias = Callable[
    [aioimap.IMAP4_SSL, TaskT],
    Coroutine[Any, Any, _ResultType[
        TaskT,
        ResultT
    ]]
]


def _is_response_invalid(resp: aioimap.Response):
    return resp.result in ('NO', 'BAD')


def _is_msg_same(
    source_envelope: Envelope,
    new_envelope: Envelope
) -> bool:
    return (
        source_envelope.message_id is not None
        and new_envelope.message_id is not None
        and source_envelope.message_id == new_envelope.message_id
    )


def _ensure_structure(text: str) -> str:
    return text[text.find('('):text.rfind(')')+1]


def _concat_response_lines(resp: aioimap.Response, quote: bool = True) -> bytearray:
    res = bytearray()
    is_str_sync = False
    for line in resp.lines:
        assert isinstance(line, (bytes, bytearray))
        if line.endswith(b'}'):
            line = line[0:line.rfind(b'{')]
            is_str_sync = True
        elif is_str_sync:
            is_str_sync = False
            if quote:
                line = b''.join((
                    b'"', line, b'"'
                ))
        res.extend(line)
    return res


async def _create_client(host: str, port: int):
    res = aioimap.IMAP4_SSL(host, port, timeout=cfg.timeout)
    await res.wait_hello_from_server()
    return res


async def _request(
    operation: Coroutine[Any, Any, aioimap.Response],
    task: TaskT,
    min_line_count: int | None = None,
    size: int | None = None
) -> aioimap.Response | MailError[TaskT]:
    try:
        async with cfg._occupancy_limiter(size):
            resp = await operation
    except IMAP_ERRORS as exc:
        return MailError(
            task,
            ErrorTypes.CONNECTION_FAILED,
            IssueLevel.ERROR,
            exc_to_dict(exc)
        )
    else:
        if (
            _is_response_invalid(resp)
            and (
                min_line_count is None
                or len(resp.lines) < min_line_count
            )
        ):
            return MailError(
                task,
                ErrorTypes.REQUEST_ERROR,
                IssueLevel.ERROR,
                resp=resp
            )
    return resp


async def launch_imap_tasks(
    task_data: Iterable[_T],
    unpacker: Callable[
        [_T],
        TaskT
    ],
    handler: _HandlerType[TaskT, ResultT],
    state: States,
    bound_widget: Widget,
):
    task_count = 0
    tasks: _TaskQueueType[TaskT] = asyncio.Queue()
    for key in task_data:
        tasks.put_nowait(unpacker(key))
        task_count += 1
    results: _ResultQueueType[
        TaskT, ResultT
    ] = asyncio.Queue()
    result_count = 0
    aio_tasks: set[asyncio.Task[None]] = set()
    for _ in range(cfg.max_imap_connection_count):
        aio_task = asyncio.create_task(
            dispatch_tasks(handler, tasks, results))
        aio_task.add_done_callback(aio_tasks.discard)
        aio_tasks.add(aio_task)
    while True:
        if result_count == task_count:
            for _ in range(cfg.max_imap_connection_count):
                tasks.put_nowait(None)
            await asyncio.gather(*aio_tasks)
            break
        try:
            res = await results.get()
        except asyncio.CancelledError:
            for aio_task in aio_tasks:
                aio_task.cancel()
            raise
        # DEBUG
        # with open(TMP_PATH, 'a')as file:
        #     file.write(f'task:{tasks}\n')
        if isinstance(res, MailError):
            if is_error_retryable(res) and res.task.retries < cfg.max_retries:
                tasks.put_nowait(res.task)
            else:
                result_count += 1
                bound_widget.post_message(
                    ErrorOcurred(res, state)
                )
        else:
            result_count += 1
            bound_widget.post_message(
                ResultOcurred(res, state)
            )


async def dispatch_tasks(
    handler: _HandlerType[
        TaskT, ResultT
    ],
    tasks: _TaskQueueType[TaskT],
    results: _ResultQueueType[
        TaskT,
        ResultT
    ]
):
    async def logout():
        assert client is not None
        try:
            await client.logout()
        except IMAP_ERRORS:
            pass
        else:
            try:
                await client.close()
            except IMAP_ERRORS:
                pass

    async def put_error(
        error: MailError[TaskT],
    ):
        await results.put(error)
    account: Account | None = None
    mailbox: str | None = None
    client: aioimap.IMAP4_SSL | None = None
    while True:
        task = await tasks.get()
        if task is None:
            if client is not None:
                await logout()
            break
        task.retries += 1
        if account != task.account:
            mailbox = None
            if client is not None:
                await logout()
            try:
                client = await _create_client(
                    task.account.host,
                    task.account.port
                )
            except IMAP_ERRORS as exc:
                await put_error(
                    MailError(
                        task,
                        ErrorTypes.CONNECTION_FAILED,
                        IssueLevel.ERROR,
                        exc_to_dict(exc)
                    )
                )
                continue
            login_resp = await _request(client.login(
                task.account.address.address,
                task.account.password
            ), task)
            if isinstance(login_resp, MailError):
                await put_error(login_resp)
                continue
            id_resp = await _request(client.id(**IMAP4_ID), task)
            if isinstance(id_resp, MailError):
                await put_error(id_resp)
                continue
            account = task.account
        assert client is not None
        if mailbox != task.mailbox:
            select_resp = await _request(client.select(task.mailbox), task)
            if isinstance(select_resp, MailError):
                await put_error(select_resp)
                continue
            mailbox = task.mailbox
        try:
            res = await handler(client, task)
        except Exception as exc:
            await put_error(MailError(
                task,
                ErrorTypes.UNKNOWN,
                IssueLevel.ERROR,
                exc_to_dict(exc)
            ))
        else:
            if isinstance(res, MailError) and is_error_retryable(res):
                account = None
            await results.put(res)


async def search_messages(
    task_data: Iterable[MessageDataType],
    bound_widget: Widget,
):
    async def handler(
        client: aioimap.IMAP4_SSL,
        task: MessageTask
    ) -> _ResultType[MessageTask, list[str]]:
        def get_search_args() -> list[str]:
            res: list[str] = []
            if cfg.begin_search_date is not None:
                res.append('since')
                res.append(
                    mail_helpers.times_to_search_str(
                        *mail_helpers.date_to_times(
                            cfg.begin_search_date
                        )
                    )
                )
            if cfg.end_search_date is not None:
                res.append('before')
                res.append(
                    mail_helpers.times_to_search_str(
                        *mail_helpers.date_to_times(
                            cfg.end_search_date
                        )
                    )
                )
            res.append(cfg.message_type)
            return res

        def split_msgs(msg_num: bytes) -> list[str]:
            return msg_num.decode('utf-8').split()
        search_resp = await _request(client.search(*get_search_args()), task)
        if isinstance(search_resp, MailError):
            return search_resp
        res = Result(task, split_msgs(cast(bytes, search_resp.lines[0])))
        return res
    await launch_imap_tasks(
        task_data,
        lambda data: MessageTask(*data),
        handler,
        States.SEARCHING_MESSAGES,
        bound_widget
    )


async def search_normal_attachments(
    task_data: Iterable[NormalAttachmentDataType],
    bound_widget: Widget,
):
    async def handler(
        client: aioimap.IMAP4_SSL,
        task: NormalAttachmentTask
    ) -> _ResultType[NormalAttachmentTask, Attachments]:

        def lookup_normal_attachments(bodystructure: BodyStructureType):
            res: list[NonMultiPart] = []
            for part in bodystructure.walk():
                # We may use one email as an attachment to another email.
                # If the bodystructure parser can parse its structure correctly,
                # downloads attachments in itself, otherwise downloads itself.
                if isinstance(part, NonMultiPart):
                    if (
                        isinstance(part.envelope_body, NonMultiPart)
                        and part.envelope_body.main_type is not None
                        or isinstance(part.envelope_body, MultiPart)
                    ):
                        continue
                    if (
                        part.disposition is not None
                        and isinstance(
                            (attachment := part.disposition.get('attachment')),
                            dict
                        )
                        and isinstance(attachment.get('filename'), str)
                    ):
                        res.append(part)

            return res

        def lookup_html_parts(bodystructure: BodyStructureType):
            res: list[NonMultiPart] = []
            for part in bodystructure.walk():
                if (isinstance(part, NonMultiPart)
                    and (part.main_type, part.sub_type) == ('text', 'html')
                    and (part.disposition is None
                         or part.disposition.get('attachment') is None)):
                    res.append(part)
            return res
        envelope_resp = await _request(client.fetch(task.msg_num, 'envelope'), task, 2)
        if isinstance(envelope_resp, MailError):
            return envelope_resp
        envelope = load_envelope(_ensure_structure(
            _concat_response_lines(envelope_resp).decode('utf-8')
        ))
        if envelope.message_id is None:
            return MailError(
                task,
                ErrorTypes.MISSING_MSG_ID,
                IssueLevel.ERROR,
                envelope_to_dict(envelope),
                resp=envelope_resp
            )
        bodystructure_resp = await _request(client.fetch(task.msg_num, 'bodystructure'), task, 2)
        if isinstance(bodystructure_resp, MailError):
            return bodystructure_resp
        # DEBUG
        # async with aiofiles.open(TMP_PATH, 'ab') as file:
        #     for a in bodystructure_resp.lines:
        #         await file.write(b'next:')
        #         await file.write(a)
        #         await file.write(b'\n')
        bodystructure = load_bodystructure(_ensure_structure(
            _concat_response_lines(bodystructure_resp).decode('utf-8')
        ))
        normal_attachments = lookup_normal_attachments(bodystructure)
        html_parts = lookup_html_parts(bodystructure)
        return Result(task, Attachments(
            envelope,
            normal_attachments,
            html_parts
        ))
    await launch_imap_tasks(
        task_data,
        lambda data: NormalAttachmentTask(*data),
        handler,
        States.SEARCHING_NORMAL_ATTACHMENTS,
        bound_widget
    )


async def search_urls(
    task_data: Iterable[URLDataType],
    bound_widget: Widget,
):
    async def handler(
        client: aioimap.IMAP4_SSL,
        task: URLTask
    ) -> _ResultType[URLTask, set[str]]:
        # DEBUG
        async with aiofiles.open(TMP_PATH, 'wb') as file:
            # await file.write(f'{task.msg_num}:{task.html_part}\n')
            # await file.write(f'{task.envelope.subject}\n')
            envelope_resp = await _request(client.fetch(task.msg_num, 'envelope'), task, 2)
            if isinstance(envelope_resp, MailError):
                return envelope_resp
            envelope = load_envelope(_ensure_structure(
                _concat_response_lines(envelope_resp).decode('utf-8')
            ))
            if not _is_msg_same(task.envelope, envelope):
                return MailError(
                    task,
                    ErrorTypes.UNMATCHED_MSG_ID,
                    IssueLevel.ERROR,
                    envelope_to_dict(envelope),
                    resp=envelope_resp
                )
            fetch_resp = await _request(
                client.fetch(
                    task.msg_num,
                    f'body.peek[{task.html_part.section_str}]'
                ),
                task,
                4,
                task.html_part.size
            )
            if isinstance(fetch_resp, MailError):
                return fetch_resp
            data_bytes = cast(bytearray, fetch_resp.lines[1])
            assert task.html_part.params is not None
            charset = task.html_part.params.get('charset')
            assert isinstance(charset, str)
            encoding = task.html_part.encoding
            if encoding is None:
                encoding = '7bit'
            if encoding not in ('7bit', '8bit'):
                import codecs
                try:
                    data_bytes = cast(bytes, codecs.decode(
                        data_bytes, encoding))
                except ValueError:
                    return MailError(
                        task,
                        ErrorTypes.DECODE_ERROR,
                        IssueLevel.ERROR,
                        envelope_to_dict(envelope),
                        resp=envelope_resp
                    )
            try:
                data = data_bytes.decode(charset)
            except UnicodeDecodeError:
                return MailError(
                    task,
                    ErrorTypes.DECODE_ERROR,
                    IssueLevel.ERROR,
                    envelope_to_dict(envelope),
                    resp=envelope_resp
                )
            await file.writelines((
                data_bytes,
                b'\n'
            ))
            try:
                urls = crawler.get_urls(data)
            except ValueError:
                return MailError(
                    task,
                    ErrorTypes.HTML_PARSING_ERROR,
                    IssueLevel.WARNING,
                    envelope_to_dict(envelope),
                    resp=envelope_resp
                )
            urls = {
                url for url in urls if crawler.get_url_handler(url)
                is not None
            }
            return Result(
                task,
                urls
            )

    await launch_imap_tasks(
        task_data,
        lambda data: URLTask(*data),
        handler,
        States.SEARCHING_URLS,
        bound_widget
    )
