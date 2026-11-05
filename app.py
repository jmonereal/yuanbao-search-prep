# -*- coding: utf-8 -*-
"""
元宝搜索 · 备课素材包生成器（本地网页版）

零第三方依赖：内置 http.server 提供服务，标准库直连元宝搜索。

启动：
    python app.py
然后浏览器打开 http://127.0.0.1:8848

密钥：
    方式一  环境变量  YB_SECRET_ID / YB_SECRET_KEY
    方式二  同目录建 config.json：{"secret_id": "...", "secret_key": "..."}
未配置密钥时自动进入演示模式，界面会明确标注。
"""

import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yb_search as yb

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("YB_PORT", "8848"))


def load_keys():
    sid = os.environ.get("YB_SECRET_ID", "").strip()
    skey = os.environ.get("YB_SECRET_KEY", "").strip()
    if sid and skey:
        return sid, skey
    cfg = os.path.join(BASE, "config.json")
    if os.path.exists(cfg):
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                c = json.load(f)
            return c.get("secret_id", "").strip(), c.get("secret_key", "").strip()
        except Exception:
            pass
    return "", ""


class Handler(BaseHTTPRequestHandler):
    server_version = "YuanbaoPrep/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ------------------------------------------------------------ 工具方法
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---------------------------------------------------------------- 路由
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            fp = os.path.join(BASE, "static", "index.html")
            with open(fp, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/config":
            sid, skey = load_keys()
            self._json({"has_key": bool(sid and skey)})
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        path = self.path.split("?")[0]
        data = self._read_json()

        if path == "/api/search":
            self._handle_search(data)
        elif path == "/api/export":
            try:
                md = yb.to_markdown(data.get("pack") or {})
                self._send(200, md.encode("utf-8"), "text/markdown; charset=utf-8")
            except Exception as e:
                self._json({"error": "导出失败：%s" % e}, 500)
        else:
            self._send(404, b'{"error":"not found"}')

    # ------------------------------------------------------------ 业务处理
    def _handle_search(self, data):
        topic = (data.get("topic") or "").strip()
        stage = (data.get("stage") or "小学").strip()
        subject = (data.get("subject") or "信息科技").strip()
        freshness = (data.get("freshness") or "").strip()

        if not topic:
            self._json({"error": "请先填写教学课题"}, 400)
            return
        if len(topic) > 40:
            self._json({"error": "课题过长，请控制在 40 字以内"}, 400)
            return

        sid, skey = load_keys()
        if not (sid and skey):
            import mock_data
            self._json(mock_data.demo_pack(topic))
            return

        try:
            pack = yb.search_material(topic, stage, subject, freshness, sid, skey)
        except yb.YuanbaoError as e:
            self._json({"error": "调用失败：%s" % e, "hint": self._hint(str(e))}, 502)
            return
        except Exception as e:
            self._json({"error": "内部错误：%s" % e}, 500)
            return

        self._json(pack)

    @staticmethod
    def _hint(msg: str) -> str:
        m = msg.lower()
        if "resourceunavailable" in m or "resourcenotfound" in m:
            return "服务未开通或额度已用尽，请到元宝搜索控制台确认开通状态与免费额度。"
        if "invalidparameter" in m:
            return "多为入参或档位不匹配：极速版不支持 Mode / Cnt / Industry，本程序已自动裁剪，如仍报错请检查 Query 是否为空。"
        if "authfailure" in m or "signature" in m:
            return "签名校验失败，请核对 SecretId / SecretKey 是否成对、本机时间是否准确（误差需小于 5 分钟）。"
        if "requestlimitexceeded" in m:
            return "触发 QPS 限流，请降低请求频率或稍后重试。"
        return "请对照官方错误码文档排查。"


def main():
    sid, skey = load_keys()
    url = "http://127.0.0.1:%d" % PORT
    print("=" * 56)
    print(" 元宝搜索 · 备课素材包生成器")
    print(" 地址：%s" % url)
    if sid and skey:
        print(" 模式：真实调用（密钥已加载，SecretId 尾号 %s）" % sid[-6:])
    else:
        print(" 模式：演示数据（未配置密钥）")
        print("       请在 config.json 填入 secret_id / secret_key 后重启")
    print(" 按 Ctrl+C 停止")
    print("=" * 56)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
