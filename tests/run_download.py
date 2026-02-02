"""Simple script to search and download papers from ScienceDirect.

Usage:
    python tests/run_download.py
    python tests/run_download.py --topic "your search topic"
    python tests/run_download.py --max 5
"""

import asyncio
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vibescholar.browser import session_manager
from vibescholar.sites import ScienceDirectAdapter
from vibescholar.config import settings


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="ScienceDirect PDF 下载")
    parser.add_argument("--topic", default="large language model reasoning", help="搜索主题")
    parser.add_argument("--max", type=int, default=3, help="下载数量")
    args = parser.parse_args()

    print("=" * 70)
    print("ScienceDirect PDF 下载")
    print(f"搜索主题: {args.topic}")
    print(f"下载数量: {args.max}")
    print("=" * 70)

    # 确保目录存在
    settings.ensure_dirs()
    print(f"\n下载目录: {settings.papers_dir}")

    # 获取浏览器会话 - 使用全局 session_manager
    print("\n启动浏览器...")
    session = await session_manager.get_session(
        site="sciencedirect",
        headless=False,  # 可见模式，方便处理验证码
    )
    print("浏览器已启动")

    try:
        # 创建适配器
        adapter = ScienceDirectAdapter(session)

        # 搜索论文
        print(f"\n搜索: {args.topic}")
        search_result = await adapter.search(args.topic, max_results=args.max)
        print(f"找到 {len(search_result.papers)} 篇论文")

        if not search_result.papers:
            print("未找到论文，退出")
            return

        # 显示搜索结果
        print("\n搜索结果:")
        for i, paper in enumerate(search_result.papers, 1):
            authors = ", ".join(paper.author_names[:2]) if paper.author_names else "Unknown"
            if paper.author_names and len(paper.author_names) > 2:
                authors += " et al."
            title = paper.title[:60] + "..." if len(paper.title) > 60 else paper.title
            print(f"\n  {i}. {title}")
            print(f"     作者: {authors}")
            print(f"     年份: {paper.year}")

        # 下载 PDF
        print("\n" + "-" * 70)
        print("开始下载...")
        print("-" * 70)

        downloaded = 0
        failed = 0

        for i, paper in enumerate(search_result.papers, 1):
            title = paper.title[:50] + "..." if len(paper.title) > 50 else paper.title
            print(f"\n[{i}/{len(search_result.papers)}] {title}")

            filename = paper.suggested_filename()
            save_path = str(settings.papers_dir / filename)

            if Path(save_path).exists():
                print("  已存在，跳过")
                downloaded += 1
                continue

            try:
                result = await adapter.download_pdf(paper, save_path)
                if result.success:
                    size_kb = result.file_size / 1024 if result.file_size else 0
                    print(f"  下载成功! {size_kb:.1f} KB")
                    print(f"  保存至: {filename}")
                    downloaded += 1
                else:
                    print(f"  下载失败: {result.error}")
                    failed += 1
            except Exception as e:
                print(f"  异常: {e}")
                failed += 1

        # 总结
        print("\n" + "=" * 70)
        print("下载完成!")
        print(f"  成功: {downloaded}")
        print(f"  失败: {failed}")
        print(f"  目录: {settings.papers_dir}")
        print("=" * 70)

    finally:
        print("\n关闭浏览器...")
        await session_manager.close_all()
        print("完成")


if __name__ == "__main__":
    asyncio.run(main())
