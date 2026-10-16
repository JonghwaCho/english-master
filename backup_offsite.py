"""오프사이트 백업 업로드 (S3 호환 스토리지, 외부 의존성 없음).

AWS S3 / Cloudflare R2 / Backblaze B2(S3 API) 등에 백업 파일을 올린다.
boto3 없이 표준 라이브러리만으로 AWS Signature V4(path-style PUT)를 구현한다.

환경변수(모두 설정돼야 활성화):
  BACKUP_S3_ENDPOINT   예) https://s3.ap-northeast-2.amazonaws.com
                            https://<account>.r2.cloudflarestorage.com
  BACKUP_S3_BUCKET     버킷 이름
  BACKUP_S3_ACCESS_KEY / BACKUP_S3_SECRET_KEY
선택:
  BACKUP_S3_REGION     기본 us-east-1 (R2는 auto 권장)
  BACKUP_S3_PREFIX     기본 english-master-backups
"""
import os
import hmac
import hashlib
import logging
import datetime
import urllib.request
import urllib.parse


def _cfg():
    return {
        "endpoint": (os.environ.get("BACKUP_S3_ENDPOINT", "") or "").rstrip("/"),
        "bucket": os.environ.get("BACKUP_S3_BUCKET", "") or "",
        "region": (os.environ.get("BACKUP_S3_REGION", "") or "us-east-1"),
        "access": os.environ.get("BACKUP_S3_ACCESS_KEY", "") or "",
        "secret": os.environ.get("BACKUP_S3_SECRET_KEY", "") or "",
        "prefix": (os.environ.get("BACKUP_S3_PREFIX", "english-master-backups") or "").strip("/"),
    }


def is_configured(cfg=None):
    c = cfg or _cfg()
    return all([c["endpoint"], c["bucket"], c["access"], c["secret"]])


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _hmac(key, msg):
    if isinstance(msg, str):
        msg = msg.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).digest()


def signing_key(secret, datestamp, region, service):
    """AWS SigV4 서명 키 파생(kDate→kRegion→kService→kSigning)."""
    k_date = _hmac(("AWS4" + secret).encode("utf-8"), datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def build_authorization(method, canonical_uri, query, headers, payload_hash,
                        access, secret, region, service, amz_date, datestamp):
    """SigV4 Authorization 헤더 값과 signature(hex)를 만든다. headers는 {k:v} dict."""
    items = {k.lower(): str(v).strip() for k, v in headers.items()}
    signed_headers = ";".join(sorted(items))
    canonical_headers = "".join(f"{k}:{items[k]}\n" for k in sorted(items))
    canonical_request = "\n".join([
        method, canonical_uri, query or "", canonical_headers, signed_headers, payload_hash,
    ])
    algorithm = "AWS4-HMAC-SHA256"
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        algorithm, amz_date, scope, _sha256_hex(canonical_request.encode("utf-8")),
    ])
    sig = hmac.new(signing_key(secret, datestamp, region, service),
                   string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (f"{algorithm} Credential={access}/{scope}, "
                     f"SignedHeaders={signed_headers}, Signature={sig}")
    return authorization, sig


def upload_file(local_path, key=None, cfg=None):
    """백업 파일 1개를 S3 호환 스토리지에 PUT 한다. (성공?, 메시지)."""
    c = cfg or _cfg()
    if not is_configured(c):
        return False, "not_configured"
    name = os.path.basename(local_path)
    object_key = "/".join([p for p in [c["prefix"], key or name] if p])
    with open(local_path, "rb") as f:
        body = f.read()
    payload_hash = _sha256_hex(body)

    host = urllib.parse.urlparse(c["endpoint"]).netloc
    # path-style: /<bucket>/<key> (키 세그먼트는 인코딩, '/'는 보존)
    canonical_uri = "/" + c["bucket"] + "/" + urllib.parse.quote(object_key)
    now = datetime.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    sign_headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    authorization, _ = build_authorization(
        "PUT", canonical_uri, "", sign_headers, payload_hash,
        c["access"], c["secret"], c["region"], "s3", amz_date, datestamp)

    req = urllib.request.Request(c["endpoint"] + canonical_uri, data=body, method="PUT", headers={
        "Authorization": authorization,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(body)),
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return True, f"{resp.status} -> {object_key}"
    except Exception as e:  # noqa: BLE001
        logging.warning(f"[OffsiteBackup] upload failed: {e}")
        return False, str(e)
