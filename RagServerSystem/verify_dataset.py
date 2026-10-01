"""
智能售后客服系统客服 · 数据入库 + 离检索验证工具
跑通离线流程：数据加载 → 分块 → 向量化入库 → 检索命中验证（仅依赖本地 embedding，不需 Redis/在线 LLM）

用法：
  python verify_dataset.py          # 用现有向量库做检索验证
  python verify_dataset.py fresh    # 强制重建数据并入库后再检索验证
"""
import asyncio
import sys
from pathlib import Path
BASE = Path(__file__).parent
sys.path.append(str(BASE))
import config.settings as s


async def ingest():
    from ingestion.service import DataLayer
    s.CHUNKING_STRATEGY = "recursive"   # 快且稳的分块
    print(">>> 开始数据入库（会清空并重建向量库）")
    await DataLayer.data_loader(BASE / "data")
    print(">>> 数据入库完成")


async def retrieve_test():
    from infrastructure.vector_store.async_chroma_vector import ChromaVector
    vs = ChromaVector()
    cases = [
        ("订单SO20241120005是什么商品", ["SO20241120005"]),
        ("iPhone 15多少钱", ["4999", "iPhone 15"]),
        ("小米 15价格和参数", ["4499", "小米 15"]),
        ("华为 Mate 60 Pro参数", ["6499", "Mate 60 Pro"]),
        ("智能售后客服热线", ["400-800-1234", "热线"]),
        ("七天无理由退货政策", ["无理由退货", "七天"]),
    ]
    print("=" * 74)
    print("检索验证")
    print("=" * 74)
    ok = True
    for q, expects in cases:
        docs = vs.similarity_search(q, k=3)
        text = " ".join(d.page_content for d in docs)
        miss = [m for m in expects if m.lower() not in text.lower()]
        if miss:
            ok = False
        print(f"\nQ: {q}\n  top1: {docs[0].page_content[:50] if docs else '(空)'}...")
        print(f"  命中={[e for e in expects if e.lower() in text.lower()]}  "
              f"{'✅' if not miss else '❌ 未命中' + str(miss)}")
    print()
    print("✅ 检索验证通过：所有问题命中知识库" if ok else "❌ 部分问题未命中，请检查数据/分块")
    return ok


async def main():
    if len(sys.argv) > 1 and sys.argv[1] == "fresh":
        await ingest()
    else:
        print(">>> 跳过入库（用现有向量库，传入 fresh 强制重建）")
    await retrieve_test()


if __name__ == "__main__":
    asyncio.run(main())