from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit

from filemind.services.application import ApplicationService
from filemind.ui.widgets import Card, Page, button, label


class AssistantPage(Page):
    def __init__(self, service: ApplicationService):
        super().__init__("AI 助手", "以对话探索整理思路。当前为本地交互演示，未连接模型服务。")
        self.service = service
        intro = Card(hero=True)
        intro.body.addWidget(label("从一个整理想法开始", "HeroTitle"))
        intro.body.addWidget(label("例如：如何整理下载目录？怎样统一项目文件的命名？", "Muted"))
        intro.body.addWidget(label("演示回答为固定文本，不会发送问题或文件信息。", "Badge"))
        self.body.addWidget(intro)
        conversation = Card("对话演示")
        conversation.body.addWidget(label("你的问题"))
        self.question = QLineEdit()
        self.question.setMaxLength(500)
        self.question.setAccessibleName("输入演示问题")
        self.question.setPlaceholderText("输入一个文件整理问题…")
        conversation.body.addWidget(self.question)
        actions = QHBoxLayout()
        self.send_button = button("查看演示回答", primary=True)
        actions.addWidget(self.send_button)
        actions.addStretch()
        conversation.body.addLayout(actions)
        self.answer = label("尚未开始演示。输入问题后可查看本地固定回答。", "Muted")
        self.answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        conversation.body.addWidget(self.answer)
        self.send_button.clicked.connect(self.reply)
        self.question.returnPressed.connect(self.reply)
        self.body.addWidget(conversation)
        self.finish()

    def reply(self) -> None:
        self.answer.setText(self.service.demo.assistant_reply(self.question.text()))
