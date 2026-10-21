# 現在の状況

最終更新: 2026-10-21

## いまの段階

初版を一通り実装した（2026-10-21）。利用者の指示で、本番 PC での検証（T0.2）より先に実装を進めた。残っているのは本番 PC と実会議での手動確認である。

| 文書 | 状態 |
|---|---|
| `specs/_steering/`（product / tech / structure） | draft（要件・設計の前提として使用中） |
| `specs/meeting-transcriber/requirements.md` | approved（2026-10-21）。REQ-4.4 を改訂（録音を既定で保存。利用者の指示） |
| `specs/meeting-transcriber/design.md` | approved（2026-10-21）。4.2・4.3・5.2 の改訂と、録音の既定の変更（3.4・3.5）も承認済み（2026-10-21） |
| `specs/meeting-transcriber/tasks.md` | approved（2026-10-21） |
| `specs/speaker-diarization/requirements.md`（次版） | draft（2026-10-21） |
| `specs/open-audio/requirements.md`（次版） | draft（2026-10-21） |

| タスク | 状態 |
|---|---|
| T0.1, T1.1〜T1.4, T2.1〜T2.4, T3.1, T4.2 | 完了 |
| T3.2, T3.3, T4.1, T4.3 | 実装済み。本番 PC での手動確認待ち（T4.1 は GPU 付きの Docker も未確認） |
| T0.2, T4.4 | 未実施（本番 PC・実会議での確認） |

## 次にやること

1. 本番 PC で T0.2 を行う（`spike/README.md`）。V1・V3・V7 の結果によっては、取得ページの共有方法を直す。
   - V1 では、共有の選択画面で「画面全体」タブを選び、「システム オーディオも共有する」をオンにする。
2. 本番 PC で本体を動かし、T3.2・T3.3・T4.3 の手動の完了条件を確かめる（README の「記録のしかた」に沿って行う）。
3. 実際の Teams 会議で T4.4 を行う。
4. 次版の要件（speaker-diarization、open-audio）の未決事項を決め、承認する。

## 検証項目の状況

| ID | 内容 | 結果 |
|---|---|---|
| V1 | Edge で画面共有とマイクが使えるか | 未確認（開発用 PC ではマイクと画面共有の API は使えた。システム音声は未確認） |
| V2 | Edge から WSL のサーバーに localhost で接続できるか | 可（2026-10-21） |
| V3 | ウィンドウ共有で Teams の音だけが取れるか | 未確認 |
| V4 | 共有を続けても Teams の会議に影響がないか | 未確認 |
| V5 | 背景タブでも取得と送信が続くか | 未確認 |
| V6 | Edge で `http://localhost` を開けるか | 可（2026-10-21） |
| V7 | 映像トラックを止めても音声が続くか | 未確認 |

## 別の端末で作業を再開するとき

```bash
git clone git@github.com:handy-dd18/mtglog.git
cd mtglog && scripts/start.sh
```

前提と起動方法の詳細は `README.md` にある。テストは `cd server && uv run pytest`、`cd web && npm test`（Node.js 20 以降）。
