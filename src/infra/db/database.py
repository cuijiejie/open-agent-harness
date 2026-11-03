"""数据库连接管理：SQLAlchemy 引擎、Session 工厂、请求级 session 生命周期"""
from flask import g
from sqlalchemy import create_engine, Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from config.Configs import cfg

DATABASE_URL: str = f"mysql+pymysql://{cfg.mysqlUser}:{cfg.mysqlPass}@{cfg.mysqlHost}:{cfg.mysqlPort}/{cfg.mysqlDB}"

engine: Engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
    # 固定会话时区为 UTC：列里有的是代码写 timezone.utc、有的是 MySQL 的
    # func.now()（服务器本地时间，CST=UTC+8）。不统一的话同一列混两种时钟——
    # 归档一次 updated_at 就"跳 8 小时"，会话列表排序和 trace 的 hours 过滤都会错。
    connect_args={"init_command": "SET time_zone = '+00:00'"},
)
SessionLocal: sessionmaker = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Session:
    """获取请求级共享 DB session，同一请求内多次调用返回同一个 session"""
    if 'db' not in g:
        g.db = SessionLocal()
    return g.db


def close_db(exception: Exception | None = None) -> None:
    """请求结束时关闭 DB session，由 Flask teardown_appcontext 自动调用"""
    db: Session | None = g.pop('db', None)
    if db is not None:
        db.close()
