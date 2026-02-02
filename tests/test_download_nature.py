"""Search and download papers on a given topic from Nature.

Also tests the new browser modules integration with NatureAdapter:
- CaptchaHandler: Global CAPTCHA handling with lock mechanism
- DOMService: DOM extraction service
"""

import asyncio
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vibescholar.browser import session_manager
from vibescholar.browser.captcha_handler import CaptchaHandler
from vibescholar.browser.dom_service import DOMService
from vibescholar.sites import NatureAdapter
from vibescholar.config import settings


# 搜索主题
SEARCH_TOPIC = "large language model reasoning"
MAX_PAPERS_PER_SOURCE = 3  # 每个来源下载3篇


async def test_nature(session):
    """Test Nature search and download."""
    print("\n" + "=" * 70)
    print("测试 Nature")
    print("=" * 70)

    adapter = NatureAdapter(session)

    # 搜索
    print(f"\n搜索: {SEARCH_TOPIC}")
    try:
        search_result = await adapter.search(SEARCH_TOPIC, max_results=MAX_PAPERS_PER_SOURCE)
        print(f"找到 {len(search_result.papers)} 篇论文")
    except Exception as e:
        print(f"搜索失败: {e}")
        return 0, 0

    # 显示搜索结果
    for i, paper in enumerate(search_result.papers, 1):
        authors = ", ".join(paper.author_names[:2]) if paper.author_names else "Unknown"
        if paper.author_names and len(paper.author_names) > 2:
            authors += " et al."
        title = paper.title[:55] + "..." if len(paper.title) > 55 else paper.title
        print(f"\n  {i}. {title}")
        print(f"     作者: {authors}")
        print(f"     年份: {paper.year}")

    # 下载
    print("\n开始下载...")
    downloaded = 0
    failed = 0

    for i, paper in enumerate(search_result.papers[:MAX_PAPERS_PER_SOURCE], 1):
        title = paper.title[:45] + "..." if len(paper.title) > 45 else paper.title
        print(f"\n[Nature {i}/{MAX_PAPERS_PER_SOURCE}] {title}")

        filename = paper.suggested_filename()
        save_path = str(settings.papers_dir / filename)

        if Path(save_path).exists():
            print("  已存在，跳过")
            downloaded += 1
            continue

        try:
            result = await adapter.download_pdf(paper, save_path)
            if result.success:
                print(f"  成功! {result.file_size / 1024:.1f} KB")
                downloaded += 1
            else:
                print(f"  失败: {result.error}")
                failed += 1
        except Exception as e:
            print(f"  异常: {e}")
            failed += 1

    return downloaded, failed


# ============================================================================
# 新模块集成测试
# ============================================================================

async def test_adapter_modules(session):
    """Test NatureAdapter integration with new modules."""
    print("\n" + "=" * 70)
    print("测试 NatureAdapter 模块集成")
    print("=" * 70)

    adapter = NatureAdapter(session)

    # Test captcha_handler property
    print("\n1. 测试 captcha_handler 属性...")
    handler = adapter.captcha_handler
    print(f"   CaptchaHandler: {type(handler).__name__}")
    assert isinstance(handler, CaptchaHandler), "captcha_handler should be CaptchaHandler"
    print("   ✓ 成功")

    # Test dom_service property
    print("\n2. 测试 dom_service 属性...")
    dom = adapter.dom_service
    print(f"   DOMService: {type(dom).__name__}")
    assert isinstance(dom, DOMService), "dom_service should be DOMService"
    print("   ✓ 成功")

    # Test is_captcha_page method
    print("\n3. 测试 is_captcha_page 方法...")
    test_cases = [
        ("Are you a robot?", True),
        ("验证码", True),
        ("Nature Research", False),
    ]
    for content, expected in test_cases:
        result = adapter.is_captcha_page(content)
        status = "✓" if result == expected else "✗"
        print(f"   {status} '{content}' -> {result}")

    # Test DOMService on Nature homepage
    print("\n4. 测试 DOMService 在 Nature 首页...")
    await session.goto("https://www.nature.com")
    await asyncio.sleep(2)

    # Refresh dom_service for new page
    dom = adapter.dom_service
    links = await dom.extract_links(selector="a[href]")
    print(f"   找到 {len(links)} 个链接")

    text = await dom.extract_text_content("h1, h2", join_separator=" | ")
    print(f"   标题: {text[:80]}..." if text else "   未找到标题")

    print("\nNatureAdapter 模块集成测试完成!")
    return True


async def main():
    """Main test function."""
    import argparse

    parser = argparse.ArgumentParser(description="Nature 测试")
    parser.add_argument(
        "--modules",
        action="store_true",
        help="运行新模块集成测试"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="运行所有测试 (模块测试 + 下载测试)"
    )
    args = parser.parse_args()

    print("=" * 70)
    print("PDF 下载功能测试 (Nature)")
    print(f"搜索主题: {SEARCH_TOPIC}")
    print(f"下载数量: {MAX_PAPERS_PER_SOURCE} 篇")
    print("=" * 70)

    # 确保目录存在
    settings.ensure_dirs()
    print(f"\n下载目录: {settings.papers_dir}")

    # 创建浏览器会话（可见模式，方便用户观察）
    print("\n创建浏览器会话...")
    session = await session_manager.get_session(headless=False if not args.modules else True)

    # Run module tests if requested
    if args.modules or args.all:
        await test_adapter_modules(session)
        if not args.all:
            return

    downloaded, failed = await test_nature(session)

    # 总结
    print("\n" + "=" * 70)
    print("测试完成!")
    print(f"  下载成功: {downloaded}")
    print(f"  下载失败: {failed}")
    print(f"  下载目录: {settings.papers_dir}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
