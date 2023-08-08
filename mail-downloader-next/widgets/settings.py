from collections.abc import Iterable
from typing import Any, Generic, TypeVar, cast

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, ScrollableContainer
from textual.message import Message
from textual.reactive import var
from textual.widgets import Input, Label, Select

from .switch_label import SwitchWithLabel

SelectOptionT = TypeVar('SelectOptionT')


class BaseSettingItem(Container):
    DEFAULT_CSS = '''
    BaseSettingItem {
        height: auto;
        margin-bottom: 1;
    }
    BaseSettingItem>.hidden {
        display: none;
    }
    BaseSettingItem * {
        max-width: 50;
    }
    BaseSettingItem>.title {
        text-style: bold;
    }
    BaseSettingItem>.description {
        color: grey;
    }
    BaseSettingItem.error>.title {
        color: $error;
    }
    '''
    error: var[bool] = var(False)
    value: var[Any] = var(None)

    class ErrorChanged(Message):
        pass

    def __init__(self, title: str, description: str = '', id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.title = title
        self.description = description

    def watch_error(self, error: bool):
        if error:
            self.add_class('error')
        else:
            self.remove_class('error')
        self.post_message(self.ErrorChanged())

    def compose(self) -> ComposeResult:
        yield Label(self.title, classes='title')
        yield Label(self.description, classes=' '.join((
            'description',
            '' if self.description else 'hidden',
        )))


class EnablingInputSettingItem(BaseSettingItem):
    value: var[str]
    DEFAULT_CSS = '''
    EnablingInputSettingItem>SwitchWithLabel {
        margin-left: 1;
    }
    EnablingInputSettingItem>.hidden {
        display: none;
    }
    '''

    def __init__(
        self,
        title: str,
        description: str = '',
        enabled: bool = True,
        initial: str | None = None,
        id: str | None = None,
        classes: str | None = None
    ) -> None:
        super().__init__(title, description, id=id, classes=classes)
        self.enabled = enabled
        self.initial = initial

    @on(SwitchWithLabel.Changed)
    def switch_changed(self, event: SwitchWithLabel.Changed) -> None:
        self.enabled = event.enabled
        input = self.query_one(Input)
        if self.enabled:
            input.remove_class('hidden')
        else:
            input.add_class('hidden')

    @on(Input.Changed)
    def input_changed(self, event: Input.Changed) -> None:
        self.value = event.value

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield SwitchWithLabel(('禁用', '启用'), self.enabled)
        yield Input(self.initial)


class SelectSettingItem(BaseSettingItem, Generic[SelectOptionT]):
    value: var[SelectOptionT | None]

    def __init__(
        self,
        title: str,
        options: Iterable[tuple[str, SelectOptionT]],
        initial: SelectOptionT | None = None,
        description: str = '',
        id: str | None = None,
        classes: str | None = None
    ) -> None:
        super().__init__(title, description, id=id, classes=classes)
        self.options = list(options)
        self.initial = initial
        self.value = initial

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield Select(self.options, prompt='选择项', allow_blank=False, value=self.initial)

    @on(Select.Changed)
    def select_changed(self, event: Select.Changed):
        self.value = cast(SelectOptionT | None, event.value)


class BaseSettings(ScrollableContainer):
    DEFAULT_CSS = '''
    BaseSettings {
        border: heavy $accent;
    }
    BaseSettings.error {
        border: heavy $error;
    }
    '''
    error: var[bool] = var(False)

    @on(BaseSettingItem.ErrorChanged)
    def error_changed(self) -> None:
        from operator import attrgetter
        self.error = any(
            map(attrgetter('error'), self.query(BaseSettingItem)))

    def watch_error(self, error: bool) -> None:
        if error:
            self.add_class('error')
        else:
            self.remove_class('error')

    def compose(self) -> ComposeResult:
        raise NotImplementedError()

    def apply_settings(self) -> None:
        raise NotImplementedError()
