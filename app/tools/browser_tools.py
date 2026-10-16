
from langchain_core.tools import tool
from playwright.sync_api import sync_playwright


@tool
def fetch_webpage(url: str) -> str:
    """
    打开一个网页，获取其完整的文本内容。
    适用于需要阅读网页文章、新闻、文档等场景。
    输入是一个完整的URL（如 https://example.com）。
    """
    with sync_playwright() as p:
        # 启动无头浏览器（后台运行，不显示窗口）
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
            # 获取页面的纯文本内容
            text = page.inner_text("body")
            # 简单清洗：限制长度，去除过多空白
            text = "\n".join([line.strip() for line in text.splitlines() if line.strip()])
            if len(text) > 3000:
                text = text[:3000] + "\n... (内容过长，已截断)"
            return text
        except Exception as e:
            return f"获取网页失败: {str(e)}"
        finally:
            browser.close()


@tool
def fetch_webpage_html(url: str) -> str:
    """
    获取网页的原始HTML内容。
    适用于需要分析网页结构、提取特定标签信息的场景。
    输入是一个完整的URL。
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
            html = page.content()
            if len(html) > 5000:
                html = html[:5000] + "\n... (内容过长，已截断)"
            return html
        except Exception as e:
            return f"获取网页HTML失败: {str(e)}"
        finally:
            browser.close()

import os
import time

@tool
def screenshot_webpage(url: str) -> str:
    """
    打开一个网页并将其保存为截图（PNG格式）。
    适用于需要查看网页视觉呈现、捕获动态图表或保存页面状态的场景。
    返回截图文件的本地路径。
    """
    # 确保截图保存目录存在
    screenshot_dir = os.path.join(os.path.dirname(__file__), "..", "..", "screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)

    # 生成带时间戳的文件名
    timestamp = int(time.time())
    safe_filename = f"screenshot_{timestamp}.png"
    filepath = os.path.join(screenshot_dir, safe_filename)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
            # 截取整个页面
            page.screenshot(path=filepath, full_page=True)
            return f"网页截图已保存到: {filepath}"
        except Exception as e:
            return f"网页截图失败: {str(e)}"
        finally:
            browser.close()
            