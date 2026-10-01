"""客服业务数据持久化存储（订单 + 工单 + 人工会话消息 + DST 会话状态 + 用户 + 对话历史）

存储：MySQL（pymysql 同步驱动，短连接 + 显式事务），数据库 rag_db。
表结构：
  - orders             : 订单主表（首次启动导入种子数据）
  - tickets            : 工单表（Agent 转人工时写入）
  - ticket_messages    : 工单消息（某张工单一旦有消息，它就代表一场人工会话）
  - conversation_state : DST 会话状态（意图/槽位/阶段/护栏计数，多轮跨请求持久化）
  - conversations      : 会话表（对话历史的归属/标题/状态/时间，历史列表的数据源）
  - messages           : 会话消息表（逐条追加，Redis 只是它的热缓存）
  - users              : 用户账号（含登录令牌）

为什么坚持同步驱动而不是 aiomysql：
  调用方全是同步上下文 —— FastAPI 线程池（asyncio.to_thread）、Agent 工具函数、
  后台线程（流式回答结束后落历史）。保持同步签名，这 30+ 个函数的调用点一行都不用改，
  也让「查单/建单」这类工具逻辑保持直白；事件循环内请用 asyncio.to_thread 包裹。

约定了两条实现纪律：
  - 一律用 %s 占位符（不做 SQL 拼接），避免注入；
  - 时间列统一 DATETIME，读出来由 _row() 转回 'YYYY-MM-DD HH:MM:SS' 字符串、
    金额 DECIMAL 转 float，保证对外返回值与迁移前完全一致。
"""
import json
from contextlib import contextmanager
from datetime import date, datetime, time as _time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pymysql
from pymysql.cursors import DictCursor

from config.settings import (
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
)
from logs.log_config import log

# 建表语句（MySQL 8 方言）。索引写在建表里：MySQL 不支持 CREATE INDEX IF NOT EXISTS。
_DDL = [
    """CREATE TABLE IF NOT EXISTS orders (
        id         BIGINT AUTO_INCREMENT PRIMARY KEY,
        order_id   VARCHAR(64)  NOT NULL UNIQUE,
        customer   VARCHAR(64)  NOT NULL DEFAULT '',
        product    VARCHAR(255) NOT NULL DEFAULT '',
        amount     DECIMAL(10,2) NOT NULL DEFAULT 0,
        status     VARCHAR(32)  NOT NULL DEFAULT '',
        logistics  VARCHAR(255) NULL,
        tracking   TEXT         NULL,
        created_at DATETIME     NULL,
        user_id    VARCHAR(64)  NOT NULL DEFAULT '',
        KEY idx_orders_user (user_id, created_at),
        KEY idx_orders_status (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS tickets (
        id               BIGINT AUTO_INCREMENT PRIMARY KEY,
        ticket_id        VARCHAR(64)  NOT NULL UNIQUE,
        user_id          VARCHAR(64)  NOT NULL DEFAULT '',
        order_id         VARCHAR(64)  NOT NULL DEFAULT '',
        description      TEXT         NOT NULL,
        priority         VARCHAR(16)  NOT NULL DEFAULT '',
        category         VARCHAR(32)  NOT NULL DEFAULT '',
        status           VARCHAR(16)  NOT NULL DEFAULT 'open',
        created_at       DATETIME     NULL,
        note             TEXT         NOT NULL,
        stage            VARCHAR(32)  NOT NULL DEFAULT '',
        stage_history    TEXT         NOT NULL,
        stage_updated_at VARCHAR(32)  NOT NULL DEFAULT '',
        KEY idx_tickets_user (user_id, created_at),
        KEY idx_tickets_status (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS ticket_messages (
        msg_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
        ticket_id   VARCHAR(64) NOT NULL,
        sender      VARCHAR(16) NOT NULL DEFAULT '',
        sender_id   VARCHAR(64) NOT NULL DEFAULT '',
        sender_name VARCHAR(64) NOT NULL DEFAULT '',
        content     TEXT        NOT NULL,
        created_at  DATETIME    NULL,
        KEY idx_ticket_messages_ticket (ticket_id, msg_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS conversation_state (
        session_id        VARCHAR(255) PRIMARY KEY,
        intent            VARCHAR(32)  NOT NULL DEFAULT '',
        slots             TEXT         NOT NULL,
        stage             VARCHAR(32)  NOT NULL DEFAULT 'COLLECTING',
        tool_call_count   INT          NOT NULL DEFAULT 0,
        escalation_reason TEXT         NOT NULL,
        answer            TEXT         NOT NULL,
        updated_at        DATETIME     NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS conversations (
        conversation_id VARCHAR(255) PRIMARY KEY,   -- 作用域会话 ID：u{user_id}_{session_id}
        user_id         VARCHAR(64)  NOT NULL DEFAULT '',  -- 归属账号（隔离唯一依据）
        title           VARCHAR(255) NOT NULL DEFAULT '',  -- 取首条用户消息
        status          VARCHAR(16)  NOT NULL DEFAULT 'open',  -- open=进行中 / closed=已结束
        created_at      DATETIME     NULL,
        updated_at      DATETIME     NULL,
        KEY idx_conversations_user (user_id, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS messages (
        msg_id          BIGINT AUTO_INCREMENT PRIMARY KEY,
        conversation_id VARCHAR(255) NOT NULL,
        seq             INT          NOT NULL,   -- 会话内序号（从 1 开始，稳定排序与分页）
        role            VARCHAR(16)  NOT NULL,   -- user / assistant
        content         TEXT         NOT NULL,
        created_at      DATETIME     NULL,
        UNIQUE KEY uk_messages_conv_seq (conversation_id, seq)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS users (
        user_id       VARCHAR(64)  PRIMARY KEY,
        username      VARCHAR(64)  NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL DEFAULT '',
        nickname      VARCHAR(64)  NOT NULL DEFAULT '',
        role          VARCHAR(16)  NOT NULL DEFAULT 'user',
        status        VARCHAR(16)  NOT NULL DEFAULT 'active',
        token         VARCHAR(128) NOT NULL DEFAULT '',
        created_at    DATETIME     NULL,
        last_login_at VARCHAR(32)  NOT NULL DEFAULT '',
        KEY idx_users_token (token)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


@contextmanager
def _tx():
    """一次业务操作 = 一次连接 + 一个事务：正常提交，异常回滚，最后关连接。

    FastAPI 线程池与后台线程都会调进来，pymysql 连接不是线程安全的，
    因此不做跨请求的连接复用（每次短连接），用数据库自身的事务和行锁保证一致性。
    """
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
        password=MYSQL_PASSWORD, database=MYSQL_DATABASE,
        charset="utf8mb4", cursorclass=DictCursor,
        autocommit=False, connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把 DB 行的类型转成与迁移前一致的 Python 类型，避免调用方感知存储变更：

    - DATETIME → 'YYYY-MM-DD HH:MM:SS' 字符串（前端展示与日志都按字符串用）
    - DECIMAL  → float（金额按数值用，避免出现 '599.00' 这类字符串差异）
    """
    if row is None:
        return None
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            value = value.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(value, (date, _time)):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        out[key] = value
    return out


def _rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_row(r) for r in rows]


def init_db():
    """建表 + 导入种子订单（幂等）。模块加载时执行一次。"""
    with _tx() as cur:
        for ddl in _DDL:
            cur.execute(ddl)

        cur.execute("SELECT COUNT(*) AS n FROM orders")
        if cur.fetchone()["n"] == 0:
            # 平台只经营智能手机一个品类（iPhone 15 / 小米 15 / 华为 Mate 60 Pro），
            # 种子订单与知识库商品清单保持一致，便于演示时按订单号直接核对机型。
            seeds = [
                ("SO20241120005", "陈*杰", "iPhone 15", 4999.00, "delivered",
                 "顺丰速运 SF2345678901",
                 "2024-11-21 已揽收 → 2024-11-22 北京转运中心 → 2024-11-23 已签收，签收人：本人",
                 "2024-11-20 10:15:00"),
                ("SO20241121006", "林*婷", "小米 15", 4499.00, "shipped",
                 "京东物流 JD8765432109",
                 "2024-11-22 已出库 → 2024-11-23 武汉转运中心 → 预计 2024-11-24 送达",
                 "2024-11-21 15:30:00"),
                ("SO20241122007", "周*豪", "华为 Mate 60 Pro", 6499.00, "paid",
                 None, "预计 24 小时内发货", "2024-11-22 09:05:00"),
            ]
            cur.executemany(
                "INSERT INTO orders (order_id, customer, product, amount, status, "
                "logistics, tracking, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                seeds,
            )
            log.info(f"种子订单导入完成：{len(seeds)} 条")


# ====================== 订单 ======================

def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    with _tx() as cur:
        cur.execute("SELECT * FROM orders WHERE order_id=%s", (order_id,))
        return _row(cur.fetchone())


def can_access_order(order: Dict[str, Any], owner_id: str = "") -> bool:
    """订单归属校验：订单有归属时，只有归属账号本人或管理员可以访问。

    此前订单查询没有任何权限边界 —— 拿别人的订单号一查就能看到客户、金额、
    物流与收货信息。归属账号由 bind_ownerless_orders 在启动时绑定到演示买家。
    订单无归属（历史数据）时放行，避免旧数据把所有人都挡在门外。
    """
    holder = (order.get("user_id") or "").strip()
    if not holder:
        return True
    requester = (owner_id or "").strip()
    if not requester:
        # 没带账号上下文（如工具被无登录态调用）时按拒绝处理：宁可查不到，也不越权
        return False
    if requester == holder:
        return True
    admin = get_user_by_id(requester)
    return bool(admin and admin.get("role") == "admin")


def bind_ownerless_orders(user_id: str) -> int:
    """把尚无归属的订单绑到指定账号（演示买家），让归属校验有真实数据可用。幂等。

    种子订单是 mock 数据、原本谁都能查，启动时统一挂到演示买家账号下；
    已绑定的订单不会被改写，因此重启或换账号不会把归属挪走。
    """
    if not user_id:
        return 0
    with _tx() as cur:
        cur.execute(
            "UPDATE orders SET user_id=%s WHERE user_id IS NULL OR user_id=''",
            (user_id,),
        )
        return cur.rowcount or 0


def update_order_status(order_id: str, status: str) -> Optional[Dict[str, Any]]:
    """更新订单状态（后台管理员维护，如发货/签收/退款中）；返回更新后的订单，不存在返回 None。"""
    with _tx() as cur:
        cur.execute("SELECT * FROM orders WHERE order_id=%s", (order_id,))
        row = _row(cur.fetchone())
        if row is None:
            return None
        cur.execute("UPDATE orders SET status=%s WHERE order_id=%s", (status, order_id))
        row["status"] = status
        return row


def list_all_orders(status: Optional[str] = None, page: int = 1,
                    page_size: int = 50) -> tuple[List[Dict[str, Any]], int]:
    """后台订单列表（分页）。status 为空/'all' 返回全部，否则按状态过滤；
    返回 (items, total)。供管理端订单页使用。"""
    status = None if status in (None, "", "all") else status
    where, params = "", []
    if status:
        where = " WHERE status=%s"
        params = [status]
    with _tx() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM orders{where}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            f"SELECT * FROM orders{where} ORDER BY created_at DESC, id DESC "
            "LIMIT %s OFFSET %s",
            params + [page_size, max(0, (page - 1) * page_size)],
        )
        return _rows(cur.fetchall()), total


def list_orders_by_user(user_id: str, limit: int = 20) -> tuple[List[Dict[str, Any]], int]:
    """当前账号名下的订单列表（最近下单在前）。返回 (items, total)。

    「查我名下所有订单」的唯一入口：只按归属账号过滤，因此天然不会带出他人订单
    （不需要再逐单调 can_access_order）。user_id 为空直接返回空，避免没有账号上下文
    时把全库订单列出来。
    """
    uid = (user_id or "").strip()
    if not uid:
        return [], 0
    with _tx() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM orders WHERE user_id=%s", (uid,))
        total = cur.fetchone()["n"]
        cur.execute(
            "SELECT * FROM orders WHERE user_id=%s "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (uid, limit),
        )
        return _rows(cur.fetchall()), total


# ====================== 工单 ======================

def create_ticket(ticket_id: str, user_id: str, order_id: str, description: str,
                  priority: str, category: str, note: str = "",
                  stage: str = "") -> Dict[str, Any]:
    """建单。stage 为业务环节（退货/退款类工单才有，如 submitted）。

    工单双轨状态：status 是工单生命周期（待处理/处理中/已解决/已关闭），
    stage 是业务环节（申请→审核→寄回→验收→退款→到账），用户查进度看的是 stage。
    """
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    order_id = order_id or ""
    stage = stage or ""
    note = note or ""
    history = [{"stage": stage, "at": created_at}] if stage else []
    with _tx() as cur:
        cur.execute(
            "INSERT INTO tickets (ticket_id, user_id, order_id, description, priority, "
            "category, status, created_at, note, stage, stage_history, stage_updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ticket_id, user_id, order_id, description, priority, category, "open",
             created_at, note, stage, json.dumps(history, ensure_ascii=False),
             created_at if stage else ""),
        )
    return {
        "ticket_id": ticket_id, "user_id": user_id, "order_id": order_id,
        "description": description, "priority": priority, "category": category,
        "status": "open", "created_at": created_at, "note": note,
        "stage": stage, "stage_history": history,
        "stage_updated_at": created_at if stage else "",
    }


def list_tickets(status: str = "open") -> List[Dict[str, Any]]:
    """供人工客服侧查询工单"""
    with _tx() as cur:
        cur.execute(
            "SELECT * FROM tickets WHERE status=%s ORDER BY created_at DESC, id DESC",
            (status,),
        )
        return _rows(cur.fetchall())


def list_all_tickets(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询全部工单；status 为空/'all' 返回所有，否则按状态过滤。供管理端看板。"""
    with _tx() as cur:
        if status in (None, "", "all"):
            cur.execute("SELECT * FROM tickets ORDER BY created_at DESC, id DESC")
        else:
            cur.execute(
                "SELECT * FROM tickets WHERE status=%s ORDER BY created_at DESC, id DESC",
                (status,),
            )
        return _rows(cur.fetchall())


def list_tickets_paged(status: Optional[str], page: int, page_size: int
                       ) -> List[Dict[str, Any]]:
    """分页查询工单（排序：创建时间倒序，最新在前）。返回当页工单。"""
    offset = max(0, (page - 1) * page_size)
    with _tx() as cur:
        if status in (None, "", "all"):
            cur.execute(
                "SELECT * FROM tickets ORDER BY created_at DESC, id DESC "
                "LIMIT %s OFFSET %s",
                (page_size, offset),
            )
        else:
            cur.execute(
                "SELECT * FROM tickets WHERE status=%s ORDER BY created_at DESC, id DESC "
                "LIMIT %s OFFSET %s",
                (status, page_size, offset),
            )
        return _rows(cur.fetchall())


def count_tickets(status: Optional[str] = None) -> int:
    """统计工单数量；status 为空/'all' 统计全部，否则按状态过滤。"""
    with _tx() as cur:
        if status in (None, "", "all"):
            cur.execute("SELECT COUNT(*) AS n FROM tickets")
        else:
            cur.execute("SELECT COUNT(*) AS n FROM tickets WHERE status=%s", (status,))
        return cur.fetchone()["n"]


# 工单环节 → 工单生命周期状态的自动联动（人工只推环节，状态自动跟随）
_STAGE_TO_STATUS = {
    "submitted": "open",       # 待商家审核
    "completed": "resolved",   # 退款到账，售后办结
    "rejected": "resolved",    # 审核未通过，流程终止
}
# 工单环节 → 订单销售状态的联动（业务数据随售后结果变化）
_STAGE_TO_ORDER_STATUS = {
    "refunding": "refunding",  # 退款已发起
    "completed": "refunded",   # 退款已到账
}


def _parse_stage_history(raw: Any) -> List[Dict[str, str]]:
    """把 stage_history 字段（JSON 文本）解析为列表；脏数据降级为空列表。"""
    if isinstance(raw, list):
        return raw
    try:
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def update_ticket_status(
    ticket_id: str, status: str = None, note: str = None, stage: str = None
) -> Optional[Dict[str, Any]]:
    """更新工单处理状态/处理备注/业务环节；返回更新后的工单，不存在返回 None。

    企业做法：人工在后台只需推进「业务环节」，工单生命周期状态与订单销售状态自动跟随，
    并把每次环节变更追加进 stage_history，用户侧即可看到完整的流转轨迹。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as cur:
        cur.execute("SELECT * FROM tickets WHERE ticket_id=%s", (ticket_id,))
        row = _row(cur.fetchone())
        if row is None:
            return None
        d = dict(row)

        if note is not None:
            cur.execute(
                "UPDATE tickets SET note=%s WHERE ticket_id=%s", (note, ticket_id)
            )
            d["note"] = note

        if stage is not None and stage != (d.get("stage") or ""):
            history = _parse_stage_history(d.get("stage_history"))
            history.append({"stage": stage, "at": now})
            # 人工未显式指定状态时，按环节自动推进工单状态
            if status is None:
                status = _STAGE_TO_STATUS.get(stage, "processing")
            cur.execute(
                "UPDATE tickets SET stage=%s, stage_history=%s, stage_updated_at=%s "
                "WHERE ticket_id=%s",
                (stage, json.dumps(history, ensure_ascii=False), now, ticket_id),
            )
            d["stage"] = stage
            d["stage_history"] = history
            d["stage_updated_at"] = now
            # 业务数据联动：退款环节同步订单销售状态（订单号缺失时跳过）
            order_status = _STAGE_TO_ORDER_STATUS.get(stage)
            if order_status and (d.get("order_id") or ""):
                cur.execute(
                    "UPDATE orders SET status=%s WHERE order_id=%s",
                    (order_status, d["order_id"]),
                )
                log.info(f"工单 {ticket_id} 环节={stage} → 订单 {d['order_id']} 状态={order_status}")

        if status is not None:
            cur.execute(
                "UPDATE tickets SET status=%s WHERE ticket_id=%s", (status, ticket_id)
            )
            d["status"] = status

    d["stage_history"] = _parse_stage_history(d.get("stage_history"))
    return d


def get_ticket(ticket_id: str) -> Optional[Dict[str, Any]]:
    """按工单号查询单条工单；不存在返回 None。stage_history 已解析为列表。"""
    with _tx() as cur:
        cur.execute("SELECT * FROM tickets WHERE ticket_id=%s", (ticket_id,))
        d = _row(cur.fetchone())
    if not d:
        return None
    d["stage_history"] = _parse_stage_history(d.get("stage_history"))
    return d


def query_ticket_progress(query: str, owner: str = "") -> Optional[Dict[str, Any]]:
    """按用户标识或订单号查该用户最近一张工单的处理进度（倒序最新一条）。

    工单的 user_id 存登录账号标识（工单归属账号，不随会话变化），
    order_id 列存关联订单号，描述里也常带订单号，故三者同时匹配，
    取最新一条即可，避免漏查。不存在返回 None。

    owner: 限定工单归属账号。传入后只看 owner 名下的工单 —— 种子订单号是 mock 数据，
    被多个测试账号共用（如 SO20241120005），不加这道收口时 B 账号拿 A 账号的单号一查，
    就会拿到 A 的工单（实测新账号确认退货时返回了 admin 几十分钟前建的工单）。
    """
    if not query:
        return None
    with _tx() as cur:
        # created_at 是秒级字符串，同一秒内建的多张工单会并列，排序结果不确定：
        # 实测「先建退货工单、同秒再建投诉工单」后查进度，返回的是退货那张。
        # 用自增主键 id 兜底（插入顺序），保证「最近一张」始终是最后建的那张。
        if owner:
            cur.execute(
                "SELECT * FROM tickets WHERE (user_id=%s OR order_id=%s OR description LIKE %s) "
                "AND user_id=%s ORDER BY created_at DESC, id DESC LIMIT 1",
                (query, query, f"%{query}%", owner),
            )
        else:
            cur.execute(
                "SELECT * FROM tickets WHERE user_id=%s OR order_id=%s OR description LIKE %s "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (query, query, f"%{query}%"),
            )
        d = _row(cur.fetchone())
    if not d:
        return None
    d["stage_history"] = _parse_stage_history(d.get("stage_history"))
    return d


# ====================== 人工会话消息（转人工实时对话）======================
# 约定：某张工单一旦有了 ticket_messages 记录，它就代表一场「人工会话」。
# AI 自动建的退货/退款工单走阶段化流转，不写消息，因此不会被当成人工会话。

def add_ticket_message(ticket_id: str, sender: str, content: str,
                       sender_id: str = "", sender_name: str = "") -> Dict[str, Any]:
    """追加一条会话消息。sender 为 user（用户）/ agent（人工客服）/ system（系统提示）。"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as cur:
        cur.execute(
            "INSERT INTO ticket_messages (ticket_id, sender, sender_id, sender_name, "
            "content, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (ticket_id, sender, sender_id or "", sender_name or "", content, created_at),
        )
        msg_id = cur.lastrowid
    return {
        "msg_id": msg_id, "ticket_id": ticket_id, "sender": sender,
        "sender_id": sender_id or "", "sender_name": sender_name or "",
        "content": content, "created_at": created_at,
    }


def list_ticket_messages(ticket_id: str, after_id: int = 0,
                         limit: int = 200) -> List[Dict[str, Any]]:
    """按自增 msg_id 升序取消息。after_id 用于增量拉取（断线重连补消息）。"""
    with _tx() as cur:
        cur.execute(
            "SELECT * FROM ticket_messages WHERE ticket_id=%s AND msg_id>%s "
            "ORDER BY msg_id LIMIT %s",
            (ticket_id, after_id, limit),
        )
        return _rows(cur.fetchall())


def get_handoff_ticket(user_id: str) -> Optional[Dict[str, Any]]:
    """查该用户当前进行中的人工会话工单：未关闭且已有消息，取最新一条。

    用「是否已有消息」而非「是否未关闭」判定，避免把 AI 自动建的
    退货/退款工单误当成人工会话。不存在返回 None。
    """
    if not user_id:
        return None
    with _tx() as cur:
        cur.execute(
            "SELECT t.* FROM tickets t WHERE t.user_id=%s AND t.status<>'closed' "
            "AND EXISTS (SELECT 1 FROM ticket_messages m WHERE m.ticket_id=t.ticket_id) "
            "ORDER BY t.created_at DESC, t.id DESC LIMIT 1",
            (user_id,),
        )
        d = _row(cur.fetchone())
    if not d:
        return None
    d["stage_history"] = _parse_stage_history(d.get("stage_history"))
    return d


def list_handoff_sessions() -> List[Dict[str, Any]]:
    """客服工作台会话列表：所有存在消息的工单，带最后一条消息与消息条数。

    按最后一条消息时间倒序，让客服一眼看出谁刚发了新留言。
    关联 users 表带出用户名，客服看到的才不是一串账号 ID。
    """
    with _tx() as cur:
        cur.execute(
            """SELECT t.ticket_id, t.user_id, t.description, t.status, t.priority,
                      t.category, t.created_at,
                      u.username AS username, u.nickname AS nickname,
                      m.content AS last_content, m.sender AS last_sender,
                      m.created_at AS last_at,
                      (SELECT COUNT(*) FROM ticket_messages x
                        WHERE x.ticket_id=t.ticket_id) AS message_count
                 FROM tickets t
                 JOIN (SELECT ticket_id, MAX(msg_id) AS mid FROM ticket_messages
                        GROUP BY ticket_id) lm ON lm.ticket_id = t.ticket_id
                 JOIN ticket_messages m ON m.msg_id = lm.mid
                 LEFT JOIN users u ON u.user_id = t.user_id
                ORDER BY m.msg_id DESC"""
        )
        return _rows(cur.fetchall())


# ====================== DST 会话状态 ======================

def get_conversation_state(session_id: str) -> Optional[Dict[str, Any]]:
    """读取会话状态；slots 字段解析为 dict；不存在返回 None。"""
    with _tx() as cur:
        cur.execute(
            "SELECT * FROM conversation_state WHERE session_id=%s", (session_id,)
        )
        d = _row(cur.fetchone())
    if not d:
        return None
    try:
        d["slots"] = json.loads(d["slots"]) if d["slots"] else {}
    except ValueError:
        d["slots"] = {}
    return d


def upsert_conversation_state(state: Dict[str, Any]) -> None:
    """不存在则 INSERT，存在则整体覆盖（session 级 upsert），幂等。"""
    with _tx() as cur:
        cur.execute(
            """INSERT INTO conversation_state
               (session_id, intent, slots, stage, tool_call_count,
                escalation_reason, answer, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                 intent=VALUES(intent), slots=VALUES(slots), stage=VALUES(stage),
                 tool_call_count=VALUES(tool_call_count),
                 escalation_reason=VALUES(escalation_reason),
                 answer=VALUES(answer), updated_at=VALUES(updated_at)""",
            (state["session_id"], state.get("intent", ""),
             json.dumps(state.get("slots", {}), ensure_ascii=False),
             state.get("stage", "COLLECTING"), state.get("tool_call_count", 0),
             state.get("escalation_reason", "") or "", state.get("answer", "") or "",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


# ====================== 用户账号 ======================

# 对外返回的用户字段（不包含 password_hash / token）
_USER_PUBLIC = ("user_id", "username", "nickname", "role", "status",
                "created_at", "last_login_at")


def _public_user(row: Any) -> Dict[str, Any]:
    d = _row(dict(row)) or {}
    return {k: d.get(k) for k in _USER_PUBLIC}


def create_user(user_id: str, username: str, password_hash: str,
                nickname: str = "", role: str = "user") -> Optional[Dict[str, Any]]:
    """新建用户；用户名已存在返回 None（由调用方转 409）。"""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as cur:
        cur.execute("SELECT 1 AS ok FROM users WHERE username=%s", (username,))
        if cur.fetchone():
            return None
        cur.execute(
            "INSERT INTO users (user_id, username, password_hash, nickname, role, "
            "status, token, created_at, last_login_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (user_id, username, password_hash, nickname or username, role,
             "active", "", created_at, ""),
        )
    return {"user_id": user_id, "username": username,
            "nickname": nickname or username, "role": role,
            "status": "active", "created_at": created_at, "last_login_at": ""}


def get_user_by_id(user_id: str, with_secret: bool = False) -> Optional[Dict[str, Any]]:
    with _tx() as cur:
        cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
        row = _row(cur.fetchone())
    if row is None:
        return None
    return row if with_secret else _public_user(row)


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """含 password_hash，仅供登录校验使用。"""
    with _tx() as cur:
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        return _row(cur.fetchone())


def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    """按登录令牌取用户（简单会话：token 直接存 users 表）。"""
    if not token:
        return None
    with _tx() as cur:
        cur.execute("SELECT * FROM users WHERE token=%s", (token,))
        row = _row(cur.fetchone())
    return _public_user(row) if row else None


def update_user_login(user_id: str, token: str) -> None:
    """登录成功：写入新令牌与最近登录时间。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as cur:
        cur.execute(
            "UPDATE users SET token=%s, last_login_at=%s WHERE user_id=%s",
            (token, now, user_id),
        )


def clear_user_token(user_id: str) -> None:
    """登出：作废令牌。"""
    with _tx() as cur:
        cur.execute("UPDATE users SET token='' WHERE user_id=%s", (user_id,))


def list_users(page: int = 1, page_size: int = 10,
               keyword: str = "") -> tuple[List[Dict[str, Any]], int]:
    """后台用户列表（分页，可按用户名/昵称模糊搜索）。返回 (items, total)。"""
    where, params = "", []
    if keyword:
        where = " WHERE username LIKE %s OR nickname LIKE %s"
        params = [f"%{keyword}%", f"%{keyword}%"]
    with _tx() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM users{where}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            f"SELECT * FROM users{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [page_size, max(0, (page - 1) * page_size)],
        )
        return [_public_user(r) for r in cur.fetchall()], total


def update_user_status(user_id: str, status: str) -> Optional[Dict[str, Any]]:
    """启用/禁用用户；被禁用时同时作废令牌（立即踢下线）。"""
    with _tx() as cur:
        cur.execute("SELECT 1 AS ok FROM users WHERE user_id=%s", (user_id,))
        if not cur.fetchone():
            return None
        if status == "disabled":
            cur.execute(
                "UPDATE users SET status=%s, token='' WHERE user_id=%s", (status, user_id)
            )
        else:
            cur.execute("UPDATE users SET status=%s WHERE user_id=%s", (status, user_id))
    return get_user_by_id(user_id)


def delete_user(user_id: str) -> bool:
    """删除用户；不存在返回 False。"""
    with _tx() as cur:
        cur.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
        return cur.rowcount > 0


def count_users() -> int:
    with _tx() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        return cur.fetchone()["n"]


# ====================== 对话历史（会话 + 消息）======================
# 对话历史是业务数据：以 MySQL 为准（重启进程、Redis 被清空都不丢），
# Redis 只作为热缓存（见 infrastructure/mysql_history.py）。

_TITLE_MAX = 24


def _owner_from_conversation_id(conversation_id: str) -> str:
    """从作用域会话 ID 反解归属账号：形如 u{user_id}_{session_id}。

    格式由 access/history.scoped_session_id 定义。user_id 是 32 位 hex、不含下划线，
    故按第一个下划线切分是安全的；两处格式若被改坏，
    tests/test_chat_history_store.py 的一致性断言会直接失败，不会静默错位。
    """
    cid = conversation_id or ""
    if cid.startswith("u") and "_" in cid:
        return cid[1:].split("_", 1)[0]
    return ""


def ensure_conversation(conversation_id: str, user_id: str = "") -> Dict[str, Any]:
    """确保会话行存在（幂等，已有则不改动）；返回会话行。"""
    cid = (conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id 不能为空")
    uid = (user_id or "").strip() or _owner_from_conversation_id(cid)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as cur:
        cur.execute(
            "INSERT IGNORE INTO conversations (conversation_id, user_id, title, status, "
            "created_at, updated_at) VALUES (%s,%s,'','open',%s,%s)",
            (cid, uid, now, now),
        )
        cur.execute("SELECT * FROM conversations WHERE conversation_id=%s", (cid,))
        return _row(cur.fetchone()) or {}


def append_message(conversation_id: str, role: str, content: str,
                   user_id: str = "") -> Optional[Dict[str, Any]]:
    """追加一条会话消息（seq 自增；会话不存在则自动创建）；空内容返回 None。

    role: user / assistant。标题取首条用户消息，updated_at 每次刷新。
    seq 用「先锁会话行再取 MAX(seq)+1」计算：并发写同一会话时不会拿到同一个序号
    （唯一索引 uk_messages_conv_seq 是最后一道兜底）。
    """
    cid = (conversation_id or "").strip()
    text = (content or "").strip()
    if not cid or not text:
        return None
    role = "assistant" if role in ("ai", "assistant") else "user"
    uid = (user_id or "").strip() or _owner_from_conversation_id(cid)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = text.replace("\n", " ").strip()[:_TITLE_MAX]
    with _tx() as cur:
        cur.execute(
            "INSERT IGNORE INTO conversations (conversation_id, user_id, title, status, "
            "created_at, updated_at) VALUES (%s,%s,'','open',%s,%s)",
            (cid, uid, now, now),
        )
        cur.execute(
            "SELECT conversation_id FROM conversations WHERE conversation_id=%s FOR UPDATE",
            (cid,),
        )
        cur.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM messages "
            "WHERE conversation_id=%s",
            (cid,),
        )
        seq = cur.fetchone()["next_seq"]
        cur.execute(
            "INSERT INTO messages (conversation_id, seq, role, content, created_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (cid, seq, role, text, now),
        )
        msg_id = cur.lastrowid
        cur.execute(
            "UPDATE conversations SET updated_at=%s, "
            "title=CASE WHEN title='' AND %s='user' THEN %s ELSE title END "
            "WHERE conversation_id=%s",
            (now, role, title, cid),
        )
    return {"msg_id": msg_id, "conversation_id": cid, "seq": seq,
            "role": role, "content": text, "created_at": now}


def list_messages(conversation_id: str, limit: int = 0, offset: int = 0
                  ) -> tuple[List[Dict[str, Any]], int]:
    """按 seq 升序取会话消息（limit<=0 取全部）。返回 (items, total)。"""
    cid = (conversation_id or "").strip()
    if not cid:
        return [], 0
    with _tx() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=%s", (cid,)
        )
        total = cur.fetchone()["n"]
        if limit and limit > 0:
            cur.execute(
                "SELECT * FROM messages WHERE conversation_id=%s "
                "ORDER BY seq LIMIT %s OFFSET %s",
                (cid, limit, max(0, offset)),
            )
        else:
            cur.execute(
                "SELECT * FROM messages WHERE conversation_id=%s ORDER BY seq", (cid,)
            )
        return _rows(cur.fetchall()), total


def list_conversations_by_user(user_id: str, page: int = 1, page_size: int = 50
                               ) -> tuple[List[Dict[str, Any]], int]:
    """当前账号的会话列表（最近更新在前，带消息数）。返回 (items, total)。

    只按归属账号过滤，天然看不到他人会话；空会话（只有会话行、没有消息）不列出。
    """
    uid = (user_id or "").strip()
    if not uid:
        return [], 0
    cond = ("FROM conversations c WHERE c.user_id=%s "
            "AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.conversation_id)")
    with _tx() as cur:
        cur.execute(f"SELECT COUNT(*) AS n {cond}", (uid,))
        total = cur.fetchone()["n"]
        cur.execute(
            "SELECT c.conversation_id, c.user_id, c.title, c.status, c.created_at, "
            "c.updated_at, (SELECT COUNT(*) FROM messages m "
            "WHERE m.conversation_id=c.conversation_id) AS message_count "
            f"{cond} ORDER BY c.updated_at DESC, c.conversation_id DESC "
            "LIMIT %s OFFSET %s",
            (uid, page_size, max(0, (page - 1) * page_size)),
        )
        return _rows(cur.fetchall()), total


def get_conversation(conversation_id: str, user_id: str = "") -> Optional[Dict[str, Any]]:
    """按归属账号取会话；不属于该账号（或不存在）返回 None —— 越权一律当作不存在。"""
    cid = (conversation_id or "").strip()
    uid = (user_id or "").strip()
    if not cid or not uid:
        return None
    with _tx() as cur:
        cur.execute(
            "SELECT * FROM conversations WHERE conversation_id=%s AND user_id=%s",
            (cid, uid),
        )
        return _row(cur.fetchone())


def get_latest_conversation_before(user_id: str, before_time: str
                                   ) -> Optional[Dict[str, Any]]:
    """取该账号在某一时刻之前最近活跃的一场会话（用于「转人工前的机器人记录」）。

    tickets 表与 conversations 表之间没有关联字段，所以这里按「归属账号 + 会话创建时间
    不晚于该工单创建时间」反查最近活跃的一场：用户总是从当前正在聊的那场会话点转人工，
    而那场会话正是 updated_at 最新的，因此反查结果稳定，不需要新增字段。

    过滤用 created_at 而不是 updated_at：用户转人工后若又回到 AI 模式继续聊，同一场会话的
    updated_at 会走到工单创建时间之后，用 updated_at 会把这唯一正确的会话漏掉。
    """
    uid = (user_id or "").strip()
    if not uid or not before_time:
        return None
    with _tx() as cur:
        cur.execute(
            "SELECT * FROM conversations WHERE user_id=%s AND created_at<=%s "
            "ORDER BY updated_at DESC, conversation_id DESC LIMIT 1",
            (uid, before_time),
        )
        return _row(cur.fetchone())


def close_conversation(conversation_id: str, user_id: str) -> bool:
    """结束会话（status=closed，历史仍可回看）；非本人会话/不存在/已结束返回 False。"""
    cid = (conversation_id or "").strip()
    uid = (user_id or "").strip()
    if not cid or not uid:
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as cur:
        cur.execute(
            "UPDATE conversations SET status='closed', updated_at=%s "
            "WHERE conversation_id=%s AND user_id=%s AND status='open'",
            (now, cid, uid),
        )
        return cur.rowcount > 0


def delete_conversation(conversation_id: str, user_id: str) -> bool:
    """删除会话及其消息；非本人会话/不存在返回 False。"""
    cid = (conversation_id or "").strip()
    uid = (user_id or "").strip()
    if not cid or not uid:
        return False
    with _tx() as cur:
        cur.execute(
            "DELETE FROM conversations WHERE conversation_id=%s AND user_id=%s",
            (cid, uid),
        )
        if cur.rowcount <= 0:
            return False
        cur.execute("DELETE FROM messages WHERE conversation_id=%s", (cid,))
        return True


def clear_messages(conversation_id: str) -> int:
    """清空某会话的消息（保留会话行）；返回删除条数。"""
    cid = (conversation_id or "").strip()
    if not cid:
        return 0
    with _tx() as cur:
        cur.execute("DELETE FROM messages WHERE conversation_id=%s", (cid,))
        return cur.rowcount or 0


# 模块加载时自动初始化（幂等）
try:
    init_db()
except Exception as e:
    log.error(f"业务库(MySQL)初始化失败: {e}", exc_info=True)