from collections.abc import Iterable
from typing import Any, Generic, TypeVar, cast

import utils.mail_helpers as helpers
from config import (MESSAGE_TYPES, Config, get_backup_config, get_config,
                    sync_config)
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.message import Message
from textual.reactive import var
from textual.screen import Screen
from textual.validation import Regex, ValidationResult, Validator
from textual.widget import Widget
from textual.widgets import (Button, Checkbox, Footer, Header, Input, Label,
                             Select, Static, Switch)
from widgets.settings import (BaseSettingItem, BaseSettings,
                              EnablingInputSettingItem, SelectSettingItem)
from widgets.switch_label import AutoHorizontal, SwitchWithLabel

cfg = get_config()


class DateValidator(Validator):

    def __init__(self) -> None:
        super().__init__('无效日期格式')

    def validate(self, value: str) -> ValidationResult:
        try:
            helpers.input_str_to_times(value)
            return self.success()
        except ValueError:
            return self.failure()


class DateInputSettingItem(EnablingInputSettingItem):
    value: var[str]

    def on_mount(self) -> None:
        input = self.query_one(Input)
        input.validators.append(DateValidator())
        self.validate()

    def validate(self) -> None:
        input = self.query_one(Input)
        validation_res = input.validate(input.value)
        assert validation_res is not None
        self.error = not validation_res.is_valid

    @on(SwitchWithLabel.Changed)
    def switch_changed(self, event: SwitchWithLabel.Changed) -> None:
        # This function is called before the base class
        # and 'self.enabled' hasn't changed.
        if event.enabled:
            self.validate()
        else:
            self.error = False

    @on(Input.Changed)
    def input_changed(self, event: Input.Changed) -> None:
        if event.validation_result is not None:
            self.validate()


class SearchSettings(BaseSettings):
    def compose(self) -> ComposeResult:
        descr = '内容应为 "YYYY-mm-dd"的形式.'
        begin_date = cfg.begin_search_date
        if begin_date is not None:
            begin_date = helpers.times_to_input_str(
                *helpers.date_to_times(begin_date)
            )
        yield DateInputSettingItem(
            '起始日期',
            descr,
            enabled=begin_date is not None,
            initial=begin_date,
            id='setting-begin-date')
        end_date = cfg.end_search_date
        if end_date is not None:
            end_date = helpers.times_to_input_str(
                *helpers.date_to_times(end_date)
            )
        yield DateInputSettingItem(
            '截止日期',
            descr,
            enabled=end_date is not None,
            initial=end_date,
            id='setting-end-date')
        yield SelectSettingItem(
            '邮件类型',
            zip(
                ['所有邮件', '未读邮件', '已读邮件'],
                MESSAGE_TYPES
            ),
            cfg.message_type,
            id='setting-message-type'
        )

    def apply_settings(self):
        setting_begin_date = self.query_one('#setting-begin-date')
        assert isinstance(setting_begin_date, DateInputSettingItem)
        if setting_begin_date.enabled:
            cfg.begin_search_date = helpers.times_to_date(
                *helpers.input_str_to_times(setting_begin_date.value)
            )
        else:
            cfg.begin_search_date = None
        setting_end_date = self.query_one('#setting-end-date')
        assert isinstance(setting_end_date, DateInputSettingItem)
        if setting_end_date.enabled:
            cfg.end_search_date = helpers.times_to_date(
                *helpers.input_str_to_times(setting_end_date.value)
            )
        else:
            cfg.end_search_date = None
        setting_message_type = self.query_one('#setting-message-type')
        assert isinstance(setting_message_type, SelectSettingItem)
        assert setting_message_type.value is not None
        cfg.message_type = setting_message_type.value


class SearchScreen(Screen[bool]):
    BINDINGS = [
        Binding('ctrl+b', 'dismiss', 'Back')
    ]
    CSS_PATH = 'search_screen.css'

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes='header'):
            yield Label('选择搜索类型.')
        yield SearchSettings()
        with AutoHorizontal():
            yield Button('搜索', variant='primary', id='search-button')
            yield Button('取消', variant='error', id='cancel-button')
        yield Footer()

    @on(Button.Pressed, '#search-button')
    def search(self):
        from textual.app import ScreenStackError
        self.query_one(SearchSettings).apply_settings()
        try:
            self.dismiss(True)
        except ScreenStackError:
            pass

    @on(Button.Pressed, '#cancel-button')
    def cancel(self):
        self.dismiss(False)

    @on(BaseSettingItem.ErrorChanged)
    def error_changed(self):
        self.query_one(
            '#search-button').disabled = self.query_one(SearchSettings).error
