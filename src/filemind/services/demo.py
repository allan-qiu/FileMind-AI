from filemind.domain.models import FileRecord, OrganizationProposal


class DemoService:
    """Deterministic in-memory fixtures; sample paths are never dereferenced."""

    def preview(self, query: str = "") -> tuple[OrganizationProposal, ...]:
        samples = (
            (
                "项目方案_final_v3.pdf",
                "文档",
                "项目方案.pdf",
                "文档 / 项目资料",
                "按扩展名演示分类",
            ),
            (
                "IMG_20261018_0912.jpg",
                "图片",
                "旅行照片_01.jpg",
                "图片 / 旅行",
                "按样例标签演示命名",
            ),
            ("月度支出.xlsx", "表格", "月度支出.xlsx", "文档 / 财务", "按样例用途演示分类"),
            ("灵感笔记.txt", "文档", "灵感笔记.txt", "文档 / 笔记", "保留清晰的原始名称"),
            ("品牌素材.zip", "压缩包", "品牌素材.zip", "归档 / 素材", "仅演示，不解压文件"),
            ("产品演示.mp4", "视频", "产品演示.mp4", "视频 / 产品", "按扩展名演示分类"),
        )
        return tuple(
            OrganizationProposal(
                source=FileRecord(
                    path=f"demo://inbox/{name}",
                    name=name,
                    extension="." + name.rsplit(".", 1)[1],
                    size_bytes=1024 * (index + 1),
                ),
                category=category,
                suggested_name=renamed,
                target_folder=folder,
                reason=reason,
                is_demo=True,
            )
            for index, (name, category, renamed, folder, reason) in enumerate(samples)
            if query.casefold() in f"{name} {category} {folder}".casefold()
        )

    def assistant_reply(self, question: str) -> str:
        if not question.strip():
            return "请输入一个问题，再查看演示回答。"
        return (
            "【本地固定演示回答 · 未调用 AI】\n\n"
            "建议先按文档、图片、表格和视频建立分类，再统一文件名的格式。"
            "未来版本会先生成整理预览，确认后才执行操作。\n\n"
            "此回答仅为固定示例，未分析你的文件；真实分类与操作请使用智能整理并查看日志。"
        )
