import rich.traceback
import utils.patches as patches
from config import load_config
from path import TMP_PATH, set_cwd


def run():
    from app import MailDownloaderNextApp
    MailDownloaderNextApp().run()


if __name__ == '__main__':
    rich.traceback.install()
    patches.install()
    set_cwd(__file__)
    # load_config('config/config.toml')
    load_config('config/_config.toml')  # DEBUG
    run()
