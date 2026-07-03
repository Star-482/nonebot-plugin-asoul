"""
@Author: star_482
@Date: 2026/5/26
@File: cos_client
@Description: 腾讯云 COS (S3 兼容) 客户端单例。封装同步底层操作，由 COSBucket 通过 to_thread 调用。
"""
from threading import Lock
from typing import Optional

from nonebot.log import logger

from ..config import config

_client = None
_lock = Lock()


def _build_client():
    """懒构造 boto3 S3 客户端。延迟导入 boto3，避免插件加载时强依赖未安装的依赖。

    走 S3 兼容协议连接腾讯云 COS。region 必须填实际区域（如 ap-guangzhou），
    否则 SigV4 签名校验失败。endpoint 填区域级地址（不含 bucket 名），
    设 addressing_style=virtual 由 boto3 自动将 bucket 名拼入 host，
    避免 COS 返回 PathStyleDomainForbidden。
    """
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.session.Session().client(
        service_name="s3",
        endpoint_url=config.cos_url,
        aws_access_key_id=config.cos_id,
        aws_secret_access_key=config.cos_key,
        region_name=config.cos_region,
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "virtual"},
        ),
    )


def get_client():
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = _build_client()
                logger.info("COS 客户端已初始化")
    return _client


def reset_client():
    """仅用于测试或凭据热重载。"""
    global _client
    with _lock:
        _client = None


# ── 同步底层操作（异常会上抛，由调用方 try/except 转为 None）──

def put_object_sync(key: str, data: bytes, content_type: str, metadata: dict | None = None):
    kwargs = dict(
        Bucket=config.cos_bucket_name, Key=key, Body=data, ContentType=content_type,
    )
    if metadata:
        kwargs["Metadata"] = {k: str(v) for k, v in metadata.items()}
    client = get_client()
    client.put_object(**kwargs)


def head_object_sync(key: str) -> bool:
    """对象存在返回 True，404/NoSuchKey 返回 False，其他异常上抛。"""
    from botocore.exceptions import ClientError

    client = get_client()
    try:
        client.head_object(Bucket=config.cos_bucket_name, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return False
        raise


def head_object_meta_sync(key: str) -> dict | None:
    """HEAD 对象并返回用户自定义元数据，不存在返回 None。"""
    from botocore.exceptions import ClientError

    client = get_client()
    try:
        resp = client.head_object(Bucket=config.cos_bucket_name, Key=key)
        return resp.get("Metadata") or {}
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return None
        raise


def delete_object_sync(key: str):
    client = get_client()
    client.delete_object(Bucket=config.cos_bucket_name, Key=key)


def public_url_for(key: str) -> str:
    base = (config.cos_public_url or "").rstrip("/")
    if not base:
        from urllib.parse import urlparse
        p = urlparse(config.cos_url)
        base = f"{p.scheme}://{config.cos_bucket_name}.{p.hostname}"
    return f"{base}/{key}"
