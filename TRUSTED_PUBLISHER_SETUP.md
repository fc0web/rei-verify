# PyPI Trusted Publisher Setup — rei-verify

**目的**: 本 repo の `.github/workflows/publish.yml` が **PyPI 本番 publish + TestPyPI dry-run** を Trusted Publisher (OIDC) 経由で 実行できるようにする 5-step handoff。

**★ 重要**: 本 setup が 未完了の状態で `v*` tag を push すると workflow が fire するが **PyPI publish は fail** します。 setup 完了 verify 後に tag push すること。

**背景 (memory 忘れ対策)**: 過去 rei-automator-mcp arc で 「PyPI publish は 含めません、 次判断待ち」 と 明言した 直後に tag push で workflow を auto-fire させた 事故あり ([`project_rei_automator_mcp_v020a2_a3_arc_2026-08-18.md`](../../.claude/projects/C--Users-user-rei-aios/memory/project_rei_automator_mcp_v020a2_a3_arc_2026-08-18.md))。 本 doc は その 再発防止 の checklist。

---

## Step 1 — PyPI (本番) Trusted Publisher 登録

**URL**: https://pypi.org/manage/account/publishing/

**Pending Publisher** として 以下を 追加:

| field | value |
|---|---|
| PyPI Project Name | `rei-verify` |
| Owner | `fc0web` |
| Repository name | `rei-verify` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

★ 「Environment name」 は GitHub Actions 側の environment 名と **完全一致** 必須 (Step 3 で 作成)。

---

## Step 2 — TestPyPI (dry-run) Trusted Publisher 登録

**URL**: https://test.pypi.org/manage/account/publishing/

同上の 4 field + Environment name = `testpypi`。

TestPyPI は workflow_dispatch (手動実行) で 発火するので、 tag push 前に **本番 と 同じ pipeline** を dry-run 検証できる。

---

## Step 3 — GitHub Environment 作成

**URL**: https://github.com/fc0web/rei-verify/settings/environments

以下 2 environment を 作成:

- **`pypi`** — tag push で 発火する 本番 publish 用
- **`testpypi`** — workflow_dispatch で 発火する dry-run 用

任意で:
- deployment protection rule (approval reviewer 追加 で 追加 gate 化)
- environment secrets (Trusted Publisher OIDC 使う 場合は 不要、 secrets 保存禁止 が 本方式の 価値)

---

## Step 4 — TestPyPI dry-run 実行 (推奨、 tag push 前の safety net)

**URL**: https://github.com/fc0web/rei-verify/actions/workflows/publish.yml

「Run workflow」 button → branch=`master` → 「Run workflow」 で workflow_dispatch 発火。

期待:
- `build` job success (wheel + sdist 生成 + twine check PASS)
- `testpypi` job success (TestPyPI に v0.1.0a1 が publish される)

verify:
```bash
curl -s https://test.pypi.org/pypi/rei-verify/json | python -c "
import json, sys
d = json.load(sys.stdin)
print('latest:', d['info']['version'])
"
# → latest: 0.1.0a1
```

install test:
```bash
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ rei-verify
python -c "from rei_verify import Verdict; print(Verdict.CONFIRMED)"
# → confirmed
```

**dry-run が fail した場合 の 診断**:
- `invalid-publisher` error → Step 1/2 の 4 field mismatch or Step 3 environment 名 mismatch
- `build` job fail → `python -m build` local で 再現 verify
- 401 unauthorized → Trusted Publisher 「Pending」 のまま (PyPI 側で 登録完了確認)

---

## Step 5 — v0.1.0a1 tag push で PyPI 本番 publish

Step 1-4 全 完了 verify 後、 藤本さん judgment で:

```bash
cd C:/Users/user/rei-verify
git tag -a v0.1.0a1 -m "v0.1.0a1 — skeleton + 4 refutation tools + integration demo"
git push origin v0.1.0a1
```

期待:
- workflow の `build` job success
- `pypi` job success (PyPI 本番 に v0.1.0a1 publish、 Trusted Publisher OIDC + Sigstore attestation)
- workflow duration ~1-2 min

verify:
```bash
python -c "
import urllib.request, json
req = urllib.request.Request('https://pypi.org/pypi/rei-verify/json', headers={'User-Agent':'rei-verify-verify/1.0'})
d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8'))
print('latest:', d['info']['version'])
print('provenance (Sigstore attestation):', d['releases'][d['info']['version']][0].get('provenance') is not None)
"
```

install:
```bash
pip install rei-verify         # core primitives
pip install rei-verify[mcp]    # + MCP server
```

---

## Rollback / yank

Alpha version 公開後、 深刻 bug を 発見した場合:
- **yank** (推奨): PyPI project settings で release yank → 新規 install は 拒否、 明示 pin されている 場合のみ install 可能 (削除ではない)
- **削除は不可**: PyPI は 一度 upload された file を 削除できない仕様 (immutability)

yank の 前に yank-worthy か 検討し、 次 patch release (v0.1.0a2 等) で 対応する 方が 通常 望ましい。

---

## checklist

tag push 前 に 以下 全 ✓:

- [ ] PyPI 側 Pending Publisher 登録済 (Step 1)
- [ ] TestPyPI 側 Pending Publisher 登録済 (Step 2)
- [ ] GitHub `pypi` environment 作成済 (Step 3)
- [ ] GitHub `testpypi` environment 作成済 (Step 3)
- [ ] TestPyPI dry-run success 確認済 (Step 4)
- [ ] `TestPyPI 側 v0.1.0a1 install verify 済` (Step 4)
- [ ] version 番号 (pyproject.toml + README + `__init__.py`) 整合 確認
- [ ] `.github/workflows/publish.yml` の trigger + environment 名 double-check
- [ ] 藤本さん judgment 「本番 publish OK」 明示 approval

上記 全 ✓ 後、 Step 5 (tag push) を 実行。
