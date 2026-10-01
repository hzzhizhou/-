"""对话历史持久化单元测试。

背景：历史此前只存在 Redis 的单个 JSON 数组 key 里 —— Redis 一挂或 key 到期，
用户端「历史会话」就整段消失。改造后以 MySQL（conversations / messages 两表）
为准、Redis 只做热缓存，本组用例锁住四件事：
  1) 写入即落库（逐条 INSERT、seq 递增），换实例/重启进程后仍读得到；
  2) 作用域会话 ID 与归属账号能在库层互解（两处格式必须一致）；
  3) 列表/读取/删除/结束都按归属账号过滤，越权一律当作「不存在」；
  4) Redis 不可用时读写仍成功（fail-open），只是回源查库。

⚠️ 本组是「真落库」用例，需要有可用的 MySQL（配置见 .env）；
   连不上时整组 skip，不影响其余纯逻辑用例。用到的会话行在前后都会按
   测试专用账号前缀清理，不会残留在业务库里。
"""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import infrastructure.redis.connection as redis_conn
from access import history
from infrastructure import mysql_store
from infrastructure.mysql_history import MysqlChatHistory

UID_A = "a" * 32
UID_B = "b" * 32
SID = "session0001"


def _cleanup_test_sessions() -> None:
    """按测试专用账号前缀清掉本组用例写进去的会话与消息。"""
    for uid in (UID_A, UID_B):
        prefix = f"u{uid}_%"
        with mysql_store._tx() as cur:
            cur.execute("DELETE FROM messages WHERE conversation_id LIKE %s", (prefix,))
            cur.execute("DELETE FROM conversations WHERE conversation_id LIKE %s", (prefix,))


@pytest.fixture
def db():
    """取业务库句柄；MySQL 不可用则跳过（历史持久化依赖真实数据库）。"""
    try:
        mysql_store.init_db()
    except Exception as e:
        pytest.skip(f"MySQL 不可用，跳过持久化用例：{e}")
    _cleanup_test_sessions()
    yield mysql_store
    _cleanup_test_sessions()


@pytest.fixture
def redis_down(monkeypatch):
    """模拟 Redis 不可用：拿连接即抛错（与真实降级路径一致）。"""
    def _boom(*_a, **_k):
        raise RuntimeError("redis unavailable")

    async def _aboom(*_a, **_k):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(redis_conn, "get_redis_connection", _boom)
    monkeypatch.setattr(redis_conn, "get_redis_async", _aboom)


# ---------- 作用域 ID 与归属账号 ----------
def test_scoped_id_and_owner_are_inverse():
    """history 组 ID 与 mysql_store 解账号必须互为逆运算（两处格式不能各改各的）。"""
    scoped = history.scoped_session_id(UID_A, SID)
    assert scoped == f"u{UID_A}_{SID}"
    assert mysql_store._owner_from_conversation_id(scoped) == UID_A
    assert mysql_store._owner_from_conversation_id("非法ID") == ""


# ---------- 库层：写入 ----------
def test_append_message_creates_conversation(db):
    cid = f"u{UID_A}_{SID}"
    msg = db.append_message(cid, "user", "你好")
    assert msg["seq"] == 1
    conv = db.get_conversation(cid, UID_A)
    assert conv["user_id"] == UID_A
    assert conv["status"] == "open"


def test_seq_increments_and_title_from_first_user_message(db):
    cid = f"u{UID_A}_{SID}"
    db.append_message(cid, "assistant", "您好，请问有什么可以帮您")  # 先来一条助手消息
    db.append_message(cid, "user", "订单 SO20241120005 到哪了")
    db.append_message(cid, "assistant", "已发货")
    rows, total = db.list_messages(cid)
    assert total == 3
    assert [r["seq"] for r in rows] == [1, 2, 3]
    # 标题取首条「用户」消息，而不是首条消息
    assert db.get_conversation(cid, UID_A)["title"] == "订单 SO20241120005 到哪了"


def test_blank_message_is_not_stored(db):
    cid = f"u{UID_A}_{SID}"
    assert db.append_message(cid, "user", "   ") is None
    assert db.list_messages(cid) == ([], 0)


def test_title_is_truncated(db):
    cid = f"u{UID_A}_{SID}"
    db.append_message(cid, "user", "这个问题非常非常长需要被截断处理一二三四五六七八九十")
    title = db.get_conversation(cid, UID_A)["title"]
    assert 0 < len(title) <= mysql_store._TITLE_MAX


def test_list_messages_limit_offset_takes_tail(db):
    cid = f"u{UID_A}_{SID}"
    for i in range(5):
        db.append_message(cid, "user", f"问题{i}")
    _, total = db.list_messages(cid, limit=1)
    rows, _ = db.list_messages(cid, limit=2, offset=max(0, total - 2))
    assert [r["content"] for r in rows] == ["问题3", "问题4"]


# ---------- 库层：列表与隔离 ----------
def test_list_conversations_only_own_account(db):
    db.append_message(f"u{UID_A}_session0001", "user", "A 的会话")
    db.append_message(f"u{UID_B}_session0002", "user", "B 的会话")
    items, total = db.list_conversations_by_user(UID_A)
    assert total == 1
    assert items[0]["conversation_id"] == f"u{UID_A}_session0001"


def test_empty_conversation_is_not_listed(db):
    db.ensure_conversation(f"u{UID_A}_emptysess001", UID_A)
    assert db.list_conversations_by_user(UID_A) == ([], 0)


def test_blank_account_lists_nothing(db):
    db.append_message(f"u{UID_A}_session0001", "user", "A 的会话")
    assert db.list_conversations_by_user("") == ([], 0)


# ---------- 用户端 API：列表 ----------
def test_list_history_sessions_strips_scope_and_puts_open_first(db):
    c1 = history.scoped_session_id(UID_A, "session0001")
    c2 = history.scoped_session_id(UID_A, "session0002")
    db.append_message(c1, "user", "第一个会话")
    db.append_message(c2, "user", "第二个会话")
    assert db.close_conversation(c2, UID_A) is True

    sessions = asyncio.run(history.list_history_sessions(UID_A))
    # 返回给前端的是「前端会话 ID」，不带归属账号前缀
    assert [s["session_id"] for s in sessions] == ["session0001", "session0002"]
    # 进行中的置顶，已结束的排后
    assert [s["status"] for s in sessions] == ["open", "closed"]


def test_history_session_carries_title_and_count(db):
    cid = history.scoped_session_id(UID_A, "session0003")
    db.append_message(cid, "user", "iPhone 15 保修多久")
    db.append_message(cid, "assistant", "保修 12 个月")
    sessions = asyncio.run(history.list_history_sessions(UID_A))
    assert sessions[0]["title"] == "iPhone 15 保修多久"
    assert sessions[0]["message_count"] == 2


def test_list_history_sessions_never_leaks_other_account(db):
    db.append_message(history.scoped_session_id(UID_B, "session0007"), "user", "B 的私密提问")
    assert asyncio.run(history.list_history_sessions(UID_A)) == []


# ---------- 用户端 API：读消息 ----------
def test_get_history_messages_maps_roles(db):
    cid = history.scoped_session_id(UID_A, "session0004")
    db.append_message(cid, "user", "问题")
    db.append_message(cid, "assistant", "回答")
    msgs = asyncio.run(history.get_history_messages(UID_A, "session0004"))
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "问题"


def test_get_history_messages_other_account_gets_nothing(db):
    cid = history.scoped_session_id(UID_A, SID)
    db.append_message(cid, "user", "A 的私密内容")
    # B 拿着 A 的 session_id 也读不到：作用域 ID 用 B 的账号拼，库里不存在该会话
    assert asyncio.run(history.get_history_messages(UID_B, SID)) == []


def test_invalid_session_id_returns_nothing():
    for bad in ("", "短", "../etc/passwd", "a" * 65):
        assert asyncio.run(history.get_history_messages(UID_A, bad)) == []


# ---------- 用户端 API：删除 / 结束 ----------
def test_delete_only_own_session(db):
    cid = history.scoped_session_id(UID_A, SID)
    db.append_message(cid, "user", "A 的会话")
    assert asyncio.run(history.delete_history_session(UID_B, SID)) is False
    assert db.list_messages(cid)[1] == 1          # 越权删除没有生效
    assert asyncio.run(history.delete_history_session(UID_A, SID)) is True
    assert db.list_messages(cid)[1] == 0


def test_close_is_idempotent_and_scoped(db):
    cid = history.scoped_session_id(UID_A, SID)
    db.append_message(cid, "user", "A 的会话")
    assert asyncio.run(history.close_history_session(UID_B, SID)) is False
    assert asyncio.run(history.close_history_session(UID_A, SID)) is True
    assert asyncio.run(history.close_history_session(UID_A, SID)) is True   # 幂等
    assert db.get_conversation(cid, UID_A)["status"] == "closed"
    # 结束只是打标记，历史仍可回看
    assert len(asyncio.run(history.get_history_messages(UID_A, SID))) == 1


# ---------- 持久化：重启进程 / Redis 不可用 ----------
def test_history_survives_process_restart(db, redis_down):
    """写完换一个实例（等同重启进程/换 worker）仍读得到：历史在库里，不在内存/Redis。"""
    cid = history.scoped_session_id(UID_A, SID)
    first = MysqlChatHistory(cid)
    first.add_message(HumanMessage(content="我的退款进度"))
    first.add_message(AIMessage(content="退款已到账"))

    second = MysqlChatHistory(cid)
    assert [m.content for m in second.messages()] == ["我的退款进度", "退款已到账"]


def test_async_read_write_with_redis_down(db, redis_down):
    cid = history.scoped_session_id(UID_A, "session0009")
    hist = MysqlChatHistory(cid)
    asyncio.run(hist.async_add_message(HumanMessage(content="问题")))
    asyncio.run(hist.async_add_message(AIMessage(content="回答")))
    assert [m.content for m in asyncio.run(hist.amessages())] == ["问题", "回答"]


def test_history_list_and_delete_do_not_depend_on_redis(db, redis_down):
    cid = history.scoped_session_id(UID_A, SID)
    db.append_message(cid, "user", "不依赖 Redis")
    sessions = asyncio.run(history.list_history_sessions(UID_A))
    assert sessions and sessions[0]["title"] == "不依赖 Redis"
    # 删除时清理热缓存失败不能影响删除结果
    assert asyncio.run(history.delete_history_session(UID_A, SID)) is True