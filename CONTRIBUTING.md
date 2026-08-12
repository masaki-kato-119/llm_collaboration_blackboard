# Blackboard 協調・コミット運用ルール

複数の LLM クライアント（Claude、Cursor など）がこのリポジトリを並行編集する。
このファイルが、その協調ルールの**唯一の正本**である。`AGENTS.md` や
`.cursor/rules/*` などツール固有のファイルに要約や運用メモがあっても、
内容が食い違った場合はこの CONTRIBUTING.md を優先する。ツール固有ファイル
側は、ルールを複製せずこのファイルを参照するのが望ましい。

## 作業開始プロトコル

Blackboard 上でタスクに着手する前に、次の順で確認する。

1. `list_plans` で Plan 一覧を確認し、対象の `plan_id` を決める（省略時は既定の `project`）。
2. `read_plan(role=..., plan_id=...)` で `executable_tasks` を見る。
3. 自分の Role に一致し、`executable_tasks` に含まれるタスクだけを
   `claim_task`（`actor_id` / `role` / `expected_revision` / `plan_id` を渡す）。
4. `conflict` を受け取ったら Plan を再読込みしてやり直す。**他 actor が
   `in_progress` にしているタスクには手を出さない**（二重実装の予防）。

複数の Plan・複数 actor が並行しているときは、`read_plan` の `tasks` から
`in_progress` のものと `started_by` を確認してから着手する。

## 1タスク（Blackboard の Plan 項目）= 1コミット

Blackboard の Plan（例: `blackboard/plan/project.md`）上でタスクを `done` にした
直後に、そのタスクの変更だけをまとめてコミットする。複数タスクの変更を1つの
コミットに混ぜない。

コミットメッセージは次の形式にする。

```
<type>(<task_id>): <要約>

Task: <task_id> — <Plan 上のタスク名>
Plan: <plan ファイルのパス、例 blackboard/plan/project.md>
```

`<type>` は `feat` / `fix` / `refactor` / `test` / `docs` / `chore` などを使う。

## コミット前に確認すること

- `pytest` がグリーンであること(`pre-commit install` 済みなら自動で走る。
  セットアップは次項)
- `ruff check src tests scripts` に新規の指摘がないこと
- そのタスクが Blackboard 上で `done` になっており、対応する Event が
  outbox から delivered 済みであること
- コミットに含めるファイルを `git status` で確認し、意図しないファイル
  （`.venv/`、`.mcp.json` などの機械固有設定、他タスクの変更）が
  混ざっていないこと
- 自分が作成・更新責任を持つ State 文書（例: `blackboard/state/project_state.md`）
  があれば、いま進行中の内容に更新されていること（次項を参照）

## pre-commit(任意だが推奨)

`.pre-commit-config.yaml` に ruff(lint、`--fix`)と pytest を登録済み。
初回だけ次を実行すると、以後 `git commit` のたびに自動で走る。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pre_commit install
```

`markdown-hierarchical-memory/` は参考用の別プロジェクトなので lint 対象外
(`pyproject.toml` の `[tool.ruff] extend-exclude`)。`ruff format` はまだ
有効化していない(既存コードが未対応、詳細は `doc/NEXT_WORK_PLAN_ja.md`
B-1-4)。

## State 文書は都度更新する（自動反映しない）

仕様7章の通り、Plan と State は意図的に別物として設計されている。
Plan は「何をすべきか」、State は「いま何が進行中か」に答える。
Plan のタスク一覧から State を自動生成・自動書き換えする仕組みは作らない
— それをやると State が Plan の単なる鏡像になり、State 固有の価値
（担当者が言葉で状況・懸念・次の一手を書き残せること）が失われる。

そのため、Plan 上のタスクを `claim`/`done` にしたとき、その変更が
Blackboard 全体や他 actor の作業状況に影響するなら、関係する State 文書も
同じタイミングで手で更新し、同じコミットに含める。更新を忘れて古い State が
残っていたら、気づいた actor がその場で `write_state` して直す
（気づいた時点で直す＝ State の陳腐化を溜め込まない）。

## フェーズ単位のまとめ

`doc/IMPLEMENTATION_PLAN_ja.md` のフェーズ（例: Phase 6）内の全タスクが `done` に
なったら、個々のタスクコミットに加えて、フェーズ完了を示す軽いまとめコミット
（例: `docs(phase6): mark Phase 6 complete`）を1つ追加してもよい。ただし
個々のタスクコミットの代わりにはしない。

## やらないこと

- `--amend` や force-push で既存のコミット履歴を書き換えない
- 複数タスクの変更を1コミットに squash しない
- Blackboard の Plan / Memory / State / Event ファイルと、対応するソース
  コード変更を別々のコミットに分けない（1タスク分はまとめて1コミット）
