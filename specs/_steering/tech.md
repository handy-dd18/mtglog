---
status: draft
---

# 技術方針

## 動作環境（2026-10-21 時点で確認）

| 項目 | 値 |
|---|---|
| OS | Windows 11（build 26200）＋ WSL2（kernel 5.15） |
| GPU | NVIDIA GeForce RTX 3050（VRAM 6GB）（WSL から CUDA 12.8 が見える） |
| WSL の音声 | WSLg の PulseAudio。公開されるデバイスは `PulseAudioRDPSink` / `PulseAudioRDPSource` のみ |
| 実行中の会議ツール例 | `ms-teams.exe`（新 Teams） |

開発用 PC と本番 PC は別の可能性がある。上の値は開発用 PC で確認したもので、本番 PC では検証タスクで確かめ直す。

## 制約: Windows 側で独自の exe を実行できない

アプリは本番 PC で動かす想定であり、Windows 側で新しい実行ファイルを実行できない。AppLocker や WDAC（アプリケーション制御）が有効な PC では、PowerShell も Constrained Language Mode で起動し、`Add-Type` で C# を読み込んで Windows API を呼ぶ方法も封じられる。したがって、Windows の音声 API（WASAPI）を直接呼ぶ独自コードは、exe でもスクリプトでも使えない前提で設計する。

## 音声取得を WSL だけで完結できない理由

WSLg の PulseAudio は、Windows の既定デバイスを RDP 経由で中継しているだけである。WSL からはデバイスを選べず、スピーカーに出ている音（ループバック）も取れない。マイク側の `PulseAudioRDPSource` が届けるのは Windows の既定の録音デバイスの音だけで、相手の声は届かない。Docker Desktop のコンテナも WSL2 上で動くので条件は同じである。

## 採用方針: Edge を音声取得役にする

本番 PC に標準で入っていて実行が許されている Microsoft Edge（Chromium）のブラウザー API で音声を取る。文字起こしサーバー（WSL 上）が localhost で取得用の Web ページを配信し、利用者はそのページを Windows 側の Edge で開く。会議そのものには Edge で参加しない（主催者の設定によっては、Teams 会議にデスクトップアプリからしか参加できない）。Edge は会議と無関係な localhost のページを表示するだけである。localhost は安全なコンテキストとして扱われるので、HTTPS なしでもマイクと画面共有の API を使える。

| 取得対象 | 使う API | 取れる音 |
|---|---|---|
| 自分の声 | `getUserMedia`（`deviceId` 指定） | 利用者がページで選んだマイク（ヘッドセットのマイク） |
| 相手の声 | `getDisplayMedia`（画面全体＋システム音声） | Windows の既定の再生デバイスに出ている音から、ブラウザー自身の音を除いたもの |

Chromium の実装では、Windows のシステム音声の取得は「既定の再生デバイスで再生中の音を、Chrome の音を除いて取る」デバイス（`kLoopbackWithoutChromeId`）として定義されている。このため、会議ツールの出力先（ヘッドセット）が Windows の既定の再生デバイスでなければ、相手の声は取れない。初版はヘッドセット利用を前提とし、ヘッドセットを既定の再生デバイスにすることを利用条件にする。

### この方式で失うもの

- 会議ツールのプロセスが使うデバイスを OS のセッション情報から自動特定する機能。プロセスごとの音声セッションを列挙する API（`IAudioSessionManager2`）はネイティブコードからしか呼べない。代わりに、ページが既定デバイスとヘッドセットらしい名前のデバイスを候補として示し、利用者が確定する。
- 会議ツールの音だけを取る機能。システム音声には、同じ再生デバイスに出る通知音や他アプリの音も混ざる。Chrome 141 で追加された `windowAudio: "window"`（選んだウィンドウの音だけを共有する指定）が Windows の Edge で Teams／Zoom のウィンドウに使えれば、この欠点は解消できる。対応状況は未確認で、検証タスクで確かめる。

## 構成要素と技術選定

| 要素 | 実行場所 | 技術 |
|---|---|---|
| 取得ページ | Windows の Edge | HTML／JavaScript。AudioWorklet で 16 kHz モノラルに変換し、WebSocket で送る。ビルド工程なしの静的ファイル |
| 文字起こしサーバー | WSL2 または Docker | Python 3.12、FastAPI（取得ページの配信と WebSocket 受信）、faster-whisper（CUDA）、Silero VAD |

WSL2 の既定のネットワーク設定では、Windows 側から `localhost` で WSL 内のサーバーに接続できる（localhost 転送）。Docker の場合は `127.0.0.1` にポートを公開する。

### 文字起こしモデル

既定は faster-whisper の `large-v3-turbo`、計算型 `int8_float16`。6GB の VRAM に収まり、日本語の精度と速度の釣り合いがよいため。GPU が使えない環境では `small` を CPU（`int8`）で動かす。

## 実行基盤の優先順位

1. WSL2 上で直接実行（第一候補）。
2. PowerShell から `docker compose` でサーバーを起動する。Docker Desktop は、利用条件によって有償ライセンスが必要になる点に注意する。

取得ページは同じものを使うので、実行基盤の違いはサーバーの起動方法だけに閉じる。

## 未検証の前提（検証タスクで確かめる）

- V1: 本番 PC の Edge で、ポリシーによって画面共有（`getDisplayMedia`）やマイクの利用が禁止されていないか。
- V2: 本番 PC で WSL2 の localhost 転送が有効か（ネットワークモードや Hyper-V ファイアウォールの設定に依存する）。→ 確認済み（2026-10-21）。
- V3: Windows の Edge で `windowAudio: "window"` を指定し、Teams／Zoom のウィンドウを選んだときに、そのアプリの音だけが取れるか。
- V4: 会議中に Edge で画面共有を続けても、会議ツールの画面共有や会議の動作に影響しないか。
- V5: Edge のタブが背景にあるときや最小化されているときも、音声の取得と送信が止まらないか。
- V6: 本番 PC で Edge から `http://localhost` のページを開けるか（プロキシ設定や URL フィルタで止められないか）。→ 確認済み（2026-10-21）。

## 検討して採らなかった方式

| 方式 | 採らなかった理由 |
|---|---|
| Windows 側の C# exe（WASAPI のプロセスループバック） | 本番 PC で独自 exe を実行できない |
| PowerShell の `Add-Type` で WASAPI を呼ぶ | アプリケーション制御が有効な PC では Constrained Language Mode になり使えない。有効でない PC でも、セキュリティ製品に不審なスクリプトとして検知されやすい |
| WSLg の `PulseAudioRDPSource` | 既定の録音デバイス（マイク）しか届かず、相手の声が取れない |
| ステレオミキサー（Stereo Mix） | 音声ドライバーが対応している場合しか使えず、有効化には管理者の操作が要ることが多い |
| usbipd-win でヘッドセットを WSL に直接つなぐ | Windows へのインストールが必要で、つないでいる間は Teams／Zoom からヘッドセットが使えなくなる |
