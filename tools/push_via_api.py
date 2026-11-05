"""push_via_api.py — 用 GitHub API 推送（当 github.com:443 连不上时）

适用场景：`git push` 报 `Failed to connect to github.com:443`，
但 `api.github.com` 能通（国内网络很常见）。

它按本地历史**逐 commit** 重放到远端：
  1. `git rev-list --reverse HEAD` 拿到全部提交；
  2. 每个提交用 `git ls-tree -r` 得到文件表，与父提交比较，只上传变化的 blob；
  3. 建 tree（`base_tree` = 上一提交的 tree，删除项用 `sha: null`）；
  4. 建 commit（parents = 远端上一提交）；
  5. 最后把 ref 指过去。

Token 来源（按优先级）：
  JEYI_GITHUB_TOKEN / GITHUB_TOKEN 环境变量 → `git credential fill` 缓存的 github 凭据。

用法：
    python tools/push_via_api.py --repo y50008/jeyi --dry
    python tools/push_via_api.py --repo y50008/jeyi
    python tools/push_via_api.py --repo y50008/jeyi --branch main
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.github.com"


def sh(*args, binary=False):
    out = subprocess.run(args, cwd=ROOT, capture_output=True, check=True).stdout
    return out if binary else out.decode("utf-8", "replace")


def token() -> str:
    for key in ("JEYI_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(key):
            return os.environ[key].strip()
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        ).stdout
        for line in out.splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception:
        pass
    raise SystemExit("拿不到 token：设 JEYI_GITHUB_TOKEN，或先 git push 一次让凭据进管理器")


class GH:
    def __init__(self, tok: str):
        self.tok = tok

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": f"token {self.tok}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "jeyi-push",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise SystemExit(f"{method} {path} -> {e.code}: {detail}")


def ls_tree(commit: str) -> dict:
    """{path: (mode, type, blob_sha)}"""
    out = sh("git", "ls-tree", "-r", "-z", commit)
    files = {}
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        mode, typ, sha = meta.split()
        files[path] = (mode, typ, sha)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--since", default=None,
                    help="只推这个本地提交之后的新提交（接在远端当前 HEAD 上）")
    a = ap.parse_args()
    owner, name = a.repo.split("/", 1)

    if a.since:
        a.since = sh("git", "rev-parse", "--verify", a.since).strip()
    commits = sh("git", "rev-list", "--reverse", "HEAD").split()
    if a.since:
        full = sh("git", "rev-list", "HEAD").split()
        if a.since in full:
            commits = commits[commits.index(a.since) + 1:]
        else:
            raise SystemExit(f"--since {a.since} 不在本地历史里")
    print(f"本地 {len(commits)} 个提交，目标 {a.repo}:{a.branch}")
    if a.dry:
        prev = {}
        for c in commits:
            cur = ls_tree(c)
            changed = [p for p in cur if prev.get(p) != cur[p]]
            gone = [p for p in prev if p not in cur]
            subj = sh("git", "log", "-1", "--format=%s", c).strip()
            print(f"  {c[:8]} +{len(changed):3} -{len(gone):3}  {subj[:50]}")
            prev = cur
        print("（dry-run，没有推送）")
        return 0

    gh = GH(token())
    me = gh.call("GET", "/user")
    print(f"身份：{me['login']}")

    blob_cache: dict[str, str] = {}          # 本地 blob sha → 远端 blob sha
    prev_local, prev_remote, prev_tree = {}, None, None
    if a.since:
        # 接在远端 HEAD 上：把 since 提交的树当作"上一状态"
        prev_local = ls_tree(a.since)
        head = gh.call("GET", f"/repos/{owner}/{name}/git/ref/heads/{a.branch}")
        prev_remote = head["object"]["sha"]
        prev_tree = gh.call("GET", f"/repos/{owner}/{name}/git/commits/{prev_remote}")["tree"]["sha"]
        print(f"接在远端 {prev_remote[:8]} 之后，追加 {len(commits)} 个提交")
    for idx, c in enumerate(commits, 1):
        cur = ls_tree(c)
        entries = []
        for path, (mode, typ, sha) in cur.items():
            if prev_local.get(path) == (mode, typ, sha) and prev_tree:
                continue                       # 没变，靠 base_tree 继承
            if sha not in blob_cache:
                raw = sh("git", "cat-file", "blob", sha, binary=True)
                res = gh.call("POST", f"/repos/{owner}/{name}/git/blobs", {
                    "content": base64.b64encode(raw).decode("ascii"),
                    "encoding": "base64",
                })
                blob_cache[sha] = res["sha"]
            entries.append({"path": path, "mode": mode, "type": typ,
                            "sha": blob_cache[sha]})
        if prev_tree:
            for path in prev_local:
                if path not in cur:
                    entries.append({"path": path, "mode": "100644",
                                    "type": "blob", "sha": None})
        tree_body = {"tree": entries}
        if prev_tree:
            tree_body["base_tree"] = prev_tree
        tree = gh.call("POST", f"/repos/{owner}/{name}/git/trees", tree_body)

        msg = sh("git", "log", "-1", "--format=%s%n%n%b", c).strip() or "update"
        payload = {"message": msg, "tree": tree["sha"]}
        if prev_remote:
            payload["parents"] = [prev_remote]
        commit = gh.call("POST", f"/repos/{owner}/{name}/git/commits", payload)
        prev_remote, prev_tree, prev_local = commit["sha"], tree["sha"], cur
        print(f"  [{idx}/{len(commits)}] {c[:8]} -> {commit['sha'][:8]}  "
              f"+{len(entries)} 项")

    try:
        gh.call("POST", f"/repos/{owner}/{name}/git/refs",
                {"ref": f"refs/heads/{a.branch}", "sha": prev_remote})
        print(f"已建 {a.branch} -> {prev_remote[:8]}")
    except SystemExit:
        gh.call("PATCH", f"/repos/{owner}/{name}/git/refs/heads/{a.branch}",
                {"sha": prev_remote, "force": True})
        print(f"已更新 {a.branch} -> {prev_remote[:8]}")
    try:
        gh.call("PATCH", f"/repos/{owner}/{name}", {"default_branch": a.branch})
    except SystemExit:
        pass
    print(f"完成：https://github.com/{a.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
