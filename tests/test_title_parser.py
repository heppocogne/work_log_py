import unittest

from activity_log.title_parser import parse


class TitleParserTest(unittest.TestCase):
    def test_powerpoint(self) -> None:
        p = parse("POWERPNT.EXE", "手順書A.pptx - PowerPoint")
        self.assertEqual((p.app, p.file), ("PowerPoint", "手順書A.pptx"))
        p = parse("POWERPNT.EXE", "資料 [互換モード] - PowerPoint")
        self.assertEqual(p.file, "資料")

    def test_excel_readonly(self) -> None:
        p = parse("EXCEL.EXE", "一覧.xlsx  -  読み取り専用 - Excel")
        self.assertEqual(p.file, "一覧.xlsx")

    def test_vscode(self) -> None:
        p = parse("Code.exe", "● memo.md - work - Visual Studio Code")
        self.assertEqual((p.file, p.workspace, p.label), ("memo.md", "work", "memo.md (work)"))
        p = parse("Code.exe", "work - Visual Studio Code")
        self.assertEqual((p.file, p.workspace), (None, "work"))

    def test_remote_desktop(self) -> None:
        p = parse("mstsc.exe", "server01 - リモート デスクトップ接続")
        self.assertEqual((p.app, p.host, p.label), ("リモート", "server01", "server01"))

    def test_other(self) -> None:
        p = parse("chrome.exe", "Google - Google Chrome")
        self.assertEqual((p.app, p.label), ("Chrome", "Google - Google Chrome"))
        p = parse(None, "何か")
        self.assertEqual((p.app, p.label), ("(不明)", "何か"))
        p = parse("POWERPNT.EXE", "PowerPoint")  # 形式が違えばそのまま
        self.assertEqual(p.label, "PowerPoint")


if __name__ == "__main__":
    unittest.main()
