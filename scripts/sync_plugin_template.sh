#!/usr/bin/env bash
# 把 examples/tenko-plugin-example 同步到 GitHub 模板仓库 g1331/tenko-plugin-template。
# 示例改动推到 main 之后运行；需要已登录、能推送该仓库的 gh。
set -euo pipefail

repo="${TEMPLATE_REPO:-g1331/tenko-plugin-template}"
root="$(git rev-parse --show-toplevel)"
example="$root/examples/tenko-plugin-example"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

gh repo clone "$repo" "$work/template" -- --quiet
cd "$work/template"
git rm -rq --ignore-unmatch .
(cd "$example" && git ls-files -z .) | (cd "$example" && xargs -0 tar cf -) | tar xf -
git add -A
if git diff --cached --quiet; then
    echo "模板仓库已是最新"
    exit 0
fi
git commit -qm "sync from tenko@$(git -C "$root" rev-parse --short HEAD)"
git push -q origin HEAD
echo "已同步到 $repo"
