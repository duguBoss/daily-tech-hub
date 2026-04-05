import unittest

from daily_tech.title_history import generate_unique_title


class TitleHistoryTest(unittest.TestCase):
    def test_generate_unique_title_avoids_history_duplicates(self) -> None:
        items = [
            {"资讯标题": "美团发布原生多模态大模型LongCat-Next并开源核心架构"},
            {"资讯标题": "Google DeepMind发布Gemma 4开源模型系列，采用Apache 2.0协议"},
        ]
        history = [
            {"date": "2026-04-04", "title": "美团发布原生多模态大模型LongCat-Next并开源核心架构"},
        ]

        title = generate_unique_title(items, history)

        self.assertNotEqual(title, history[0]["title"])
        self.assertIn("LongCat-Next", title)


if __name__ == "__main__":
    unittest.main()
