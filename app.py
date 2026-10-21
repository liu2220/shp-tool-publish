"""SHP 工具交付包下载服务。

网关 WAF 拦截 .zip/.tar.gz 扩展名的静态直链，故改用无扩展名路由
流式返回文件，并通过 Content-Disposition 附带中文原始文件名。
"""
import glob
import os
import re

from flask import Flask, send_file, send_from_directory

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(BASE, "packages")


def _latest(pattern):
    """packages 目录下按文件名中的版本号（v1.9 > v1.10 按段比较）取最新文件。
    发布新版本只需把带版本号的文件放进 packages，路由无需改动。"""
    def ver_key(path):
        m = re.search(r"v(\d+(?:\.\d+)*)", os.path.basename(path))
        return [int(x) for x in m.group(1).split(".")] if m else [0]
    files = glob.glob(os.path.join(PKG, pattern))
    return max(files, key=ver_key) if files else None


@app.route("/")
def index():
    resp = send_from_directory(BASE, "index.html")
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.route("/img/<path:name>")
def img(name):
    return send_from_directory(os.path.join(BASE, "img"), name)


def _send(pkg_name, download_name):
    p = os.path.join(PKG, pkg_name)
    if not os.path.exists(p):
        return "package not found: %s" % pkg_name, 404
    resp = send_file(
        p,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/octet-stream",
        conditional=False,   # 不做 304 协商：版本升级后浏览器必须拿全新响应
    )
    # 防浏览器缓存旧版本包/页面（v1.18 曾出现客户端仍下载到 v1.17 的缓存假象）
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.route("/dl/uos-portable")
def uos_portable():
    return _send("SHP工具_UOS版_绿色版.tar.gz", "SHP工具_UOS版_绿色版.tar.gz")


@app.route("/dl/win7-portable")
def win7_portable():
    return _send("SHP_tool_Win7_portable.zip", "SHP工具_Win7版_绿色版.zip")


@app.route("/dl/uos-source")
def uos_source():
    return _send("SHP工具_UOS版_源码.zip", "SHP工具_UOS版_源码.zip")


@app.route("/dl/mobile")
def mobile():
    f = _latest("SHP工具_手机版_v*.zip")
    if f:
        name = os.path.basename(f)
        return _send(name, name)          # 下载名 = 文件名（含版本号）
    return _send("SHP工具_手机版_v1.zip", "SHP工具_手机版_PWA版.zip")


@app.route("/dl/mobile-apk")
def mobile_apk():
    f = _latest("SHP工具_手机版_v*.apk")
    if f:
        name = os.path.basename(f)
        return _send(name, name)          # 下载名 = APP名+版本，如 SHP工具_手机版_v1.9.apk
    return _send("SHP工具_手机版_v1.apk", "SHP工具_手机版.apk")


@app.route("/dl/web")
def web():
    return _send("SHP工具_网页版.zip", "SHP工具_网页版.zip")


@app.route("/dl/changelog")
def changelog():
    return send_file(os.path.join(BASE, "更新日志.md"), as_attachment=True,
                     download_name="SHP工具_更新日志.md",
                     mimetype="text/markdown")


TILE_LEVELS = ["z%d" % i for i in range(10, 19)]


@app.route("/dl/tiles/<level>")
def tiles(level):
    if level not in TILE_LEVELS:
        return "unknown tile level: %s" % level, 404
    return _send(
        os.path.join("tiles", "义乌影像_%s.zip" % level),
        "义乌影像瓦片_%s.zip" % level,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)), threaded=True)
