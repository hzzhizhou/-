"""容器入口：首次启动（向量库为空且语料目录非空）时先建索引，再启动 API 服务。

用 python 而不是 shell 脚本：Windows 上 git 可能把 .sh 换行转成 CRLF，
容器内 sh 解析会直接失败（经典 "no such file or directory"），python 入口无此问题。
"""
import asyncio
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


def _has_files(p: Path) -> bool:
    return p.exists() and any(p.iterdir())


def main() -> None:
    kb_dir = BASE / "vector_db-sql"
    corpus_dir = BASE / "data"
    if not _has_files(kb_dir) and _has_files(corpus_dir):
        print("[entrypoint] 向量库为空且检测到语料，开始构建索引（DashScope embedding，约 1-2 分钟）...",
              flush=True)
        from ingestion.service import DataLayer
        t0 = time.time()
        asyncio.run(DataLayer.ingest(corpus_dir, mode="full"))
        print(f"[entrypoint] 索引构建完成，耗时 {time.time() - t0:.1f}s", flush=True)
    else:
        print("[entrypoint] 向量库已存在，跳过入库", flush=True)

    import main as main_module
    main_module.main()


if __name__ == "__main__":
    main()
