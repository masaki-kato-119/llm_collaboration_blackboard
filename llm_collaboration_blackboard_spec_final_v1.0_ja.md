# LLM Collaboration Blackboard Specification

**ステータス:** Final Draft  
**バージョン:** 1.0  
**日付:** 2026-08-10

## 1. 目的

この仕様書は、複数の LLM が共有された Blackboard を介して協調する、最小限の協調メカニズムを定義するものです。

目的は、中央集権的な LLM オーケストレータを構築することではありません。各 LLM は事前に定義された Role を持ち、Blackboard を独立して観察して、次に何ができるかを判断します。

Blackboard は Markdown を永続表現として使用し、LLM からは MCP を通じてアクセスされます。

中核となるモデルは次の通りです。

```text
Memory = 既知の内容
Plan   = 実行すべき内容
State  = 現在進行中の内容
Event  = 発生した内容
Role   = この LLM が担う責務
LLM    = 判断と振る舞い
MCP    = Blackboard へのアクセス手段
```

このシステムは意図的に小規模に設計されています。外部ツールのオーケストレーションは、コア仕様の範囲外であり、後から接続可能です。

---

# 2. 設計原則

1. 複数の LLM が共有された Blackboard を通じて協調する。
2. LLM は互いに直接通信する必要がない。
3. 各 LLM は事前に定義された Role を持つ。
4. Blackboard は共有された調整情報の元となる情報源である。
5. Memory、Plan、State、Event は異なる意味を持つ。
6. Plan は共有されたチェックリストであり、作業順序の主要な表現である。
7. LLM は中央から命令を受けるのではなく、Plan から次に実行可能なタスクを選ぶ。
8. MCP は Blackboard へのアクセスを提供するものであり、オーケストレータではない。
9. Markdown は永続表現である。
10. 実装は可能な限りシンプルであるべきである。
11. 人間ユーザーは Blackboard を確認・変更できる。
12. 外部ツールやシステムは、コア協調モデルの外部に接続される。

---

# 3. アーキテクチャ

```text
                    ┌─────────────────────────────┐
                    │         Blackboard          │
                    │                             │
                    │  Memory                     │
                    │  Plan                       │
                    │  State                      │
                    │  Event                      │
                    │                             │
                    │          Markdown            │
                    └──────────────┬──────────────┘
                                   │
                                  MCP
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
        ┌───────────┐       ┌───────────┐       ┌───────────┐
        │   LLM A   │       │   LLM B   │       │   LLM C   │
        │ Researcher│       │Implementer│       │ Reviewer  │
        └───────────┘       └───────────┘       └───────────┘
```

Blackboard が協調の中心である。

次に動く LLM を決めるための必須の中央コンポーネントは存在しない。

---

# 4. Blackboard

Blackboard は Markdown ドキュメントを含む共有情報空間である。

推奨構造は次のとおりである。

```text
blackboard/
├── memory/
├── plan/
├── state/
└── event/
```

物理的なディレクトリ構造は実装上の推奨事項であり、プロトコル要件ではない。

各ドキュメントは YAML Front Matter を付けた後に Markdown 本文を持つことができる。

---

# 5. Memory

## 5.1 目的

Memory は、将来の作業に役立つ可能性のある知識、経験、判断、観察、その他の情報を保存する。

例:

```yaml
---
id: architecture_decision_001
type: memory
created: 2026-08-10
updated: 2026-08-10
importance: high
tags:
  - architecture
  - decision
---
```

Memory は永続的な知識である。

コア仕様は特定の検索メカニズムを規定しない。RAG、検索インデックス、GraphRAG、その他のメカニズムは、独立して追加できる。

---

# 6. Plan

## 6.1 目的

Plan は実行すべき作業を共有するための表現である。

意図的に複雑なワークフローエンジンではなく、チェックリストとして実装される。

Plan には次の情報が記録される。

- どのタスクが存在するか
- どの Role が責任を持つか
- タスクの状態
- 誰がそのタスクを開始したか
- いつ開始したか
- 誰が完了したか
- いつ完了したか
- タスク依存関係
- 並列作業のための任意のグループ化

例:

```markdown
# Plan: Project Alpha

| ID | Task | Role | Status | Started By | Started | Completed By | Completed |
|---|---|---|---|---|---|---|---|
| 1 | Research | Researcher | done | llm-a | 09:00 | llm-a | 09:30 |
| 2 | Architecture | Architect | done | llm-b | 09:40 | llm-b | 10:20 |
| 3 | Implementation | Implementer | in_progress | llm-c | 10:30 | | |
| 4 | Security Review | Reviewer | pending | | | | |
| 5 | Performance Review | Reviewer | pending | | | | |
| 6 | Final Review | Reviewer | pending | | | | |
```

依存関係は別途表現できる。

```yaml
dependencies:
  4:
    - 3
  5:
    - 3
  6:
    - 4
    - 5
```

これは、タスク 3 が完了した後に 4 と 5 を並行して進められ、タスク 6 は両方の完了を待つことを意味する。

## 6.2 タスク状態

最小限のタスク状態は次のとおりである。

- `pending`
- `in_progress`
- `done`
- `blocked`
- `cancelled`

必要に応じて追加状態を持たせることもできるが、コアモデルは小さく保つべきである。

## 6.3 次のタスクの選択

LLM は次の情報を確認して、次に実行するタスクを決定する。

1. 自身の Role
2. pending なタスク
3. タスク依存関係
4. 現在の State
5. 関連する Memory

タスクが実行可能である条件は次のとおりである。

- 状態が `pending`
- Role が LLM の Role と一致する
- 必要な依存関係がすべて `done`
- ブロッキング条件がない

その後、LLM はタスクを `in_progress` に変更して自分で引き受ける。

これが基本的な協調メカニズムである。

---

# 7. State

## 7.1 目的

State は、タスクまたは協調全体の現在の状況を表す。

例:

```yaml
---
id: project_alpha
type: state
created: 2026-08-10
updated: 2026-08-10T14:30:00+09:00
revision: 3
status: in_progress
current_task: 3
---
```

State は次の問いに答える。

> いま何が進行中か？

Plan は次の問いに答える。

> 何をすべきか？

これらは意図的に分けられている。

---

# 8. Event

## 8.1 目的

Event は、Blackboard 上で何かが起きたことを記録する。

Event は特定の LLM に宛てたコマンドではない。

例:

```yaml
---
id: event_000123
type: event
created: 2026-08-10T14:35:00+09:00
event_type: task_completed
source: llm-c
task_id: 3
---
```

Event には次のような内容を記載できる。

- タスク開始
- タスク完了
- タスクブロック
- 状態変更
- Plan 変更
- 人間による介入
- その他関連する事実

初期実装では、Event は主に観察と監査のために使われる。

システムは、Event が特定の対象 LLM を含む必要はない。

---

# 9. Role

各 LLM には事前に定義された Role がある。

例:

```text
LLM A = Researcher
LLM B = Architect
LLM C = Implementer
LLM D = Reviewer
```

Role はその LLM に期待される責務と能力を定義する。

Role は他の LLM が動的に送る命令ではない。

LLM は Role を使って、自分に関連する Plan タスクを決定する。

---

# 10. 協調モデル

基本的な協調ループは次のとおりである。

```text
        ┌─────────────────────┐
        │      Blackboard     │
        │                     │
        │ Plan / State /      │
        │ Memory / Event      │
        └──────────┬──────────┘
                   │
                   ▼
              LLM observes
                   │
                   ▼
          Select executable task
                   │
                   ▼
              Claim task
                   │
                   ▼
                Work
                   │
                   ▼
          Update Blackboard
                   │
                   ▼
               Emit Event
                   │
                   └───────────────► next LLM
```

次に動く LLM は中央オーケストレータによって選ばれるのではない。

共有 Plan を確認することで、作業を発見する。

---

# 11. 並列性と同期

このシステムは当初、専用スケジューラを必要としない。

並列性は Plan から生じる。

例:

```text
                 Implementation
                       │
              ┌────────┴────────┐
              ▼                 ▼
        Security Review   Performance Review
              │                 │
              └────────┬────────┘
                       ▼
                  Final Review
```

タスク 4 と 5 は、互換性のある LLM によって独立に引き受けられる可能性がある。

タスク 6 は、必要な依存関係が両方とも `done` になった後にのみ実行可能になる。

これにより、最小限の以下の機能が提供される。

- 順序付け
- 依存関係
- 並列実行
- 同期

ただし、ワークフローエンジンを導入することはない。

---

# 12. タイミング

タイミングは主に Plan とタスク履歴によって表現される。

各タスクには次を記録できる。

- `started`
- `completed`
- `started_by`
- `completed_by`

したがって Plan は共有された実行タイムラインを提供する。

より高度なスケジューリング、期限、周期的トリガー、タイムアウト、時間的制約は MVP の範囲外であり、必要に応じて後から追加できる。

---

# 13. MCP

MCP は Blackboard へのアクセスを提供する。

初期の論理操作は次のとおりである。

```text
read_memory
write_memory
read_plan
claim_task
update_task

read_state
write_state

read_event
emit_event
```

正確な MCP ツール／リソースのスキーマは実装依存である。

MCP は次のことをしない。

- LLM を選択する
- Role を割り当てる
- タスクを分解する
- LLM が何をすべきか決める
- ワークフローオーケストレータとして動作する
- 外部ツールを管理する

MCP は共有 Blackboard へのアクセス層である。

---

# 14. 並行性

複数の LLM が同じタスクの請負や更新を試みることがある。

最低限の実装では、2 つの LLM が同じタスクを同時に引き受けられないようにする必要がある。

シンプルな compare-and-set や revision 機構で十分である。

例:

```text
LLM A がタスクを読んで pending と判断
LLM B もタスクを読んで pending と判断

LLM A がタスクを請負
タスク = in_progress

LLM B が請負を試みる
請負は拒否される

LLM B が Plan を再読する
別の実行可能タスクを選ぶ
```

実装は、可能な限り最もシンプルで信頼性のあるメカニズムを優先すべきである。

完全な分散ロックシステムは不要である。

---

# 15. Human-in-the-loop

人間は Blackboard の第一級参加者である。

人間は次のことができる。

- Plan を作成・変更する
- タスク状態を変更する
- Memory を追加する
- State を変更する
- Event を確認する
- 作業を一時停止・再開する
- タスクを追加・削除する

例:

```yaml
status: blocked
reason: "Architecture decision required"
```

これにより、人間の判断を LLM と同じ協調空間に入れられる。

---

# 16. セッションと環境の継続性

Blackboard が永続的であるため、作業は次の範囲で継続できる。

- LLM セッション
- 異なる LLM 製品
- デスクトップ環境と CLI 環境
- 異なるマシン
- 人間と LLM のセッション

例:

```text
Claude
   │
   ▼
Blackboard
   │
   ├── Plan
   ├── State
   ├── Memory
   └── Event
   │
   ▼
Other LLM
```

新しい LLM は、関連情報が Blackboard に記録されていれば、前回の会話履歴を持たなくても現在の作業を判断できる。

---

# 17. 外部ツール

外部ツールは、コア協調モデルの外に置かれる。

後に、MCP や他のアダプタを通じて、Blackboard 参加者を次のようなツールに接続できる。

```text
Browser
Blender
Unity
GitHub
OS Puppeteer
Mesen
SysML tools
etc.
```

アーキテクチャは次のとおりである。

```text
                LLM Collaboration
                      │
                 Blackboard
                      │
                     MCP
                      │
              External adapters
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
       Browser      Blender      GitHub
```

協調モデルは、外部ツール実装とは独立して維持される。

---

# 18. セキュリティと権限

コア仕様は完全なセキュリティモデルを定義しない。

ただし、実装では次を前提とすべきである。

- すべての LLM がすべての Blackboard ドキュメントに書き込み権限を持つ必要はない
- Role を権限に対応付けられる
- 外部ツールには別途認可が必要になることがある
- 破壊的な操作が暗黙に許可されるべきではない

権限システムは、Blackboard のデータモデルを変えずに追加できる。

---

# 19. ファイルとデータ形式

Markdown は人間にとっての標準的な可読表現である。

YAML Front Matter には機械可読なメタデータを含められる。

例:

```markdown
---
id: task_003
type: task
role: implementer
status: in_progress
started_by: llm-c
started: 2026-08-10T10:30:00+09:00
---

# Implementation

Implement the API error handling.

## Notes

...
```

実装は性能向上のためにインデックスやキャッシュを使うことができるが、Markdown 表現は回復可能で理解しやすい状態で残す必要がある。

---

# 20. MVP

MVP は、完全なオーケストレーション基盤を提供するためではなく、基本的な協調モデルを実証するためのものである。

## 20.1 MVP の範囲

MVP は次をサポートするものとする。

1. Markdown Blackboard
2. Plan チェックリスト
3. 複数の事前定義済み LLM Role
4. MCP アクセス
5. Plan の読み取り
6. 実行可能なタスクの選択
7. 原子的なタスク請負
8. タスク状態の更新
9. 開始／完了情報の記録
10. 基本的な State
11. 基本的な Memory
12. 基本的な Event ログ

## 20.2 MVP シナリオ

最小デモは次のとおりである。

```text
LLM A = Researcher
LLM B = Implementer
LLM C = Reviewer

Plan:
  1. Research
  2. Implement
  3. Review
```

実行:

```text
LLM A
  ├─ claims Research
  ├─ performs research
  ├─ writes Memory
  ├─ marks Research done
  └─ emits task_completed

LLM B
  ├─ sees Implementation is now executable
  ├─ claims Implementation
  ├─ performs implementation
  ├─ updates State
  └─ marks Implementation done

LLM C
  ├─ sees Review is executable
  ├─ claims Review
  ├─ performs review
  └─ marks Review done
```

中央の LLM オーケストレータは不要である。

## 20.3 MVP 受け入れ条件

MVP は次の条件を満たした場合に成功とみなす。

- 2 つ以上の独立した LLM プロセスが 1 つの Blackboard を共有できる
- 各 LLM が自分の Role に一致するタスクを特定できる
- 2 つの LLM が同じタスクを成功裏に請負できない
- 完了したタスクにより依存タスクが実行可能になる
- 並列タスクを異なる LLM が請負できる
- 1 つの LLM が書き込んだ Memory を別の LLM が読み取れる
- State が LLM セッション再起動後も維持される
- Event が重要な操作の監査記録を提供する
- 人間が Markdown Blackboard を確認・変更できる

---

# 21. フル実装

MVP 後のフル実装では、同じ概念モデルを維持するべきである。

追加候補には次がある。

- より豊かな Plan の依存関係表現
- タスク優先度
- 期限とタイムアウト
- スケジュール済みタスク
- より豊かな Event フィルタリング
- Event サブスクリプション
- 権限管理
- Blackboard インデックス
- RAG 連携
- Graph-based Memory
- 監査履歴
- 復旧とロールバック
- 外部 MCP アダプタ
- 複数 Blackboard インスタンス
- プロジェクト／ワークスペース分離

これらはコアモデルの前提条件ではなく、拡張機能である。

---

# 22. 実装フェーズ

## フェーズ 1 — Blackboard Core

実装する内容:

- Markdown ファイルストレージ
- Memory CRUD
- Plan CRUD
- State CRUD
- Event 追加／読み取り
- YAML Front Matter パース

## フェーズ 2 — MCP Server

公開するもの:

- `read_memory`
- `write_memory`
- `read_plan`
- `claim_task`
- `update_task`
- `read_state`
- `write_state`
- `read_event`
- `emit_event`

## フェーズ 3 — Task Claiming

原子的な請負と revision／衝突検出を実装する。

## フェーズ 4 — Multi-LLM Test

異なる Role を持つ少なくとも 2 つの独立した LLM クライアントを同じ Blackboard に対して実行する。

## フェーズ 5 — Parallelism

独立したタスクが並行して実行され、依存タスクが必要な先行タスク完了を待つことを示す。

## フェーズ 6 — Full Implementation

コア Blackboard モデルを変えずに、高度な機能を追加する。

---

# 23. 非対象

以下はバージョン 1.0 では明示的に対象外である。

- 汎用 LLM エージェントフレームワークの構築
- MCP の置き換え
- 中央集権型のスーパーエージェントの作成
- すべてのタスクを中央 LLM に動的にルーティングすること
- 汎用ワークフローエンジンの構築
- データベースの必須化
- 特定の LLM ベンダーへの依存
- 特定の RAG 実装への依存
- 最初からすべての外部ツールを統合すること

---

# 24. 中核概念

このモデル全体は次のように要約できる。

```text
                  LLM Collaboration Blackboard

    Memory       Plan        State        Event
      │            │           │            │
      └────────────┴───────────┴────────────┘
                         │
                     Blackboard
                         │
                        MCP
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
      LLM A            LLM B            LLM C
    Researcher       Implementer       Reviewer
        │                │                │
        └────────────────┼────────────────┘
                         │
                  shared progress
```

基本原則は次のとおりである。

> LLM は別の LLM によってオーケストレートされる必要はない。事前に定義された Role に従って共有 Blackboard を観察・更新することで、協調できる。

Plan は共有された作業タイムラインを提供し、State は現在の状態を表し、Memory は知識を提供し、Event は起きた内容を記録する。

MCP は共通のアクセス手段を提供する。

これは LLM 協調を成立させるために必要な最小システムである。
