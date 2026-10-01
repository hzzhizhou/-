"""
MySQL状态管理器
用于管理文档状态和分块关联
通过文档状态管理器，可以判断文件是否需要更新，获取旧的分块ID，更新文档状态和分块关联，删除文档记录和分块关联，获取所有已记录的文档路径
"""
import hashlib
import aiomysql
from pathlib import Path
from typing import List, Set
from logs.log_config import data_layer_log as log
from config.settings import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
from utils.doc_identity import make_doc_id, normalize_doc_path

class MySQLStateManager:
    """MySQL状态管理器"""
    def __init__(self):
        self.pool = None

    async def initialize(self):
        """创建连接池并初始化表结构"""
        self.pool = await aiomysql.create_pool(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            db=MYSQL_DATABASE,
            minsize=1,
            maxsize=10,
            autocommit=False,
            charset='utf8mb4'
        )
        await self._create_tables()
        log.info("MySQL 状态管理器初始化完成")

    async def _create_tables(self):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 创建 documents 表
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id VARCHAR(255) PRIMARY KEY,
                        file_path VARCHAR(512) NOT NULL UNIQUE,
                        file_name VARCHAR(255),
                        mtime DOUBLE,
                        content_hash VARCHAR(64),
                        last_indexed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # 创建 doc_chunks 表
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS doc_chunks (
                        chunk_id VARCHAR(255) PRIMARY KEY,
                        doc_id VARCHAR(255) NOT NULL,
                        chunk_index INT,
                        FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
                    )
                """)
                
                # 检查并创建索引 idx_doc_path
                await cur.execute("""
                    SELECT COUNT(1) FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                    AND TABLE_NAME = 'documents' 
                    AND INDEX_NAME = 'idx_doc_path'
                """)
                if (await cur.fetchone())[0] == 0:
                    await cur.execute("CREATE INDEX idx_doc_path ON documents(file_path)")
                
                # 检查并创建索引 idx_doc_id
                await cur.execute("""
                    SELECT COUNT(1) FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                    AND TABLE_NAME = 'doc_chunks' 
                    AND INDEX_NAME = 'idx_doc_id'
                """)
                if (await cur.fetchone())[0] == 0:
                    await cur.execute("CREATE INDEX idx_doc_id ON doc_chunks(doc_id)")
                
            await conn.commit()
    @staticmethod
    def get_file_hash(file_path: Path) -> str:
        """计算文件 SHA256 哈希"""
        hasher = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
        return hasher.hexdigest()

    async def need_update(self, file_path: Path) -> bool:
        """判断文件是否需要更新（新增或修改）"""
        current_mtime = file_path.stat().st_mtime
        current_hash = self.get_file_hash(file_path)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 只读事务：先结束该连接可能残留的旧一致快照，保证读到最新已提交数据
                await conn.rollback()
                # 查询用归一化路径（入库时 file_path 已存正斜杠绝对路径）
                await cur.execute(
                    "SELECT mtime, content_hash FROM documents WHERE file_path = %s",
                    (normalize_doc_path(file_path),)
                )
                row = await cur.fetchone()
                if not row:
                    return True
                old_mtime, old_hash = row
                return old_mtime != current_mtime or old_hash != current_hash

    async def get_old_chunk_ids(self, file_path: Path) -> List[str]:
        """获取某个文件的所有旧分块 ID"""
        doc_id = make_doc_id(file_path)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 只读事务：先结束该连接可能残留的旧一致快照
                await conn.rollback()
                await cur.execute(
                    "SELECT chunk_id FROM doc_chunks WHERE doc_id = %s",
                    (doc_id,)
                )
                rows = await cur.fetchall()
                return [row[0] for row in rows]

    async def update_state(self, file_path: Path, chunk_ids: List[str]):
        """更新文档状态和分块关联（事务内完成）"""
        doc_id = make_doc_id(file_path)
        norm_path = normalize_doc_path(file_path)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. 插入或更新文档记录（id 用基于路径哈希的 doc_id，路径仅作属性列）
                await cur.execute(
                    """INSERT INTO documents (id, file_path, file_name, mtime, content_hash)
                       VALUES (%s, %s, %s, %s, %s)
                       AS new
                       ON DUPLICATE KEY UPDATE
                       file_name = new.file_name,
                       mtime = new.mtime,
                       content_hash = new.content_hash,
                       last_indexed = CURRENT_TIMESTAMP""",
                    (
                        doc_id,
                        norm_path,
                        Path(file_path).name,
                        file_path.stat().st_mtime,
                        self.get_file_hash(file_path)
                    )
                )
                # 2. 删除旧的 chunk 关联
                await cur.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (doc_id,))
                # 3. 插入新的 chunk 关联
                if chunk_ids:
                    args = [(chunk_id, doc_id, idx) for idx, chunk_id in enumerate(chunk_ids)]
                    await cur.executemany(
                        "INSERT INTO doc_chunks (chunk_id, doc_id, chunk_index) VALUES (%s, %s, %s)",
                        args
                    )
                await conn.commit()
                log.info(f"MySQL 状态已更新: {Path(file_path).name}，共 {len(chunk_ids)} 个块")

    async def remove_document(self, file_path: Path):
        """删除文档记录（级联删除 doc_chunks）"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM documents WHERE file_path = %s",
                                  (normalize_doc_path(file_path),))
                await conn.commit()
                log.info(f"已从 MySQL 删除文档: {Path(file_path).name}")

    async def get_all_file_paths(self) -> Set[str]:
        """获取所有已记录的文档路径"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 只读事务：先结束该连接可能残留的旧一致快照
                await conn.rollback()
                await cur.execute("SELECT file_path FROM documents")
                rows = await cur.fetchall()
                return {row[0] for row in rows}

    async def list_documents(self) -> List[dict]:
        """列出已入库文档及其分块数（供管理端知识库页）"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 只读事务：先结束该连接可能残留的旧一致快照
                await conn.rollback()
                await cur.execute(
                    """SELECT d.id, d.file_path, d.file_name, d.last_indexed,
                              COUNT(c.chunk_id) AS chunk_count
                       FROM documents d
                       LEFT JOIN doc_chunks c ON c.doc_id = d.id
                       GROUP BY d.id, d.file_path, d.file_name, d.last_indexed
                       ORDER BY d.last_indexed DESC"""
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in rows]

    async def get_doc_chunks(self, doc_id: str) -> List[dict]:
        """返回某文档的分块列表（chunk_id + 序号）"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 只读事务：先结束该连接可能残留的旧一致快照
                await conn.rollback()
                await cur.execute(
                    "SELECT chunk_id, chunk_index FROM doc_chunks "
                    "WHERE doc_id=%s ORDER BY chunk_index",
                    (doc_id,)
                )
                rows = await cur.fetchall()
                return [{"chunk_id": r[0], "chunk_index": r[1]} for r in rows]

    async def reset(self):
        """清空全部状态（doc_chunks 依赖 documents 的级联外键一并清空）。
        用于"全量重建"，保证 MySQL 状态与向量库同步归零后再重建。"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM documents")
                await conn.commit()
                log.info("MySQL 状态已清空")

    async def close(self):
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()