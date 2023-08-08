from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Generic, TypeVar

from config import Account
from textual.message import Message as TextualMessage
from utils.imap_structure import Envelope, NonMultiPart
from widgets.issue_viewer import DetailsType, IssueLevel


# Enums.
class ErrorTypes(Enum):
    IGNORE = auto()
    """Ignore the error."""

    CONNECTION_FAILED = auto()
    DECODE_ERROR = auto()
    HANDLE_ERROR = auto()
    HTML_PARSING_ERROR = auto()
    LINK_SEARCHING_ERROR = auto()
    MISSING_MSG_ID = auto()
    REQUEST_ERROR = auto()
    UNKNOWN = auto()
    UNMATCHED_MSG_ID = auto()


class States(Enum):
    SEARCHING_MESSAGES = auto()
    SEARCHING_NORMAL_ATTACHMENTS = auto()
    SEARCHING_URLS = auto()
    SEARCHING_LARGE_ATTACHMENTS = auto()


# Tasks.
@dataclass
class MessageTask:
    account: Account
    mailbox: str

    def __post_init__(self):
        self.retries = 0


TaskT = TypeVar('TaskT', bound=MessageTask)


@dataclass
class NormalAttachmentTask(MessageTask):
    msg_num: str


@dataclass
class URLTask(NormalAttachmentTask):
    envelope: Envelope
    html_part: NonMultiPart


@dataclass
class LargeAttachmentTask(NormalAttachmentTask):
    envelope: Envelope
    url: str


# Results and errors.
ResultT = TypeVar('ResultT')


@dataclass
class Result(Generic[TaskT, ResultT]):
    task: TaskT
    res: ResultT


@dataclass
class ResultOcurred(TextualMessage, Generic[
    TaskT,
    ResultT
]):
    res: Result[TaskT, ResultT]
    state: States


@dataclass
class BaseError(Generic[TaskT]):
    task: TaskT
    error_typ: ErrorTypes
    level: IssueLevel
    details: DetailsType | None = None


ErrorT = TypeVar('ErrorT', bound=BaseError)


@dataclass
class ErrorOcurred(
    TextualMessage,
    Generic[ErrorT]
):
    error: ErrorT
    state: States


def is_error_retryable(error: BaseError[TaskT]):
    return error.error_typ is ErrorTypes.CONNECTION_FAILED
