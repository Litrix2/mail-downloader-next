from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import var
from textual.widgets import Label, Switch


class AutoHorizontal(Horizontal):
    DEFAULT_CSS = '''
    AutoHorizontal {
        width: auto;
        height: auto;
    }
    '''


class SwitchWithLabel(AutoHorizontal):
    DEFAULT_CSS = '''
    SwitchWithLabel {
        background: $boost;
        height: 3;
    }
    SwitchWithLabel Label {
        height: 1fr;
        content-align-vertical: middle; 
    }
    '''
    enabled = var(True, init=False)

    class Changed(Message):
        def __init__(self, enabled: bool) -> None:
            super().__init__()
            self.enabled = enabled

    def __init__(self, text: str | tuple[str, str], enabled: bool = True):
        super().__init__()
        if isinstance(text, str):
            text = (text, text)
        self.text = text
        self.enabled = enabled

    @on(Changed)
    def _change(self):
        self.query_one(Label).update(self.text[self.enabled])
        self.query_one(Switch).value = self.enabled

    @on(Switch.Changed)
    def _changed(self, event: Switch.Changed):
        event.stop()
        self.enabled = event.value

    def watch_enabled(self, enabled: bool):
        self.post_message(self.Changed(enabled))

    def compose(self) -> ComposeResult:
        yield Label(self.text[self.enabled])
        yield Switch(self.enabled)
