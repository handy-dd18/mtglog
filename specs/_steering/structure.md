---
status: draft
---

# リポジトリ構成

```
.
├── CLAUDE.md
├── specs/
│   ├── _steering/          # 製品・技術・構成の前提（全機能共通）
│   └── <feature>/          # requirements.md / design.md / tasks.md
├── web/                    # 取得ページ（Edge で開く静的 HTML/JS、サーバーが配信）
├── server/                 # 文字起こしサーバー（Python）
│   ├── src/mtglog/
│   └── tests/
├── docker/                 # Dockerfile と compose.yaml
├── scripts/                # 起動スクリプト（WSL 用 .sh、Windows 用 .ps1）
└── output/                 # 文字起こし結果（git 管理外）
```

- Python は `uv` で依存を管理し、`pytest` でテストする。
- 取得ページはビルド工程を持たない。音声変換などのロジックは純粋な関数に分け、Node.js のテストランナー（`node --test`）で単体テストする。
- 結果ファイルと音声ファイルは `output/` に置き、git で追跡しない。
