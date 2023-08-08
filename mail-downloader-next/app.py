import events
from config import NAME, Config
from screens.main_screen import MainScreen
from screens.search_screen import SearchScreen
from textual import on
from textual.app import App
from textual.driver import Driver
from textual.widgets import Footer, Header


class MailDownloaderNextApp(App[None]):
    TITLE = NAME

    def on_mount(self):
        self.push_screen(MainScreen())
