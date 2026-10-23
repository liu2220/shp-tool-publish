#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SHP 工具交付包打包脚本。

统一产出 downloads/packages 下的发布包，各包的排除规则就地维护。
用法：python3 pack_release.py [mobile|web|uos-source|uos-portable|all]
"""
import fnmatch
import os
import sys
import tarfile
import zipfile

WORKSPACE = "/workspace"
OUT = os.path.join(WORKSPACE, "downloads", "packages")

# 源目录 -> (输出 zip 名, 排除的路径前缀片段 / 后缀)
JOBS = {
    "mobile": {
        "src": "SHP工具_手机版",
        "zip": "SHP工具_手机版_v1.27.zip",
        # android 工程（APK 源码）与 .git 不打进 PWA 包；vendor 必须带（内置 LibreDWG/SheetJS）
        "exclude_dirs": [".git", "android", "__pycache__", ".gradle", "node_modules"],
        # tileserver.py 保留：用户挂载离线瓦片包时需要（也是 E2E 依赖）
        "exclude_files": [],
    },
    "web": {
        "src": "SHP工具_网页版",
        "zip": "SHP工具_网页版.zip",
        # tools 是构建期的依赖轮子目录（~30M），运行期不需要
        "exclude_dirs": [".git", "__pycache__", "tools", "node_modules"],
        "exclude_files": [],
    },
    "uos-source": {
        "src": "SHP工具_UOS版",
        "zip": "SHP工具_UOS版_源码.zip",
        "fmt": "zip",
        # tools/libredwg 是 x86-64 二进制（30M），龙芯/ARM 用不了且会报格式错误；
        # vendor 必须完整打包（openpyxl 等纯 Python 内置依赖）
        "exclude_dirs": [".git", "__pycache__", ".pytest_cache", "tools",
                         "node_modules", ".idea"],
        "exclude_files": [".DS_Store"],
    },
    "uos-portable": {
        "src": "SHP工具_UOS版_绿色版",
        "zip": "SHP工具_UOS版_绿色版.tar.gz",
        "fmt": "tar.gz",
        # python/ 为 x86-64 自包含解释器（547M），必须保留——龙芯/ARM 主机
        # 由 run.sh 自动改走系统 python3 + vendor 纯 Python 依赖；
        # tools/libredwg 为 x86-64 二进制（龙芯用不了，会报格式错误）；
        # core.* 为崩溃核心转储（core.129831 有 35M），绝不随包分发。
        "exclude_dirs": [".git", "__pycache__", ".pytest_cache", "tools",
                         "node_modules", ".idea"],
        "exclude_files": [".DS_Store"],
        # terminfo 断链（python/lib/terminfo -> 不存在的 share/terminfo）
        # 在任意层级；core/core.<pid> 崩溃转储只可能出现在包根顶层，
        # 绝不能按 basename 全局匹配（会误杀 vendor/**/core.py，v41 踩坑）
        "exclude_globs": ["terminfo"],
        "exclude_globs_top": ["core", "core.[0-9]*"],
    },
}


def should_skip(root_rel, parts, job):
    """按路径前缀排除，而不是按目录名匹配。

    这里踩过坑：早期按「目录名 == tools」排除，把 vendor/ezdxf/tools/
    一起删了（ezdxf 的核心子包），本机测试全通过、发给用户解压才崩。
    现在只认相对于 src 的顶层前缀（如 "tools"），vendor/**/tools 不受影响。
    """
    src = os.path.join(WORKSPACE, job["src"])
    rel = os.path.relpath(root_rel, src)
    if rel in (".", ""):
        return False
    for ex in job["exclude_dirs"]:
        if rel == ex or rel.startswith(ex + os.sep):
            return True
    return False


def _in_excluded_dir(rel_root, excl):
    """判断相对路径是否落在顶层排除目录内（供 dirs 剪枝使用）。"""
    return any(rel_root == e or rel_root.startswith(e + os.sep) for e in excl)


def _verify_uos_source(src):
    """打包前校验内置包完整（防止再次发出残缺包），返回错误列表。"""
    import subprocess
    import sys
    pkgdir = os.path.join(src, "vendor")
    check = (
        "import sys, os, importlib\n"
        "pkgdir, pkg = sys.argv[1], sys.argv[2]\n"
        "sys.path.insert(0, pkgdir)\n"
        "if __name__ == '__main__':\n"
        "    import io\n"
        "    _o = sys.stdout\n"
        "    sys.stdout = io.StringIO()\n"
        "    outs = []\n"
        "    for dp, ds, fs in os.walk(os.path.join(pkgdir, pkg)):\n"
        "        if '__pycache__' in dp: continue\n"
        "        mod = os.path.relpath(dp, pkgdir).replace(os.sep, '.')\n"
        "        for f in fs:\n"
        "            if not f.endswith('.py'): continue\n"
        "            nm = f[:-3]\n"
        "            full = mod + ('' if nm == '__init__' else '.' + nm)\n"
        "            try: importlib.import_module(full)\n"
        "            except Exception as e:\n"
        "                if \"No module named 'railroad'\" in str(e): continue\n"
        "                OPTIONAL = ('matplotlib', 'PIL', 'pillow')\n"
        "                if isinstance(e, ModuleNotFoundError) and (e.name in OPTIONAL or (e.name or '').startswith('matplotlib') or (e.name or '').startswith('PIL')): continue\n"
        "                if \"No module named 'matplotlib'\" in str(e) or \"No module named 'PIL'\" in str(e): continue\n"
        "                outs.append(full + ' || ' + str(e)[:70])\n"
        "    sys.stdout = _o\n"
        "    sys.stderr.write('\\n'.join(outs))\n"
    )
    bad = []
    for pkg in ("openpyxl", "et_xmlfile", "ezdxf", "pyparsing"):
        if not os.path.isdir(os.path.join(pkgdir, pkg)):
            bad.append("%s: 目录缺失" % pkg)
            continue
        r = subprocess.run([sys.executable, "-c", check, pkgdir, pkg],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=180)
        bad += [l for l in (r.stderr or "").splitlines() if l.strip()]
    return bad


def _skip_file(fname, job, rel_root="."):
    """文件级排除：精确名单 + glob + .pyc。

    glob 分两类（v41 教训：曾用 basename glob "core.*" 排除崩溃转储，
    结果把 vendor/openpyxl/packaging/core.py、pyparsing/core.py 等正常
    模块一并误杀——openpyxl 16 处、pyparsing 7 处导入失败，ezdxf 因依赖
    pyparsing.core 连带 182 处失败）：
      exclude_globs     —— 任意层级生效（仅放不会误伤正常文件名的模式）
      exclude_globs_top —— 仅 src 顶层生效（崩溃转储只应出现在包根目录）
    """
    for pat in job.get("exclude_globs", []):
        if fnmatch.fnmatch(fname, pat):
            return True
    if rel_root in (".", ""):
        for pat in job.get("exclude_globs_top", []):
            if fnmatch.fnmatch(fname, pat):
                return True
    return (fname in job.get("exclude_files", [])
            or fname.endswith(".pyc"))


def _fsize(full):
    """文件大小；断链符号链接等取 lstat 兜底（terminfo -> 不存在的
    share/terminfo，getsize 会抛 FileNotFoundError）。"""
    try:
        return os.path.getsize(full)
    except OSError:
        try:
            return os.lstat(full).st_size
        except OSError:
            return 0


def _prune_dirs(root, dirs, rel_root, job):
    """原地剪枝 dirs：顶层排除目录 + __pycache__/.egg-info"""
    keep = []
    for d in dirs:
        sub = d if rel_root == "." else os.path.join(rel_root, d)
        if _in_excluded_dir(sub, job["exclude_dirs"]):
            continue
        if d == "__pycache__" or d.endswith(".egg-info"):
            continue
        keep.append(d)
    dirs[:] = keep


def pack(name):
    job = JOBS[name]
    src = os.path.join(WORKSPACE, job["src"])
    dst = os.path.join(OUT, job["zip"])
    if name in ("uos-source", "uos-portable"):
        errs = _verify_uos_source(src)
        if errs:
            print("  ✘ %s：内置包校验失败，拒绝打包（会发出残缺包）" % name)
            for e in errs[:6]:
                print("      " + e)
            return 0
    n = 0
    total = 0
    if job.get("fmt") == "tar.gz":
        with tarfile.open(dst, "w:gz", compresslevel=6) as t:
            for root, dirs, files in os.walk(src):
                rel_root = os.path.relpath(root, src)
                if rel_root != "." and _in_excluded_dir(
                        rel_root, job["exclude_dirs"]):
                    dirs[:] = []
                    continue
                _prune_dirs(root, dirs, rel_root, job)
                for f in files:
                    if _skip_file(f, job, rel_root):
                        continue
                    full = os.path.join(root, f)
                    arc = os.path.join(job["src"], os.path.relpath(full, src))
                    t.add(full, arc, recursive=False)
                    n += 1
                    total += _fsize(full)
    else:
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            for root, dirs, files in os.walk(src):
                rel_root = os.path.relpath(root, src)
                if rel_root != "." and _in_excluded_dir(
                        rel_root, job["exclude_dirs"]):
                    dirs[:] = []
                    continue
                _prune_dirs(root, dirs, rel_root, job)
                for f in files:
                    if _skip_file(f, job, rel_root):
                        continue
                    full = os.path.join(root, f)
                    arc = os.path.join(job["src"], os.path.relpath(full, src))
                    z.write(full, arc)
                    n += 1
                    total += _fsize(full)
    size = os.path.getsize(dst)
    print("  %s -> %s  （%d 个文件，源 %.1f MB，包 %.1f MB）"
          % (name, job["zip"], n, total / 1048576.0, size / 1048576.0))
    return n


def main():
    os.makedirs(OUT, exist_ok=True)
    targets = sys.argv[1:] or ["mobile", "web", "uos-source", "uos-portable"]
    if "all" in targets:
        targets = list(JOBS)
    for t in targets:
        if t not in JOBS:
            print("  未知目标：%s" % t)
            return 1
    print("打包交付物：")
    for t in targets:
        pack(t)
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
