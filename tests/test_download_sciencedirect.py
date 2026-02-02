"""Search and download papers on a given topic from ScienceDirect.

Also tests the browser modules:
- CaptchaHandler: Global CAPTCHA handling with lock mechanism
- SessionManager: Multi-session management with timeout cleanup
- DOMService: DOM extraction service
"""

import asyncio
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vibescholar.browser import session_manager, SessionManager
from vibescholar.browser.captcha_handler import CaptchaHandler
from vibescholar.browser.dom_service import DOMService, DOMElement
from vibescholar.sites import ScienceDirectAdapter
from vibescholar.config import settings


# 搜索主题
SEARCH_TOPIC = "large language model reasoning"
MAX_PAPERS_PER_SOURCE = 3  # 每个来源下载3篇


async def test_sciencedirect(session):
    """Test ScienceDirect search and download."""
    print("\n" + "=" * 70)
    print("测试 ScienceDirect")
    print("=" * 70)

    adapter = ScienceDirectAdapter(session)

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
        print(f"\n[ScienceDirect {i}/{MAX_PAPERS_PER_SOURCE}] {title}")

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
# 新模块测试
# ============================================================================

async def test_captcha_handler(session):
    """Test CaptchaHandler functionality."""
    print("\n" + "=" * 70)
    print("测试 CaptchaHandler")
    print("=" * 70)

    handler = CaptchaHandler(session)

    # Test CAPTCHA detection
    print("\n1. 测试 CAPTCHA 检测...")
    test_cases = [
        ("Are you a robot?", True),
        ("请输入验证码", True),
        ("unusual traffic detected", True),
        ("Welcome to ScienceDirect", False),
        ("Search results", False),
    ]

    passed = 0
    for content, expected in test_cases:
        result = handler._is_captcha_page(content)
        status = "✓" if result == expected else "✗"
        print(f"   {status} '{content[:30]}...' -> {result} (expected: {expected})")
        if result == expected:
            passed += 1

    print(f"\n   通过: {passed}/{len(test_cases)}")

    # Test lock mechanism
    print("\n2. 测试锁机制...")
    CaptchaHandler._handling = False
    CaptchaHandler._wait_event = None

    lock_result = await handler._try_acquire_lock()
    print(f"   获取锁: {lock_result}")
    assert lock_result == "acquired", "Lock acquisition failed"

    handler._release_lock()
    print("   释放锁: 成功")

    print("\nCaptchaHandler 测试完成!")
    return True


async def test_session_manager():
    """Test SessionManager functionality."""
    print("\n" + "=" * 70)
    print("测试 SessionManager")
    print("=" * 70)

    manager = SessionManager(max_sessions=3, session_timeout=60)

    print(f"\n配置: max_sessions={manager.max_sessions}, timeout={manager.session_timeout}s")

    # Test session creation
    print("\n1. 测试会话创建...")
    session1 = await manager.get_session("sciencedirect", headless=True)
    print(f"   创建会话 1: {session1.session_id}")

    # Test session reuse
    print("\n2. 测试会话复用...")
    session1_again = await manager.get_session("sciencedirect", headless=True)
    is_same = session1 is session1_again
    print(f"   复用会话: {'✓ 成功' if is_same else '✗ 失败'}")

    # Test has_session
    print("\n3. 测试 has_session...")
    has_sd = manager.has_session("sciencedirect")
    has_nature = manager.has_session("nature")
    print(f"   has_session('sciencedirect'): {has_sd}")
    print(f"   has_session('nature'): {has_nature}")

    # Clean up
    print("\n4. 清理会话...")
    await manager.close_all()
    print("   所有会话已关闭")

    print("\nSessionManager 测试完成!")
    return True


async def test_dom_service(session):
    """Test DOMService functionality."""
    print("\n" + "=" * 70)
    print("测试 DOMService")
    print("=" * 70)

    # Navigate to a test page
    print("\n1. 导航到 ScienceDirect 首页...")
    await session.goto("https://www.sciencedirect.com")
    await asyncio.sleep(2)

    dom = DOMService(session.page)

    # Test extract_links
    print("\n2. 测试 extract_links...")
    links = await dom.extract_links(selector="a[href]", filter_pattern=None)
    print(f"   找到 {len(links)} 个链接")
    if links:
        print(f"   示例: {links[0].get('href', '')[:50]}...")

    # Test extract_text_content
    print("\n3. 测试 extract_text_content...")
    text = await dom.extract_text_content("h1, h2, h3", join_separator=" | ")
    print(f"   提取的标题: {text[:100]}..." if text else "   未找到标题")

    # Test wait_for_element
    print("\n4. 测试 wait_for_element...")
    found = await dom.wait_for_element("body", timeout=5000)
    print(f"   等待 body 元素: {'✓ 找到' if found else '✗ 未找到'}")

    print("\nDOMService 测试完成!")
    return True


async def test_adapter_integration(session):
    """Test adapter integration with new modules."""
    print("\n" + "=" * 70)
    print("测试适配器集成")
    print("=" * 70)

    adapter = ScienceDirectAdapter(session)

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
    is_captcha = adapter.is_captcha_page("Are you a robot?")
    print(f"   is_captcha_page('Are you a robot?'): {is_captcha}")
    assert is_captcha is True, "Should detect CAPTCHA"
    print("   ✓ 成功")

    print("\n适配器集成测试完成!")
    return True


async def run_module_tests():
    """Run all module tests."""
    print("\n" + "=" * 70)
    print("新模块测试")
    print("=" * 70)

    # Test SessionManager (doesn't need existing session)
    await test_session_manager()

    # Create session for other tests
    print("\n创建浏览器会话...")
    session = await session_manager.get_session(headless=True)

    try:
        await test_captcha_handler(session)
        await test_dom_service(session)
        await test_adapter_integration(session)
    finally:
        print("\n关闭测试会话...")

    print("\n" + "=" * 70)
    print("所有模块测试完成!")
    print("=" * 70)


async def main():
    """Main test function."""
    import argparse

    parser = argparse.ArgumentParser(description="ScienceDirect 测试")
    parser.add_argument(
        "--modules",
        action="store_true",
        help="运行新模块测试 (CaptchaHandler, SessionManager, DOMService)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="运行所有测试 (模块测试 + 下载测试)"
    )
    args = parser.parse_args()

    print("=" * 70)
    print("PDF 下载功能测试 (ScienceDirect)")
    print(f"搜索主题: {SEARCH_TOPIC}")
    print(f"下载数量: {MAX_PAPERS_PER_SOURCE} 篇")
    print("=" * 70)

    # 确保目录存在
    settings.ensure_dirs()
    print(f"\n下载目录: {settings.papers_dir}")

    # Run module tests if requested
    if args.modules or args.all:
        await run_module_tests()
        if not args.all:
            return

    # 创建浏览器会话（可见模式，方便用户观察）
    print("\n创建浏览器会话...")
    session = await session_manager.get_session(headless=False)

    downloaded, failed = await test_sciencedirect(session)

    # 总结
    print("\n" + "=" * 70)
    print("测试完成!")
    print(f"  下载成功: {downloaded}")
    print(f"  下载失败: {failed}")
    print(f"  下载目录: {settings.papers_dir}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
