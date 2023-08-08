from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, auto
from itertools import chain
from typing import Any, Iterable, TypeAlias, TypeVar, Union, cast

import mailcore
from config import Account, get_backup_config, get_config, sync_config
from mailcore import crawler, mail
from path import TMP_PATH
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import (Center, Container, Middle, ScrollableContainer,
                                Vertical)
from textual.message import Message
from textual.reactive import reactive, var
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (Button, ContentSwitcher, Footer, Header, Label,
                             LoadingIndicator, OptionList, ProgressBar,
                             TabbedContent, TabPane, Tree)
from textual.worker import Worker, WorkerState
from utils.imap_structure import Envelope
from utils.mail_helpers import decode_header
from widgets.issue_viewer import (DetailsType, IssueLevel, IssueViewer,
                                  envelope_to_dict, exc_to_dict)

from .search_screen import SearchScreen

cfg = get_config()
_ErrorEventType: TypeAlias = mailcore.ErrorOcurred[Union[
    mail.MailError[Union[
        mailcore.MessageTask,
        mailcore.NormalAttachmentTask,
        mailcore.URLTask
    ]],
    crawler.CrawlerError,
]]
_MessageDictType: TypeAlias = dict[
    mail.MessageDataType,
    list[str] | None
]
_AttachmentDictType: TypeAlias = dict[
    mail.NormalAttachmentDataType,
    mail.Attachments | None
]


class States(Enum):
    IDLE = auto()
    SEARCHING = auto()
    WAITING_FOR_DOWNLOAD = auto()
    DOWNLOADING = auto()


class MailBoxPart(Container):
    async def on_mount(self):
        await self.reload()

    async def reload(self):
        await self.remove_children()
        tree: Tree[list[mail.MessageDataType]] = Tree(
            '所有收件箱', [], id='mailbox-tree')
        assert tree.root.data is not None
        for account, mailboxes in zip(cfg.accounts, cfg.mailboxes):
            account_node = tree.root.add(
                account.address.address,
                cast(list[mail.MessageDataType], [])
            )
            assert account_node.data is not None
            for mailbox in mailboxes:
                keys = (account, mailbox)
                account_node.add_leaf(mailbox, [keys])
                account_node.data.append(keys)
                tree.root.data.append(keys)
        tree.root.expand_all()
        await self.mount(tree)


class NoAttachmentsPart(ScrollableContainer):
    is_all = reactive(True)
    has_account = reactive(True)

    def compose(self) -> ComposeResult:
        with Center():
            yield Label('没有附件可下载.', id='search-text-1')
        with Center():
            yield Button('搜索附件', id='no-attachments-search-button')

    def watch_is_all(self, value: bool):
        res = self.query(Button)
        if value:
            res.remove_class('hidden')
        else:
            res.add_class('hidden')
    watch_has_account = watch_is_all


class SearchPart(ScrollableContainer):
    account_count = var(0, init=False)
    analyzed_account_count = var(0, init=False)
    msg_count = var(0, init=False)
    analyzed_msg_count = var(0, init=False)
    normal_attachment_count = var(0, init=False)
    html_part_count = var(0, init=False)
    analyzed_html_part_count = var(0, init=False)
    url_count = var(0, init=False)
    analyzed_url_count = var(0, init=False)
    large_attachment_count = var(0, init=False)
    issue_count = var(0, init=False)

    @dataclass
    class SearchResult:
        msg_count: int
        normal_attachment_count: int
        large_attachment_count: int
        issue_count: int
        attachment_dict: _AttachmentDictType

    @dataclass
    class SearchCompleted(Message):
        """Posted when search was completed."""
        part: 'SearchPart'
        res: 'SearchPart.SearchResult'

    @dataclass
    class SearchCancelled(Message):
        """Posted when search was cancelled."""
        part: 'SearchPart'

    def __init__(
        self,
        id: str | None = None
    ) -> None:
        super().__init__(id=id)
        self.timer = self.set_interval(0.5, self.update_prompt, pause=True)
        self.msg_dict: _MessageDictType = dict()
        self.attachment_dict: _AttachmentDictType = dict()
        self.msg_ids = set[str]()
        self.worker: Worker[None] | None = None

    def reset_data(self):
        from itertools import accumulate, cycle, repeat
        self.dot = cycle(accumulate(repeat('.', 3), initial=''))
        for k in self.msg_dict.keys():
            self.msg_dict[k] = None
        self.attachment_dict.clear()
        self.msg_ids.clear()
        self.worker = None
        self.account_count = len(self.msg_dict)
        self.analyzed_account_count = 0
        self.msg_count = 0
        self.analyzed_msg_count = 0
        self.normal_attachment_count = 0
        self.html_part_count = 0
        self.analyzed_html_part_count = 0
        self.url_count = 0
        self.analyzed_url_count = 0
        self.large_attachment_count = 0
        self.issue_count = 0

    def update_prompt(self):
        self.query_one('#search-prompt', Label).update(
            ':magnifying_glass_tilted_left:'
            f'正在搜索{next(self.dot):3}'
        )

    def update_state(self):
        self.query_one('#search-state', Label).update(
            ' '.join((
                f'{self.normal_attachment_count}:paperclip:',
                f'{self.large_attachment_count}:link:',
                f'{self.msg_count}:e-mail:',
                f'{self.issue_count}:heavy_exclamation_mark:',
            ))
        )

    @on(mailcore.ErrorOcurred)
    def error_ocurred(self, event: _ErrorEventType):
        if event.error.error_typ is not mailcore.ErrorTypes.IGNORE:
            self.issue_count += 1
        state = event.state
        match state:
            case mail.States.SEARCHING_MESSAGES:
                self.analyzed_account_count += 1
            case mail.States.SEARCHING_NORMAL_ATTACHMENTS:
                self.analyzed_msg_count += 1
            case (
                mail.States.SEARCHING_URLS
                | mail.States.SEARCHING_LARGE_ATTACHMENTS
            ):
                if state is mail.States.SEARCHING_URLS:
                    assert isinstance(event.error.task, mailcore.URLTask)
                    self.analyzed_html_part_count += 1
                else:
                    assert isinstance(
                        event.error.task,
                        mailcore.LargeAttachmentTask
                    )
                    self.analyzed_url_count += 1
                if event.error.level is not IssueLevel.ERROR:
                    return
                attachments = self.attachment_dict[
                    event.error.task.account,
                    event.error.task.mailbox,
                    event.error.task.msg_num
                ]
                assert attachments is not None
                attachments.error_ocurred = True

    @on(mailcore.ResultOcurred)
    def result_ocurred(
        self,
        event: mailcore.ResultOcurred[
            mailcore.TaskT,
            Any
        ]
    ):
        state = event.state
        match state:
            case mail.States.SEARCHING_MESSAGES:
                self.analyzed_account_count += 1
                self.msg_count += len(cast(list[str], event.res.res))
                self.msg_dict[
                    event.res.task.account,
                    event.res.task.mailbox
                ] = cast(list[str], event.res.res)
                for msg_num in event.res.res:
                    assert isinstance(msg_num, str)
                    self.attachment_dict[
                        event.res.task.account,
                        event.res.task.mailbox,
                        msg_num
                    ] = None
            case mail.States.SEARCHING_NORMAL_ATTACHMENTS:
                self.analyzed_msg_count += 1
                assert isinstance(
                    event.res.task, mailcore.NormalAttachmentTask)
                assert isinstance(event.res.res, mail.Attachments)
                assert event.res.res.html_parts is not None
                msg_id = event.res.res.envelope.message_id
                assert msg_id is not None
                # Filter duplicate emails.
                if msg_id not in self.msg_ids:
                    self.attachment_dict[
                        event.res.task.account,
                        event.res.task.mailbox,
                        event.res.task.msg_num
                    ] = event.res.res
                    self.normal_attachment_count += len(
                        event.res.res.normal_attachments
                    )
                    self.html_part_count += len(
                        event.res.res.html_parts
                    )
                self.msg_ids.add(msg_id)
            case mail.States.SEARCHING_URLS:
                self.analyzed_html_part_count += 1
                assert isinstance(event.res.task, mailcore.URLTask)
                attachments = self.attachment_dict[
                    event.res.task.account,
                    event.res.task.mailbox,
                    event.res.task.msg_num,
                ]
                assert attachments is not None
                res = cast(set[str], event.res.res)
                attachments._valid_urls.update(res)
                self.url_count += len(res)
            case mail.States.SEARCHING_LARGE_ATTACHMENTS:
                self.analyzed_url_count += 1
                assert isinstance(event.res.task, mailcore.LargeAttachmentTask)
                attachments = self.attachment_dict[
                    event.res.task.account,
                    event.res.task.mailbox,
                    event.res.task.msg_num,
                ]
                assert attachments is not None
                res = cast(
                    tuple[crawler.RequestMethodType, str],
                    event.res.res
                )
                attachments.large_attachments.append(res)
                self.large_attachment_count += 1

    @on(Worker.StateChanged)
    async def event_worker_changed(self, event: Worker.StateChanged):
        progress_bar = self.query_one(ProgressBar)
        if event.state is WorkerState.SUCCESS:
            match event.worker.name:
                case 'step1':
                    progress_bar.progress = 10
                    self._search_normal_attachments()
                case 'step2':
                    progress_bar.progress = 40
                    self._search_urls()
                case 'step3':
                    progress_bar.progress = 70
                    self._search_large_attachments()
                case 'step4':
                    progress_bar.progress = 100
                    await self._finalize()
                    self.post_message(self.SearchCompleted(self, self.SearchResult(
                        self.msg_count,
                        self.normal_attachment_count,
                        self.large_attachment_count,
                        self.issue_count,
                        self.attachment_dict
                    )))
                    self.app.bell()

    @on(Button.Pressed, '#search-cancel-button')
    async def event_search_cancel(self):
        if self.worker is None:
            return
        self.worker.cancel()
        await self._finalize()
        self.post_message(self.SearchCancelled(self))

    async def _finalize(self):
        self.worker = None
        self.app.sub_title = ''
        await cfg.finalize()

    async def search(
        self,
        msg_dict: Mapping[
            mail.MessageDataType,
            list[str] | None
        ],
    ):
        self.msg_dict = dict(msg_dict)
        self.timer.reset()
        self.timer.pause()
        await self.remove_children()
        await self.mount(
            Center(Label(id='search-prompt')),
            Center(ProgressBar(
                total=100,
                show_eta=False,
                id='search-progress'
            )),
            Center(Label(id='search-state')),
            Center(Button('取消', 'error', id='search-cancel-button'))
        )
        self.reset_data()
        self.update_prompt()
        self.update_state()
        self.timer.resume()
        self._search_messages()

    def _search_messages(self):
        self.worker = self.run_worker(
            mail.search_messages(self.msg_dict.keys(), self),
            'step1'
        )

    def _search_normal_attachments(self):
        self.worker = self.run_worker(
            mail.search_normal_attachments(
                self.attachment_dict.keys(), self
            ),
            'step2'
        )

    def _search_urls(self):
        self.worker = self.run_worker(
            mail.search_urls(
                (
                    (*k, v.envelope, part)
                    for k, v in self.attachment_dict.items()
                    if v is not None
                    for part in v.html_parts
                ),
                self
            ),
            'step3'
        )

    def _search_large_attachments(self):
        self.worker = self.run_worker(
            crawler.search_large_attachments(
                (
                    (*k, v.envelope, url) for k, v in self.attachment_dict.items()
                    if v is not None and not v.error_ocurred
                    for url in v._valid_urls
                ),
                self
            ),
            'step4'
        )

    def watch_msg_count(self):
        self.update_state()
    watch_normal_attachment_count = watch_msg_count
    watch_large_attachment_count = watch_msg_count
    watch_issue_count = watch_msg_count

    def watch_analyzed_account_count(
        self,
        analyzed_count: int
    ):
        if self.worker is None:
            return
        (self.query_one(ProgressBar).
         progress) = 10*(analyzed_count /
                         self.account_count)

    def watch_analyzed_msg_count(
        self,
        analyzed_count: int
    ):
        if self.worker is None:
            return
        (self.query_one(ProgressBar).
         progress) = 10+30*(analyzed_count /
                            self.msg_count)

    def watch_analyzed_html_part_count(
        self,
        analyzed_count: int
    ):
        if self.worker is None:
            return
        (self.query_one(ProgressBar).
         progress) = 40+30*(analyzed_count /
                            self.html_part_count)

    def watch_analyzed_url_count(
        self,
        analyzed_count: int
    ):
        if self.worker is None:
            return
        (self.query_one(ProgressBar).
         progress) = 70+30*(analyzed_count /
                            self.url_count)


def error_typ_reasons() -> dict[mailcore.ErrorTypes, str]:
    return {
        mailcore.ErrorTypes.CONNECTION_FAILED:  '连接失败',
        mailcore.ErrorTypes.DECODE_ERROR: '解码错误',
        mailcore.ErrorTypes.HANDLE_ERROR: '处理失败',
        mailcore.ErrorTypes.MISSING_MSG_ID: '邮件缺少标识',
        mailcore.ErrorTypes.HTML_PARSING_ERROR: '无法解析HTML片段',
        mailcore.ErrorTypes.LINK_SEARCHING_ERROR: '无法获取链接',
        mailcore.ErrorTypes.REQUEST_ERROR: '请求错误',
        mailcore.ErrorTypes.UNKNOWN: '未知错误',
        mailcore.ErrorTypes.UNMATCHED_MSG_ID: '邮件已变更',
    }


class MainScreen(Screen[None]):
    CSS_PATH = 'main_screen.css'
    BINDINGS = [
        Binding('s', 'push_search', 'Search'),
        Binding('f12', 'push_settings', 'Settings')
    ]

    def __init__(self) -> None:
        super().__init__()
        self.msg_dict: _MessageDictType = dict()
        self.state: States = States.IDLE
        self.search_res: SearchPart.SearchResult | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield MailBoxPart()
        with Container(), TabbedContent(
            initial='attachment-pane'
        ):
            with TabPane('附件', id='attachment-pane'):
                with ContentSwitcher(
                    id='attachment-pane-switcher',
                    initial='no-attachments'
                ):
                    yield NoAttachmentsPart(id='no-attachments')
                    yield SearchPart(id='search')
        yield Footer()

    async def action_push_search(self):
        if self.state is not States.IDLE:
            return
        if not self.msg_dict:
            return
        await sync_config(get_backup_config(), cfg)
        screen = SearchScreen()
        self.app.push_screen(screen, self.search_screen_callback)

    async def on_mount(self):
        tree: Tree[
            list[mail.MessageDataType]
        ] = self.query_one('#mailbox-tree', Tree)
        assert tree.root.data is not None
        for key in tree.root.data:
            self.msg_dict[key] = None
        self.query_one(NoAttachmentsPart).has_account = bool(self.msg_dict)

    @on(mailcore.ErrorOcurred)
    async def event_error_ocurred(self, event: _ErrorEventType):
        if event.error.error_typ is mailcore.ErrorTypes.IGNORE:
            return

        def add_common_category():
            viewer.add_category('common', '普通问题')
        reasons = error_typ_reasons()
        await self.add_error_pane()
        task = event.error.task
        error_typ = event.error.error_typ
        level = event.error.level
        viewer = self.query_one('#error-pane-issue-viewer', IssueViewer)
        details = event.error.details
        state = event.state
        match state:
            case mailcore.States.SEARCHING_MESSAGES:
                add_common_category()
                descr = '1个收件箱选中失败'
                viewer.add_item(
                    descr,
                    reasons[error_typ],
                    'common',
                    details,
                    level
                )
            case mailcore.States.SEARCHING_NORMAL_ATTACHMENTS:
                assert isinstance(task, mailcore.NormalAttachmentTask)
                add_common_category()
                descr = '1封邮件处理失败'

                viewer.add_item(
                    descr,
                    reasons[error_typ],
                    'common',
                    details,
                    level
                )
            case mailcore.States.SEARCHING_URLS:
                assert isinstance(task, mailcore.URLTask)
                add_common_category()
                descr = '1封邮件部分处理失败'
                viewer.add_item(
                    descr,
                    reasons[error_typ],
                    'common',
                    details,
                    level
                )
            case mailcore.States.SEARCHING_LARGE_ATTACHMENTS:
                assert isinstance(task, mailcore.LargeAttachmentTask)
                add_common_category()
                descr = '1个链接处理失败'
                viewer.add_item(
                    descr,
                    reasons[error_typ],
                    'common',
                    details,
                    level
                )

    @on(Button.Pressed, '#no-attachments-search-button')
    async def event_push_search(self):
        await self.run_action('push_search')

    @on(SearchPart.SearchCompleted)
    def event_search_complete(self, event: SearchPart.SearchCompleted):
        res = event.res
        self.search_res = res
        if res.normal_attachment_count+res.large_attachment_count == 0:
            self.state = States.IDLE
            self.app.notify(
                '没有附件可下载.',
                title='搜索完成',
                severity='information'
            )
            switcher = self.query_one(
                '#attachment-pane-switcher',
                ContentSwitcher
            )
            switcher.current = 'no-attachments'
        else:
            self.state = States.WAITING_FOR_DOWNLOAD
        event.part.reset_data()

    @on(SearchPart.SearchCancelled)
    def event_search_cancel(self, event: SearchPart.SearchCancelled):
        self.state = States.IDLE
        switcher = self.query_one('#attachment-pane-switcher', ContentSwitcher)
        switcher.current = 'no-attachments'
        self.remove_error_pane()

    @on(Tree.NodeSelected, '#mailbox-tree')
    def event_node_selected(self, event: Tree.NodeSelected):
        if event.node.is_expanded:
            tree_root = self.query_one('#mailbox-tree', Tree).root
            self.query_one(NoAttachmentsPart).is_all = event.node == tree_root

    async def add_error_pane(self):
        tab = self.query_one(TabbedContent)
        if not tab.query('#error-pane'):
            await tab.add_pane(TabPane(
                '问题',
                IssueViewer(id='error-pane-issue-viewer'),
                id='error-pane'
            ))

    def remove_error_pane(self):
        self.query_one(TabbedContent).remove_pane('error-pane')

    @on(IssueViewer.ItemRemove)
    def event_issue_item_remove(self, event: IssueViewer.ItemRemove):
        if sum(event.viewer.get_levels().values()) == 0:
            self.remove_error_pane()

    async def search_screen_callback(self, res: bool):
        if not res or self.state is not States.IDLE:
            return
        self.state = States.SEARCHING
        self.app.sub_title = '正在搜索'
        switcher = self.query_one('#attachment-pane-switcher', ContentSwitcher)
        switcher.current = 'search'
        search_part = self.query_one('#search', SearchPart)
        self.remove_error_pane()
        await search_part.search(self.msg_dict)
