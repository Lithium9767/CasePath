"""Neo4j 只读客户端适配器。

P1 负责驱动、连接池、数据库选择、超时和异常转换；Cypher 路径模板由 P4 提供。
只读路由不是安全边界，正式部署还必须为该连接配置 Neo4j 只读账号。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from casepath.application.errors import GraphUnavailable

QueryFactory = Callable[[str, float], object]


class Neo4jGraphGateway:
    # 【调用链】bootstrap._build_graph_gateway → connect → cls(...) → __init__。
    # 网关只负责数据库访问；找哪些节点、走哪些边由 P4 的 graph_navigator.py 决定。
    def __init__(
        self,
        driver: Any,
        *,
        database: str,
        default_timeout_seconds: float,
        query_factory: QueryFactory,
        read_routing: object,
    ) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")
        self._driver = driver
        # driver 管理连接与连接池；网关另保存目标数据库、查询超时及读取路由。
        self._database = database
        self._default_timeout_seconds = default_timeout_seconds
        self._query_factory = query_factory
        self._read_routing = read_routing
        self._closed = False

    @classmethod
    def connect(
        cls,
        *,
        uri: str,
        user: str,
        password: str,
        database: str,
        connection_timeout_seconds: float,
        query_timeout_seconds: float,
        max_retry_seconds: float,
    ) -> Neo4jGraphGateway:
        """构造连接池但不要求数据库此刻在线，启动阶段通过 capability 探测。"""

        try:
            from neo4j import GraphDatabase, Query, RoutingControl

            driver = GraphDatabase.driver(
                # 构造官方驱动，不等同于已经执行连接探测或下载整个图谱。
                uri,
                auth=(user, password),
                connection_timeout=connection_timeout_seconds,
                connection_acquisition_timeout=connection_timeout_seconds,
                max_transaction_retry_time=max_retry_seconds,
            )
        except Exception as exc:
            raise GraphUnavailable("无法初始化 Neo4j 驱动") from exc
        return cls(
            # cls 就是调用这个类方法的类；这里创建 Neo4jGraphGateway 实例包装 driver。
            driver,
            database=database,
            default_timeout_seconds=query_timeout_seconds,
            query_factory=lambda text, timeout: Query(text, timeout=timeout),
            read_routing=RoutingControl.READ,
        )

    def verify_connectivity(self) -> None:
        # 【实际连接探测】LiveRuntime.capabilities 会调用它，不负责检索案例。
        try:
            self._driver.verify_connectivity()
        except Exception as exc:
            raise GraphUnavailable("Neo4j 连接不可用") from exc

    def execute_read(
        self,
        query: str,
        parameters: Mapping[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> list[Mapping[str, object]]:
        # 【在线检索入口】P4 传入固定 Cypher 模板和参数；这里不生成查询语句。
        if not query.strip():
            raise ValueError("query must not be blank")
        timeout = self._default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        try:
            configured_query = self._query_factory(query, timeout)
            result = self._driver.execute_query(
                # 到这里才提交查询；parameters_ 单独传参数，database_ 指定目标库。
                # READ 是路由选择，不能代替数据库只读账号的权限限制。
                configured_query,
                parameters_=dict(parameters),
                database_=self._database,
                routing_=self._read_routing,
            )
            records = getattr(result, "records", None)
            if records is None:
                records = result[0]
            rows: list[Mapping[str, object]] = []
            # 把 Neo4j 专用 Record 转成普通字典，避免上层算法依赖驱动内部类型。
            for record in records:
                if hasattr(record, "data"):
                    rows.append(record.data())
                elif isinstance(record, Mapping):
                    rows.append(dict(record))
                else:
                    raise TypeError("Neo4j record cannot be converted to a mapping")
            return rows
        except Exception as exc:
            raise GraphUnavailable("Neo4j 只读查询失败") from exc

    def close(self) -> None:
        # 应用关闭或装配失败时释放驱动资源，不在每个用户问题结束后关闭连接池。
        if not self._closed:
            self._driver.close()
            self._closed = True
