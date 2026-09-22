from PyQt6.QtWidgets import (
    QMainWindow,
)

from modules.settings.settings_page import (
    SettingsPage,
)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "masGold v2"
        )

        self.resize(
            1200,
            760,
        )

        self.settings_page = SettingsPage()

        self.setCentralWidget(
            self.settings_page
        )