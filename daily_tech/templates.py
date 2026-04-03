"""HTML template generators for WeChat content."""
from __future__ import annotations

import html
import re
from typing import Dict, List, Tuple

from daily_tech.utils import compact_text


def build_weixin_html_template(items: List[Dict]) -> Tuple[str, str]:
    if not items:
        return "", ""

    title_list_html = ""
    content_html = ""
    title_list: List[str] = []

    for index, item in enumerate(items, 1):
        title_raw = compact_text(item.get("资讯标题", ""))
        content_raw = compact_text(item.get("内容", ""))
        image_url = item.get("配图", "")
        if not title_raw or not content_raw:
            continue
        title_list.append(title_raw)
        title = html.escape(title_raw, quote=True)
        content = html.escape(content_raw, quote=True)
        title_list_html += (
            '<section style="display:flex;margin-bottom:16px;align-items:baseline;">'
            '<section style="flex-shrink:0;width:36px;text-align:left;">'
            f'<span style="font-size:24px;font-weight:900;color:rgb(92,11,214);font-family:Arial,sans-serif;font-style:italic;line-height:1;">{index:02d}</span>'
            "</section>"
            '<section style="flex-grow:1;">'
            f'<span style="font-size:16px;font-weight:700;color:#1f1f1f;letter-spacing:.5px;line-height:1.6;text-align:justify;font-family:-apple-system,BlinkMacSystemFont,\'Helvetica Neue\',\'PingFang SC\',\'Microsoft YaHei\',sans-serif;">{title}</span>'
            "</section>"
            "</section>"
        )
        # 构建配图HTML（如果有配图）
        image_html = ""
        if image_url:
            image_html = (
                f'<section style="margin:16px 0;text-align:center;">'
                f'<img class="rich_pages wxw-img" style="max-width:100%;border-radius:8px;" '
                f'src="{html.escape(image_url, quote=True)}">'
                f'</section>'
            )
        content_html += (
            '<section data-mpa-template="t" mpa-from-tpl="t" style="margin:40px 0 32px;outline:0;color:rgb(22,1,110);font-family:\'PingFang SC\';letter-spacing:.5px;font-size:16px;">'
            '<section powered-by="xiumi.us" mpa-from-tpl="t" style="margin:10px 0 20px;outline:0;text-align:left;display:flex;flex-flow:row nowrap;">'
            '<section mpa-from-tpl="t" style="padding-left:5px;outline:0;display:inline-block;width:auto;vertical-align:top;border-left:5px solid rgb(45,5,147);flex:100 100 0%;align-self:flex-start;">'
            '<section powered-by="xiumi.us" mpa-from-tpl="t" style="margin:-2px 0 -3px;outline:0;">'
            '<section mpa-from-tpl="t" style="outline:0;font-size:17px;line-height:1.3;text-align:justify;">'
            f'<p style="outline:0;"><strong mpa-from-tpl="t" mpa-is-content="t" style="text-align:left;outline:0;"><span leaf="">{title}</span></strong></p>'
            "</section></section></section></section>"
            f'{image_html}'
            f'<section style="color:#333;line-height:1.8;font-size:15px;text-align:justify;">{content}</section>'
            "</section>"
        )

    first_title = title_list[0] if title_list else ""
    html_content = (
        '<section data-mpa-powered-by="yiban.io" '
        'style="outline:0;letter-spacing:.544px;font-family:system-ui,-apple-system,BlinkMacSystemFont,\'Helvetica Neue\',\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei UI\',\'Microsoft YaHei\',Arial,sans-serif;background-color:#fff;visibility:visible;">'
        '<img data-src="https://mmbiz.qpic.cn/mmbiz_gif/xm1dT1jCe8lIO3P2oFVtd1x040PKGCRPN033gUTrHQQz0Licdqug5X1QgUPQBRCicoTqdYMrpgk7etibXLkK9rwcg/0?wx_fmt=gif&amp;from=appmsg" '
        'alt="头图" class="rich_pages wxw-img __bg_gif" data-ratio="0.172" data-type="gif" data-w="1000" '
        'style="outline:0;text-align:center;display:inline;width:100%!important;visibility:visible!important;" '
        'src="https://mmbiz.qpic.cn/mmbiz_gif/xm1dT1jCe8lIO3P2oFVtd1x040PKGCRPN033gUTrHQQz0Licdqug5X1QgUPQBRCicoTqdYMrpgk7etibXLkK9rwcg/0?wx_fmt=gif&amp;from=appmsg">'
        "</section>"
        '<section data-mpa-template="t" mpa-from-tpl="t" style="margin-bottom:0;padding:0 4px;outline:0;letter-spacing:.544px;background-color:#fff;">'
        '<section mpa-from-tpl="t" style="text-align:center;">'
        '<section mpa-from-tpl="t" style="margin:10px auto;">'
        '<section style="margin:auto;width:120px;">'
        '<img data-src="https://mmbiz.qpic.cn/mmbiz_png/xm1dT1jCe8lWwznuFM0USlcQ4HAb5iapyX3ddvtwOXao2JMFvnD1dRzEomnCQ2qowiaev5vlwMFgzDQr4zlwLWpw/640?wx_fmt=png&amp;from=appmsg" '
        'alt="标题装饰" class="rich_pages wxw-img" style="margin:auto;display:block;width:120px!important;" '
        'src="https://mmbiz.qpic.cn/mmbiz_png/xm1dT1jCe8lWwznuFM0USlcQ4HAb5iapyX3ddvtwOXao2JMFvnD1dRzEomnCQ2qowiaev5vlwMFgzDQr4zlwLWpw/640?wx_fmt=png&amp;from=appmsg">'
        "</section>"
        '<section style="display:inline-block;">'
        '<section style="margin-top:-32px;padding:0 40px;outline:0;font-size:24px;letter-spacing:1.5px;"><strong><span leaf="">新闻提要</span></strong></section>'
        '<section style="margin-top:-2px;width:182px;height:2px;background-color:rgb(58,19,228);overflow:hidden;"><span leaf=""><br></span></section>'
        '<section style="margin-top:3px;width:182px;height:1px;background-color:rgb(92,11,214);overflow:hidden;"><span leaf=""><br></span></section>'
        '<p style="margin-bottom:24px;outline:0;font-size:12px;letter-spacing:1.5px;color:rgb(111,19,209);transform:scale(.9);"><span leaf="">NEWS BRIEF</span></p>'
        "</section></section></section></section>"
        f'<section id="yaodian" style="margin:20px 10px;">{title_list_html}</section>'
        '<section style="margin:10px auto;text-align:center;">'
        '<section style="display:inline-block;">'
        '<section style="margin-top:-4px;padding:0 40px;outline:0;font-size:24px;letter-spacing:1.5px;"><strong><span leaf="">新闻速览</span></strong></section>'
        '<section style="margin-top:-2px;width:182px;height:2px;background-color:rgb(58,19,228);overflow:hidden;"><span leaf=""><br></span></section>'
        '<section style="margin-top:3px;width:182px;height:1px;background-color:rgb(111,19,209);overflow:hidden;"><span leaf=""><br></span></section>'
        '<section style="outline:0;font-size:12px;letter-spacing:1.5px;color:rgb(92,11,214);transform:scale(.9);"><span leaf="">NEWS GLANCE</span></section>'
        "</section></section>"
        f'<section id="xiangqing"><section id="content1">{content_html}</section></section>'
        '<section style="text-align:center;">'
        '<img data-src="https://mmbiz.qpic.cn/mmbiz_png/xm1dT1jCe8lWwznuFM0USlcQ4HAb5iapyGyBVUyRZOyLbF3SaPphbdlF7K9R7YLicicIjdeAPueYcKHJDZEiceeUFQ/640?wx_fmt=png" '
        'class="rich_pages wxw-img" data-ratio="0.4255555555555556" data-type="png" data-w="900" '
        'src="https://mmbiz.qpic.cn/mmbiz_png/xm1dT1jCe8lWwznuFM0USlcQ4HAb5iapyGyBVUyRZOyLbF3SaPphbdlF7K9R7YLicicIjdeAPueYcKHJDZEiceeUFQ/640?wx_fmt=png">'
        "</section>"
    )
    html_content = re.sub(r"<!--.*?-->", "", html_content, flags=re.S)
    html_content = re.sub(r"\s+", " ", html_content)
    html_content = re.sub(r">\s+<", "><", html_content).strip()
    return html_content, first_title


def build_weixin_payload(items: List[Dict]) -> Dict:
    from datetime import datetime
    from daily_tech.config import SHANGHAI_TZ
    html_content, first_title = build_weixin_html_template(items)
    
    # 提取所有配图地址（GitHub地址）
    covers = []
    for item in items:
        image_url = item.get("配图")
        if image_url:
            covers.append(image_url)
    
    return {
        "wexinhtml": html_content,
        "key1": first_title,
        "count": len(items),
        "covers": covers,
        "generated_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
    }
