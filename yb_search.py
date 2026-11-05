# -*- coding: utf-8 -*-
"""
元宝搜索（Tencent Yuanbao Search / wsa）零依赖客户端

只用 Python 标准库：手写 TC3-HMAC-SHA256 签名、urllib 发请求、
按档位自适应裁剪参数、解析 Pages 二次 JSON、去重与权威度打分。

作者：邓家龙    许可：MIT
"""

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

HOST = "wsa.tencentcloudapi.com"
SERVICE = "wsa"
ACTION = "SearchPro"
VERSION = "2025-05-08"
ENDPOINT = "https://" + HOST

# ---------------------------------------------------------------- 档位能力表
# 元宝搜索有三个档位（极速 / 标准 / 旗舰），字段与入参差异极大。
# 个人用户（TOC）免费额度只能用在极速版与标准版，因此这份表不是理论值，
# 而是写代码时必须遵守的硬约束。
FIELD_OWNER = {
    "content":         {"standard", "flagship"},   # 动态摘要
    "score":           {"standard", "flagship"},   # 相关性得分 0~1
    "pics":            {"standard", "flagship"},   # 配图
    "authority_level": {"flagship"},               # 权威度 0~5，仅旗舰版
    "deeplinks":       {"flagship"},               # 附件子链，仅旗舰版
}
PARAM_OWNER = {
    "Mode":      {"standard", "flagship"},         # 极速版不支持
    "Cnt":       {"standard", "flagship"},         # 极速版不支持
    "Industry":  {"standard", "flagship"},         # 极速版不支持
    "Deeplinks": {"flagship"},                     # 仅旗舰版
}

TIER_CN = {"lite": "极速版", "standard": "标准版", "flagship": "旗舰版"}

_TIER_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tier_cache.json")


# ------------------------------------------------------------------ 签名部分
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def build_authorization(secret_id: str, secret_key: str, payload: str, action: str, ts: int = None):
    """按腾讯云 API 3.0 规范生成 TC3-HMAC-SHA256 签名头。"""
    ts = ts or int(time.time())
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    content_type = "application/json; charset=utf-8"

    canonical_headers = (
        "content-type:%s\nhost:%s\nx-tc-action:%s\n"
        % (content_type, HOST, action.lower())
    )
    signed_headers = "content-type;host;x-tc-action"

    canonical_request = "\n".join([
        "POST",
        "/",
        "",
        canonical_headers,
        signed_headers,
        _sha256_hex(payload),
    ])

    credential_scope = "%s/%s/tc3_request" % (date, SERVICE)
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(ts),
        credential_scope,
        _sha256_hex(canonical_request),
    ])

    k_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    k_service = _hmac(k_date, SERVICE)
    k_signing = _hmac(k_service, "tc3_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth = (
        "TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (secret_id, credential_scope, signed_headers, signature)
    )
    return auth, ts


# ------------------------------------------------------------------ 底层调用
class YuanbaoError(Exception):
    pass


# 这些错误码属瞬时抖动，退避重试有效；鉴权、参数、欠费类重试无意义
RETRYABLE = ("ResourceNotFound", "InternalError", "RequestLimitExceeded")


def call_api(params: dict, secret_id: str, secret_key: str, timeout: int = 15, retry: int = 2) -> dict:
    """
    发起一次 SearchPro 调用，返回 Response 体。

    实测坑：偶发回 `ResourceNotFound: 服务未开通`，其实服务是通的，
    隔一秒重试即成功。若不重试，用户会误判成权限/开通问题。
    """
    if not secret_id or not secret_key:
        raise YuanbaoError("缺少 SecretId / SecretKey")

    last = None
    for attempt in range(retry + 1):
        try:
            return _call_once(params, secret_id, secret_key, timeout)
        except YuanbaoError as e:
            last = e
            code = str(e).split(":")[0].strip()
            if code not in RETRYABLE or attempt == retry:
                raise
            time.sleep(1.2 * (attempt + 1))
    raise last


def _call_once(params: dict, secret_id: str, secret_key: str, timeout: int = 15) -> dict:
    payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
    auth, ts = build_authorization(secret_id, secret_key, payload, ACTION)

    headers = {
        "Authorization": auth,
        "Content-Type": "application/json; charset=utf-8",
        "Host": HOST,
        "X-TC-Action": ACTION,
        "X-TC-Version": VERSION,
        "X-TC-Timestamp": str(ts),
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=payload.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        raise YuanbaoError("HTTP %s: %s" % (e.code, raw[:300]))
    except Exception as e:
        raise YuanbaoError("请求失败: %s" % e)

    resp_body = body.get("Response", {})
    if "Error" in resp_body:
        err = resp_body["Error"]
        raise YuanbaoError("%s: %s" % (err.get("Code"), err.get("Message")))
    return resp_body


def probe_tier(secret_id: str, secret_key: str, use_cache: bool = True) -> str:
    """
    探明当前账号实际可用的档位。

    为什么要探：Version 由服务开通时的配置决定，请求里改不了。
    个人用户默认额度只覆盖极速版/标准版，若不知道档位就传 Cnt/Mode，
    极速版会直接回 InvalidParameter。所以先发一个最简请求把档位读回来。
    """
    if use_cache and os.path.exists(_TIER_CACHE):
        try:
            with open(_TIER_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("secret_id_tail") == secret_id[-6:] and time.time() - cached.get("ts", 0) < 86400:
                return cached.get("tier", "lite")
        except Exception:
            pass

    resp = call_api({"Query": "元宝搜索"}, secret_id, secret_key)
    tier = (resp.get("Version") or "lite").lower()
    if tier not in TIER_CN:
        tier = "lite"
    try:
        with open(_TIER_CACHE, "w", encoding="utf-8") as f:
            json.dump({"secret_id_tail": secret_id[-6:], "tier": tier, "ts": time.time()}, f)
    except Exception:
        pass
    return tier


def trim_params(params: dict, tier: str) -> dict:
    """按档位裁剪入参——不裁剪就等着收 InvalidParameter。"""
    allowed = {"Query", "Site", "Freshness"}
    for k in PARAM_OWNER:
        if tier in PARAM_OWNER[k]:
            allowed.add(k)
    return {k: v for k, v in params.items() if k in allowed and v not in (None, "")}


# -------------------------------------------------------------- 结果解析与清洗
def parse_pages(pages) -> list:
    """
    Pages 是一个「字符串数组」，每个元素本身是 JSON 字符串，
    必须再 load 一次。这是接入时第一个容易绊倒人的地方。
    """
    docs = []
    for raw in pages or []:
        if isinstance(raw, dict):
            docs.append(raw)
            continue
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                docs.append(obj)
        except (ValueError, TypeError):
            continue
    return docs


def _norm_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower().replace("www.", "")
        path = (p.path or "/").rstrip("/")
        return host + path
    except Exception:
        return url


_PUNC = re.compile(r"[\s\u3000\-—–_|·•:：,，.。;；!！?？\"'“”‘’()（）\[\]【】<>《》/\\]+")


def _norm_title(t: str) -> str:
    return _PUNC.sub("", t or "").lower()


def dedupe(docs: list) -> list:
    seen_url, seen_title, out = set(), set(), []
    for d in docs:
        u = _norm_url(d.get("url", ""))
        t = _norm_title(d.get("title", ""))
        if u and u in seen_url:
            continue
        if t and t in seen_title:
            continue
        if u:
            seen_url.add(u)
        if t:
            seen_title.add(t)
        out.append(d)
    return out


# 域名权威权重：拿不到旗舰版的 authority_level，就用域名做近似替代
DOMAIN_RULES = [
    (("gov.cn",), 1.00),
    (("edu.cn",), 0.96),
    (("pep.com.cn", "ncet.gov.cn", "smartedu.cn"), 0.96),
    (("cnki.net", "wanfangdata.com.cn", "cqvip.com"), 0.92),
    (("xinhuanet.com", "people.com.cn", "cctv.com", "gmw.cn"), 0.90),
    (("qstheory.cn", "chinadaily.com.cn", "cnipa.gov.cn"), 0.88),
    (("baike.baidu.com", "wikipedia.org"), 0.72),
    (("zhihu.com", "bilibili.com", "csdn.net", "jianshu.com"), 0.62),
    (("sohu.com", "163.com", "sina.com.cn", "qq.com", "ifeng.com"), 0.55),
]
JUNK_HINT = ("复制", "广告", "点击查看", "下载APP", "登录后查看")


def domain_weight(url: str) -> float:
    host = urlparse(url).netloc.lower()
    for keys, w in DOMAIN_RULES:
        if any(k in host for k in keys):
            return w
    return 0.50


def _parse_date(s: str):
    s = (s or "").strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d",
                "%Y年%m月%d日", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def freshness_score(date_str: str) -> float:
    d = _parse_date(date_str)
    if not d:
        return 0.6
    days = max(0, (datetime.now() - d).days)
    if days <= 30:
        return 1.0
    if days <= 180:
        return 0.9
    if days <= 365:
        return 0.8
    if days <= 365 * 3:
        return 0.7
    return 0.55


def relevance_score(doc: dict, terms: list) -> float:
    """优先用接口给的 score；极速版没有 score，就退回词面命中率。"""
    raw = doc.get("score")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    text = (doc.get("title", "") + doc.get("passage", "") + doc.get("content", "")).lower()
    if not terms or not text:
        return 0.5
    hit = sum(1 for t in terms if t and t.lower() in text)
    return min(1.0, 0.35 + 0.65 * hit / len(terms))


def build_terms(topic: str, extra: str = "") -> list:
    raw = topic + " " + (extra or "")
    parts = [p for p in re.split(r"[\s,，、;；]+", raw) if p]
    terms = []
    for p in parts:
        terms.append(p)
        # 长词再切出二字片段，提高词面命中判定的召回
        if len(p) >= 4:
            for i in range(0, len(p) - 1, 2):
                terms.append(p[i:i + 2])
    return list(dict.fromkeys(terms))[:12]


# ------------------------------------------------------------------ 检索编排
# 四类检索式：把「备课」这件事拆成四种不同的信息需求，分别检索
def build_queries(topic: str, stage: str = "小学", subject: str = "科学") -> list:
    return [
        {"cat": "教材依据", "q": "%s %s %s 教材 课标 知识点 讲解" % (stage, subject, topic)},
        {"cat": "考点解析", "q": "%s %s %s 易错点 学业质量 评价 解析" % (stage, subject, topic)},
        {"cat": "实践活动", "q": "%s %s %s 上机 实践 活动设计 课例" % (stage, subject, topic)},
        {"cat": "延伸拓展", "q": "%s 最新 研究 进展 应用 案例" % topic},
    ]


def aggregate(docs_by_cat: dict, topic: str, tier: str, keep: int = 4) -> dict:
    """合并、去重、打分、归类，产出一份备课素材包。"""
    terms = build_terms(topic)
    all_docs, tagged = [], []
    for cat, docs in docs_by_cat.items():
        for d in docs:
            item = dict(d)
            item["_cat"] = cat
            item["_dom"] = domain_weight(item.get("url", ""))
            item["_rel"] = relevance_score(item, terms)
            item["_fresh"] = freshness_score(item.get("date", ""))
            item["_final"] = round(0.45 * item["_rel"] + 0.35 * item["_dom"] + 0.20 * item["_fresh"], 4)
            tagged.append(item)

    total_before = len(tagged)
    tagged = dedupe(tagged)          # 先去重
    tagged.sort(key=lambda x: -x["_final"])

    buckets = {}
    for item in tagged:
        b = buckets.setdefault(item["_cat"], [])
        if len(b) < keep:
            b.append(item)

    # 极速版字段缺失，要在返回里显式标出来，别让前端猜
    missing = [f for f, owners in FIELD_OWNER.items() if tier not in owners]
    return {
        "topic": topic,
        "tier": tier,
        "tier_cn": TIER_CN.get(tier, tier),
        "missing_fields": missing,
        "total_before": total_before,
        "total_after": len(tagged),
        "buckets": buckets,
    }


def search_material(topic: str, stage: str, subject: str, freshness: str,
                    secret_id: str, secret_key: str, tier: str = None,
                    delay: float = 0.15) -> dict:
    """主流程：探档位 → 四路检索 → 聚合。"""
    started = time.time()
    tier = tier or probe_tier(secret_id, secret_key)

    docs_by_cat, sent_params, errors = {}, [], []
    for item in build_queries(topic, stage, subject):
        params = {"Query": item["q"], "Cnt": 10}
        if freshness:
            params["Freshness"] = freshness
        params = trim_params(params, tier)
        sent_params.append({"cat": item["cat"], "params": params})
        try:
            resp = call_api(params, secret_id, secret_key)
            docs_by_cat[item["cat"]] = parse_pages(resp.get("Pages"))
        except YuanbaoError as e:
            errors.append({"cat": item["cat"], "err": str(e)})
            docs_by_cat[item["cat"]] = []
        time.sleep(delay)   # 温和限速，别把 QPS 打满

    pack = aggregate(docs_by_cat, topic, tier)
    pack["elapsed"] = round(time.time() - started, 2)
    pack["calls"] = len(sent_params)
    pack["sent_params"] = sent_params
    pack["errors"] = errors
    return pack


# ---------------------------------------------------------------- Markdown 导出
def to_markdown(pack: dict) -> str:
    L = []
    L.append("# 备课素材包：%s\n" % pack["topic"])
    L.append("> 检索来源：腾讯云元宝搜索（%s）｜共 %d 路检索，命中 %d 条，去重后 %d 条｜耗时 %.2fs\n"
             % (pack.get("tier_cn", ""), pack.get("calls", 0), pack.get("total_before", 0),
                pack.get("total_after", 0), pack.get("elapsed", 0)))
    order = ["教材依据", "考点解析", "实践活动", "延伸拓展"]
    cats = [c for c in order if c in pack["buckets"]] + \
           [c for c in pack["buckets"] if c not in order]
    for cat in cats:
        L.append("\n## %s\n" % cat)
        for i, d in enumerate(pack["buckets"][cat], 1):
            L.append("### %d. %s" % (i, d.get("title", "（无标题）")))
            meta = []
            if d.get("site"):
                meta.append(d["site"])
            if d.get("date"):
                meta.append(d["date"])
            meta.append("综合分 %.2f" % d.get("_final", 0))
            L.append("- %s" % "｜".join(meta))
            L.append("- 出处：%s" % d.get("url", ""))
            body = d.get("content") or d.get("passage") or ""
            if body:
                L.append("- 摘录：%s" % body.strip()[:300])
            L.append("")
    L.append("\n---\n*本素材包由「元宝搜索备课助手」自动生成，引用内容版权归原站点所有，使用前请自行核对。*")
    return "\n".join(L)


# -------------------------------------------------------------------- 命令行
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="元宝搜索备课素材包生成器（命令行版）")
    ap.add_argument("topic", help="课题，例如：算法的初步认识")
    ap.add_argument("--stage", default="小学")
    ap.add_argument("--subject", default="信息科技")
    ap.add_argument("--freshness", default="")
    ap.add_argument("--tier", default=None, help="强制指定档位：lite/standard/flagship")
    ap.add_argument("-o", "--out", default="")
    a = ap.parse_args()

    sid = os.environ.get("YB_SECRET_ID", "")
    skey = os.environ.get("YB_SECRET_KEY", "")
    pack = search_material(a.topic, a.stage, a.subject, a.freshness, sid, skey, a.tier)
    md = to_markdown(pack)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
        print("已写入 %s" % a.out)
    else:
        print(md)
