"""本地 Skill 管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.v1.schemas.local_skill import LocalSkillResponse
from app.api.v1.schemas.skill_pull import PullSkillResponse, RemoteCatalogItem
from app.common.errors import AppError
from app.api.v1.deps import get_local_skill_service, get_skill_pull_service

router = APIRouter()

_ERROR_STATUS = {
    "LOCAL_SKILL_NOT_FOUND": 404,
    "LOCAL_SKILL_INVALID_ID": 400,
    "LOCAL_SKILL_DELETE_FAILED": 500,
    "PULL_SERVER_NOT_CONFIGURED": 400,
    "PULL_SERVER_UNREACHABLE": 502,
    "PULL_SERVER_ERROR": 502,
    "REMOTE_SKILL_NOT_FOUND": 404,
    "PULL_EXTRACT_FAILED": 500,
    "IMPORT_INVALID_ZIP": 400,
    "IMPORT_MISSING_SKILL_MD": 400,
    "IMPORT_MISSING_NAME": 400,
    "IMPORT_MISSING_DESCRIPTION": 400,
    "IMPORT_EXTRACT_FAILED": 500,
}


# ── 本地 Skill ──────────────────────────────────────────────────────────────

@router.post("/import", response_model=LocalSkillResponse)
async def import_local_skill(file: UploadFile = File(...)) -> LocalSkillResponse:
    """上传 zip 包并导入为本地 skill。"""
    data = await file.read()
    try:
        result = get_local_skill_service().import_skill(data)
    except AppError as e:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(e.code, 400),
            detail={"code": e.code, "message": e.message},
        )
    return LocalSkillResponse(**result)


@router.get("", response_model=list[LocalSkillResponse])
def list_local_skills() -> list[LocalSkillResponse]:
    """列出所有本地 skill。"""
    items = get_local_skill_service().list_skills()
    return [LocalSkillResponse(**item) for item in items]


@router.delete("/{skill_id}", status_code=204)
def delete_local_skill(skill_id: str) -> None:
    """删除指定本地 skill（同时删除目录）。"""
    try:
        get_local_skill_service().delete_skill(skill_id)
    except AppError as e:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(e.code, 500),
            detail={"code": e.code, "message": e.message},
        )


# ── 远端 Skill 拉取（路由须在 /{skill_id} 之前注册） ────────────────────────

@router.post("/pull-server/import", response_model=PullSkillResponse)
async def import_remote_skill(file: UploadFile = File(...)) -> PullSkillResponse:
    """将 zip 包上传到远端 skill 服务器的 POST /skills/import。"""
    data = await file.read()
    try:
        result = get_skill_pull_service().import_to_remote(data, file.filename or "skill.zip")
    except AppError as e:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(e.code, 500),
            detail={"code": e.code, "message": e.message},
        )
    return PullSkillResponse(**result)


@router.get("/pull-server/catalog", response_model=list[RemoteCatalogItem])
def list_remote_catalog() -> list[RemoteCatalogItem]:
    """从远端服务器获取可用 skill 列表。"""
    try:
        items = get_skill_pull_service().list_remote()
    except AppError as e:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(e.code, 500),
            detail={"code": e.code, "message": e.message},
        )
    return [RemoteCatalogItem(**item) for item in items]


@router.post("/pull-server/catalog/{remote_id}/pull", response_model=PullSkillResponse)
def pull_skill(remote_id: str, body: dict) -> PullSkillResponse:
    """从远端拉取指定 skill 并解压到本地 skills_dir。

    Request body: {"name": "<skill name>"}
    """
    skill_name = (body.get("name") or "").strip()
    if not skill_name:
        raise HTTPException(status_code=400, detail={"code": "MISSING_NAME", "message": "name 不能为空"})
    try:
        result = get_skill_pull_service().pull_skill(remote_id, skill_name)
    except AppError as e:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(e.code, 500),
            detail={"code": e.code, "message": e.message},
        )
    return PullSkillResponse(**result)
