"""图谱浏览只读接口：分页读取当前运行版本，不接受客户端 Cypher。"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/v1/graph", tags=["graph"])


@router.get("")
def graph_page(
    request: Request,
    kind: Literal["nodes", "edges"] = "nodes",
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=2000),
    release: str | None = None,
) -> dict:
    runtime = getattr(request.app.state, "live_runtime", None)
    gateway = getattr(runtime, "graph_gateway", None)
    if gateway is None:
        raise HTTPException(503, "未配置在线图谱，不能展示模拟全图")
    projector = runtime.service.workflow.dependencies.condition_projector
    catalog = getattr(projector, "catalog", None)
    if catalog is None:
        raise HTTPException(503, "当前组件未提供图谱发布版本")
    current = catalog.release_id
    if release is not None and release != current:
        raise HTTPException(409, "图谱版本已变化，请重新加载")
    # key 含实体类型，避免不同类型的相同 object_id 在前端被误合并。
    if kind == "nodes":
        query = """
        MATCH (n:CasePathEntity {release_id:$release})
        RETURN n.key AS id, n.object_id AS object_id,
          [label IN labels(n) WHERE label <> 'CasePathEntity'][0] AS category,
          left(toString(coalesce(n.title,n.name,n.predicate,n.text,n.object_id)),160) AS name
        ORDER BY n.key SKIP $offset LIMIT $limit
        """
    else:
        query = """
        MATCH (a:CasePathEntity {release_id:$release})-[r]->
              (b:CasePathEntity {release_id:$release})
        RETURN a.key AS source, b.key AS target, type(r) AS relation,
          a.object_id AS source_object_id, b.object_id AS target_object_id
        ORDER BY a.key,type(r),b.key SKIP $offset LIMIT $limit
        """
    rows = gateway.execute_read(
        query, {"release": current, "offset": offset, "limit": limit + 1}
    )
    return {
        "release_id": current, "items": rows[:limit],
        "next_offset": offset + limit if len(rows) > limit else None,
    }
