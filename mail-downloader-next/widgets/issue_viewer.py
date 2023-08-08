from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Generic, NamedTuple, TypeAlias, TypeVar, cast

from rich.console import RenderableType
from rich.segment import Segment
from rich.style import Style
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, HorizontalScroll, ScrollableContainer
from textual.message import Message
from textual.screen import ModalScreen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import (Button, Footer, Label, OptionList, ProgressBar,
                             Select, Static, Tree)
from textual.widgets.tree import TreeNode
from utils.imap_structure import Envelope
from utils.mail_helpers import decode_header


class IssueLevel(Enum):
    ERROR = ':cross_mark:'
    WARNING = ':warning:'
    INFO = ':information:'


_BOLD_FMT = '[bold]{}[/]'
_T = TypeVar('_T')
DetailsType: TypeAlias = dict[str, str | None]


class _IssueItemData(NamedTuple):
    description: str
    reason: str
    category: str
    details: DetailsType | None
    level: IssueLevel


class _DismissState(Enum):
    BACK = auto()
    CLEAR = auto()
    CUSTOM = auto()


_ItemNodeType: TypeAlias = TreeNode[_IssueItemData]


class _CategoryData(NamedTuple):
    category: str
    levels: defaultdict[IssueLevel, int]
    level_nodes: dict[IssueLevel, _ItemNodeType]


_CategoryNodeType: TypeAlias = TreeNode[_CategoryData]


class _IssueScreenDismissValue(NamedTuple, Generic[_T]):
    state: _DismissState
    node: _ItemNodeType
    extra: _T | None


class _CategoryScreenDismissValue(NamedTuple, Generic[_T]):
    state: _DismissState
    data: _CategoryData
    extra: _T | None


class _BaseIssueScreen(ModalScreen[_T]):
    BINDINGS = (
        Binding('escape', 'dismiss', 'Back'),
    )
    DEFAULT_CSS = '''
    _BaseIssueScreen {
        align: center middle;
    }
    _BaseIssueScreen>ScrollableContainer {
        width: 70%;
        height: 70%;
        background: $surface;
        border: heavy $accent;
    }
    _BaseIssueScreen .bottom {
        height: auto;
        dock: bottom;
    }
    _BaseIssueScreen .bottom Button {
        margin-right: 1;
    }
    '''

    def compose(self) -> ComposeResult:
        yield Footer()


def envelope_to_dict(envelope: Envelope) -> DetailsType:
    from email.utils import parsedate_to_datetime

    def add_single_item(key: str, value: str | None) -> None:
        if value is not None:
            res[key] = value

    def add_items(key: str, values: Iterable[str] | None):
        if values is not None:
            res[key] = ', '.join(values)
    res: DetailsType = {}
    subject = envelope.subject
    add_single_item(
        '主题',
        decode_header(subject)
        if subject is not None
        else None
    )
    try:
        date = parsedate_to_datetime(envelope.date)
    except ValueError:
        pass
    else:
        if date is not None:
            add_single_item('日期', str(
                date
            ))
    add_items(
        '发件人',
        (str(address) for address in envelope.sender)
        if envelope.sender is not None else None
    )
    add_items(
        '收件人',
        (str(address) for address in envelope.to)
        if envelope.to is not None else None
    )
    return res


def exc_to_dict(exc: Exception) -> DetailsType:
    res: DetailsType = {}
    res['异常类型'] = type(exc).__name__
    value = str(exc)
    if value:
        res['信息'] = value
    return res


def issue_dict_to_str(
    dict_: defaultdict[IssueLevel, int]
) -> str:
    return ' '.join(
        f'{dict_[level]}{level.value}'
        for level in IssueLevel
    )


class IssueScreen(_BaseIssueScreen[_IssueScreenDismissValue[_T]]):
    DEFAULT_CSS = '''
    IssueScreen .description{
        text-style: bold;
    }
    IssueScreen .reason {
        margin-bottom: 1;
    }
    IssueScreen .error {
        color: $error;
    }
    IssueScreen .warning {
        color: $warning;
    }
    '''
    LEVEL_CLASSES = {
        IssueLevel.ERROR: 'error',
        IssueLevel.WARNING: 'warning',
        IssueLevel.INFO: 'info'
    }

    def __init__(
        self,
        data: _IssueItemData,
        node: _ItemNodeType
    ) -> None:
        super().__init__()
        self._data = data
        self._node = node

    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            yield Label((
                f'{self._data.level.value} '
                f'{self._data.description}.'
            ), classes=' '.join((
                'description',
                self.LEVEL_CLASSES.get(self._data.level, '')
            )))
            yield Label(
                f'{_BOLD_FMT.format("原因: ")}{self._data.reason}.',
                classes=' '.join((
                    'reason',
                    self.LEVEL_CLASSES.get(self._data.level, '')
                )))
            details = self._data.details
            if details is not None:
                for k, v in details.items():
                    yield Label(
                        '{}{}'.format(
                            _BOLD_FMT.format(f'{k}: ')
                            if v is not None
                            else k,
                            v if v is not None
                            else ''
                        ),
                        classes='detail'
                    )
            with HorizontalScroll(classes='bottom'):
                yield Button('返回', variant='primary', classes='back-btn')
                yield Button('清除', classes='clear-btn')
        yield from super().compose()

    @on(Button.Pressed, '.back-btn')
    def event_back(self):
        self.dismiss(_IssueScreenDismissValue(
            _DismissState.BACK, self._node, None
        ))

    @on(Button.Pressed, '.clear-btn')
    def event_clear(self):
        self.dismiss(_IssueScreenDismissValue(
            _DismissState.CLEAR, self._node, None
        ))


class CategoryScreen(_BaseIssueScreen[
    _CategoryScreenDismissValue[_T]
]):
    DEFAULT_CSS = '''
    CategoryScreen>ScrollableContainer {
        width: 50%;
        height: 8;
    }
    '''

    def __init__(
            self,
            data: _CategoryData,
            node: _CategoryNodeType
    ):
        super().__init__()
        self._data = data
        self._node = node

    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            yield Label(
                f'{_BOLD_FMT.format("类别: ")}{self._node.label}',
                classes='category'
            )
            yield Label(
                issue_dict_to_str(self._data[1]),
                classes='level'
            )
            with HorizontalScroll(classes='bottom'):
                yield Button('返回', variant='primary', classes='back-btn')
                yield Button('清除全部', classes='clear-btn')
        yield from super().compose()

    @on(Button.Pressed, '.back-btn')
    def event_back(self):
        self.dismiss(_CategoryScreenDismissValue(
            _DismissState.BACK, self._data, None
        ))

    @on(Button.Pressed, '.clear-btn')
    def event_clear(self):
        self.dismiss(_CategoryScreenDismissValue(
            _DismissState.CLEAR, self._data, None
        ))


class IssueViewer(Widget):
    DEFAULT_CSS = '''
    IssueViewer {
        width: 1fr;
        height: 1fr;
        border: heavy $accent;
    }
    IssueViewer Tree {
        background: $surface;
    }
    IssueViewer .footer {
        background: $primary-background-darken-1;
        height: auto;
        dock: bottom;
    }
    '''

    @dataclass
    class ItemRemove(Message):
        viewer: 'IssueViewer'

    def __init__(self, id: str | None = None, classes: str | None = None):
        super().__init__(id=id, classes=classes)
        self._categories = dict[str, tuple[str, _CategoryData]]()
        self._category_nodes = dict[str, TreeNode[
            _IssueItemData | _CategoryData
        ]]()

    def compose(self) -> ComposeResult:
        yield Tree('问题', classes='tree')
        with Container(classes='footer'):
            yield Label(classes='footer-label')

    def on_mount(self):
        self.update_footer()

    def get_levels(self, category: str | None = None) -> defaultdict[IssueLevel, int]:
        from operator import itemgetter
        res: defaultdict[IssueLevel, int] = defaultdict(int)
        if category is None:
            datas = map(itemgetter(1), self._categories.values())
        else:
            datas = (self._categories[category][1],)
        for data in datas:
            for level, count in data.levels.items():
                res[level] += count
        return res

    def update_footer(self):
        self.query_one(
            '.footer-label',
            Label
        ).update(
            issue_dict_to_str(self.get_levels())
        )

    def add_category(self, category: str, label: str | None):
        if category in self._categories:
            return
        root = self.query_one(Tree).root
        if label is None:
            label = category
        self._categories[category] = (
            label, _CategoryData(
                category,
                defaultdict[
                    IssueLevel, int
                ](int),
                dict[IssueLevel, _ItemNodeType]()
            )
        )
        root.expand()

    def remove_category(self, category: str):
        self._categories.pop(category)
        if category in self._category_nodes:
            self._category_nodes.pop(category).remove()
        self.update_footer()
        self.post_message(self.ItemRemove(self))

    def add_item(
        self,
        description: str,
        reason: str,
        category: str,
        details: DetailsType | None = None,
        level: IssueLevel = IssueLevel.INFO
    ):
        root = self.query_one(Tree).root
        item_data = _IssueItemData(
            description,
            reason,
            category,
            details,
            level
        )
        (
            category_node_label,
            category_data
        ) = self._categories[category]
        category_node = self._category_nodes.get(category)
        if category_node is None:
            category_node = root.add(
                category_node_label,
                category_data
            )
            self._category_nodes[category] = category_node
        level_node = category_data.level_nodes.get(level)
        if level_node is None:
            level_node = cast(
                _ItemNodeType,
                category_node.add(level.value)
            )
            category_data.level_nodes[level] = level_node

        level_node.add_leaf(
            f'{level.value} {description}: {reason}.',
            item_data
        ).expand()
        category_data.levels[level] += 1
        category_node.expand()
        level_node.expand()
        self.update_footer()

    def remove_item(self, node: _ItemNodeType):
        node.remove()
        assert node.data is not None
        category = node.data.category
        _, category_data = self._categories[category]
        level = node.data.level
        category_data.levels[level] -= 1
        if category_data.levels[level] == 0:
            category_data.level_nodes[level].remove()
        if sum(category_data.levels.values()) == 0:
            self.remove_category(category)
        self.update_footer()
        self.post_message(self.ItemRemove(self))

    def issue_screen_callback(self, value: _IssueScreenDismissValue[Any]):
        match value.state:
            case _DismissState.CLEAR:
                self.remove_item(value.node)

    def category_screen_callback(self, value: _CategoryScreenDismissValue[Any]):
        match value.state:
            case _DismissState.CLEAR:
                self.remove_category(value.data.category)

    @on(Tree.NodeSelected)
    def event_node_selected(self, event: Tree.NodeSelected):
        event.stop()
        if event.node is self.query_one(Tree).root:
            return
        if not event.node.is_expanded:
            return

        data = cast(
            _IssueItemData | _CategoryData | None,
            event.node.data
        )
        match data:
            case _IssueItemData():
                self.app.push_screen(
                    IssueScreen[None](data, event.node),
                    self.issue_screen_callback
                )
            case _CategoryData():
                self.app.push_screen(
                    CategoryScreen[None](data, event.node),
                    self.category_screen_callback
                )
