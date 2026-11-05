# -*- coding: utf-8 -*-
"""
元宝搜索 · 备课素材包生成器（单文件精简版）

零第三方依赖，只用 Python 标准库。复制本文件到本地，在 config.json 里填好
secret_id / secret_key（或设置环境变量 YB_SECRET_ID / YB_SECRET_KEY），执行：

    python yuanbao_prep.py 算法的初步认识

即可在命令行看到四类备课素材，并生成 素材包.md。

作者：邓家龙   许可：MIT
"""

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

HOST, SERVICE, ACTION, VERSION = "wsa.tencentcloudapi.com", "wsa", "SearchPro", "2025-05-08"

# 档位能力表：极速版不支持这些入参，旗舰版才多返回权威度与子链
PARAM_OWNER = {"Mode": ("standard", "flagship"), "Cnt": ("standard", "flagship"),
               "Industry": ("standard", "flagship"), "Deeplinks": ("flagship",)}
FIELD_OWNER = {"content": ("standard", "flagship"), "score": ("standard", "flagship"),
               "pics": ("standard", "flagship"), "authority_level": ("flagship",),
               "deeplinks": ("flagship",)}

# 这些错误码属于瞬时抖动，退避重试有效；其余（鉴权、参数、欠费）重试无意义
RETRYABLE = ("ResourceNotFound", "InternalError", "RequestLimitExceeded")


# ------------------------------------------------------------------ 1. 签名
def sign(sid: str, skey: str, payload: str, ts: int):
    """TC3-HMAC-SHA256：三层派生密钥 + 两轮摘要。"""
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"
    ch = "content-type:%s\nhost:%s\nx-tc-action:%s\n" % (ct, HOST, ACTION.lower())
    sh = "content-type;host;x-tc-action"
    cr = "\n".join(["POST", "/", "", ch, sh,
                    hashlib.sha256(payload.encode()).hexdigest()])
    scope = "%s/%s/tc3_request" % (date, SERVICE)
    sts = "\n".join(["TC3-HMAC-SHA256", str(ts), scope,
                     hashlib.sha256(cr.encode()).hexdigest()])

    def h(k, m):
        return hmac.new(k, m.encode(), hashlib.sha256).digest()

    k = h(("TC3" + skey).encode(), date)
    k = h(k, SERVICE)
    k = h(k, "tc3_request")
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return ("TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
            % (sid, scope, sh, sig)), date


# ----------------------------------------------------------------- 2. 调用
def call(params: dict, sid: str, skey: str, retry: int = 2) -> dict:
    """调 SearchPro。偶发 ResourceNotFound 是瞬时抖动，退避重试即可。"""
    last = None
    for attempt in range(retry + 1):
        try:
            return _call_once(params, sid, skey)
        except RuntimeError as e:
            last = e
            code = str(e).split(":")[0]
            if code not in RETRYABLE or attempt == retry:
                raise
            time.sleep(1.2 * (attempt + 1))
    raise last


def _call_once(params: dict, sid: str, skey: str) -> dict:
    payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
    ts = int(time.time())
    auth, _ = sign(sid, skey, payload, ts)
    req = urllib.request.Request(
        "https://" + HOST, data=payload.encode(), method="POST",
        headers={"Authorization": auth, "Content-Type": "application/json; charset=utf-8",
                 "Host": HOST, "X-TC-Action": ACTION, "X-TC-Version": VERSION,
                 "X-TC-Timestamp": str(ts)})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read().decode())
    resp = body.get("Response", {})
    if "Error" in resp:
        raise RuntimeError("%s: %s" % (resp["Error"].get("Code"), resp["Error"].get("Message")))
    return resp


def probe_tier(sid: str, skey: str) -> str:
    """先探档位再裁参数：极速版传 Cnt 会直接 InvalidParameter。"""
    tier = (call({"Query": "元宝搜索"}, sid, skey).get("Version") or "lite").lower()
    return tier if tier in ("lite", "standard", "flagship") else "lite"


def trim(params: dict, tier: str) -> dict:
    ok = {"Query", "Site", "Freshness"}
    ok |= {k for k, v in PARAM_OWNER.items() if tier in v}
    return {k: v for k, v in params.items() if k in ok and v not in (None, "")}


# ------------------------------------------------------- 3. 解析、去重、打分
def parse(pages) -> list:
    """Pages 是字符串数组，每个元素要再 json 一次。"""
    out = []
    for p in pages or []:
        try:
            o = json.loads(p) if isinstance(p, str) else p
            if isinstance(o, dict):
                out.append(o)
        except ValueError:
            pass
    return out


def norm_url(u):
    p = urlparse(u or "")
    return (p.netloc.lower().replace("www.", "") + (p.path or "/")).rstrip("/")


DOMAIN = [("gov.cn", 1.0), ("edu.cn", .96), ("pep.com.cn", .96), ("smartedu.cn", .96),
          ("cnki.net", .92), ("xinhuanet.com", .90), ("people.com.cn", .90),
          ("baike.baidu.com", .72), ("zhihu.com", .62), ("csdn.net", .62)]


def dom_score(u):
    h = urlparse(u or "").netloc.lower()
    for k, w in DOMAIN:
        if k in h:
            return w
    return .50


def fresh_score(s):
    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s or "")
    if not m:
        return .6
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return .6
    days = max(0, (datetime.now() - d).days)
    return 1.0 if days <= 30 else .9 if days <= 180 else .8 if days <= 365 else .7


def rel_score(doc, terms):
    if isinstance(doc.get("score"), (int, float)) and doc["score"] > 0:
        return float(doc["score"])          # 标准版及以上直接给分
    text = (doc.get("title", "") + doc.get("passage", "")).lower()
    hit = sum(1 for t in terms if t in text)
    return min(1.0, .35 + .65 * hit / max(1, len(terms)))   # 极速版退回词面命中


# ------------------------------------------------------------------ 4. 编排
QUERIES = [("教材依据", "%s %s %s 教材 课标 知识点 讲解"),
           ("考点解析", "%s %s %s 易错点 学业质量 评价 解析"),
           ("实践活动", "%s %s %s 上机 实践 活动设计 课例"),
           ("延伸拓展", "%s 最新 研究 进展 应用 案例")]


def build(topic, stage="小学", subject="信息科技", sid="", skey=""):
    tier = probe_tier(sid, skey)
    terms = [t for t in re.split(r"[\s,，]+", topic) if t]
    print("档位：%s ｜ 缺失字段：%s" % (tier, [f for f, v in FIELD_OWNER.items() if tier not in v]))

    buckets, seen = {}, set()
    for cat, tpl in QUERIES:
        q = tpl % ((topic,) if cat == "延伸拓展" else (stage, subject, topic))
        params = trim({"Query": q, "Cnt": 10}, tier)
        print("· %s → %s" % (cat, json.dumps(params, ensure_ascii=False)))
        try:
            docs = parse(call(params, sid, skey).get("Pages"))
        except Exception as e:
            print("  调用失败：%s" % e)
            docs = []
        kept = []
        for d in docs:
            u, t = norm_url(d.get("url")), re.sub(r"\W+", "", d.get("title", ""))
            if (u and u in seen) or (t and t in seen):
                continue
            seen |= {u, t}
            d["_s"] = round(.45 * rel_score(d, terms) + .35 * dom_score(u) + .2 * fresh_score(d.get("date")), 3)
            kept.append(d)
        buckets[cat] = sorted(kept, key=lambda x: -x["_s"])[:4]
        time.sleep(.2)                      # 温和限速
    return tier, buckets


def to_md(topic, tier, buckets):
    L = ["# 备课素材包：%s\n" % topic, "> 来源：腾讯云元宝搜索（%s）\n" % tier]
    for cat, docs in buckets.items():
        L.append("\n## %s\n" % cat)
        for i, d in enumerate(docs, 1):
            L.append("### %d. %s" % (i, d.get("title", "")))
            L.append("- %s｜%s｜综合分 %.2f" % (d.get("site", ""), d.get("date", ""), d.get("_s", 0)))
            L.append("- 出处：%s" % d.get("url", ""))
            body = (d.get("content") or d.get("passage") or "").strip()
            if body:
                L.append("- 摘录：%s" % body[:300])
            L.append("")
    L.append("\n---\n*引用内容版权归原站点所有，使用前请自行核对。*")
    return "\n".join(L)


def load_keys():
    """密钥优先级：环境变量 > 同目录 config.json。"""
    sid = os.environ.get("YB_SECRET_ID", "").strip()
    skey = os.environ.get("YB_SECRET_KEY", "").strip()
    if not (sid and skey):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                c = json.load(f)
            sid = (c.get("secret_id") or "").strip()
            skey = (c.get("secret_key") or "").strip()
    return sid, skey


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "算法的初步认识"
    stage = sys.argv[2] if len(sys.argv) > 2 else "小学"
    subject = sys.argv[3] if len(sys.argv) > 3 else "信息科技"
    SID, SKEY = load_keys()
    if not (SID and SKEY):
        sys.exit("请先在 config.json 填 secret_id / secret_key，或设置环境变量 YB_SECRET_ID / YB_SECRET_KEY")
    tier, buckets = build(topic, stage, subject, SID, SKEY)
    md = to_md(topic, tier, buckets)
    open("素材包.md", "w", encoding="utf-8").write(md)
    print("\n已生成 素材包.md（%d 字）" % len(md))
