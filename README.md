# mtglog

> [!WARNING]
> **開発中です。** 初版の実装は一通り終わっていますが、本番 PC と実際の会議での動作確認が済んでいません。仕様や使い方は予告なく変わることがあります。進み具合は [docs/STATUS.md](docs/STATUS.md) を参照してください。

Teams / Zoom の会議音声を、会議ツールに依存せず PC 内だけで文字起こしするアプリ。

- 現在の状況と次の作業: [docs/STATUS.md](docs/STATUS.md)
- 対応履歴: [docs/HISTORY.md](docs/HISTORY.md)
- ロードマップ: [docs/ROADMAP.md](docs/ROADMAP.md)
- 仕様: [specs/](specs/)（Spec 駆動。進め方は [CLAUDE.md](CLAUDE.md)）

## 仕組み

```
Windows                                  WSL2（または Docker）
 Teams / Zoom アプリ → ヘッドセット
 Edge で http://localhost:8765 を開く ──→ 文字起こしサーバー（faster-whisper）
   ├─ マイク        → 「自分」                └─ output/ に結果を保存
   └─ 画面共有の音声 → 「相手」
```

本番 PC では Windows 側で独自の exe を実行できないため、Windows 側では既存の Edge で Web ページを開くだけにしている。処理はすべて WSL 上で行い、音声も結果も PC の外へ送らない（外部への通信は、初回のセットアップでのパッケージとモデルの取得だけ）。詳しくは [specs/_steering/tech.md](specs/_steering/tech.md) を参照。

## 必要なもの

| 項目 | 用途 | 備考 |
|---|---|---|
| Windows 11 ＋ WSL2（Ubuntu） | サーバーの実行 | |
| Microsoft Edge | 音声の取得 | Windows に標準で入っているもの |
| ヘッドセット | 会議の音声 | Windows の既定の再生デバイスにしておく |
| [uv](https://docs.astral.sh/uv/) | Python と依存パッケージの管理 | WSL に入れる |
| git | リポジトリの取得 | WSL に入れる |
| NVIDIA GPU（VRAM 6 GB 以上） | 文字起こしの高速化 | なくても CPU で動く（遅くなり、精度も下がる） |
| Docker | Docker で動かす場合だけ | GPU を使うには Docker Desktop か NVIDIA Container Toolkit |

uv が入っていなければ、WSL で次を実行する。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 取得

```bash
git clone git@github.com:handy-dd18/mtglog.git
cd mtglog
```

## 初回の準備（Windows 側）

1. ヘッドセットを PC につなぐ。
2. Windows の「設定 → システム → サウンド」で、出力（再生）デバイスをヘッドセットにする。相手の声は、この既定の再生デバイスに出ている音から取るため。
3. Teams / Zoom の設定で、スピーカーとマイクをヘッドセットにする。

## 起動方法

### WSL で起動する（通常はこちら）

```bash
scripts/start.sh
```

初回は依存パッケージを取得し、文字起こしモデルを準備するので数分かかる（モデルの準備は下の「文字起こしモデル」を参照）。2 回目以降は外部へ接続しない。

```
モデル: whisper-large-v3-turbo / cuda / int8_float16

取得ページ: http://localhost:8765/  （Windows の Edge で開く）
停止するには Ctrl+C
```

と表示されたら起動している。GPU が使えない環境では、自動で CPU 用のモデル（`whisper-small`）に切り替わり、警告が表示される。

引数で設定を変えられる。

```bash
scripts/start.sh --no-save-audio       # 音声を保存しない（既定では保存する）
scripts/start.sh --language en         # 英語の会議
scripts/start.sh --port 8766           # ポートを変える
scripts/start.sh --device cpu          # GPU を使わない
```

毎回同じ設定を使うなら、`config.example.toml` を `config.toml` という名前でコピーして編集する。`scripts/start.sh` が自動で読み込む。

### 文字起こしモデル

モデルは、OpenAI が Hugging Face で公式に公開している Whisper（`openai/whisper-large-v3-turbo` と `openai/whisper-small`）だけを使う。faster-whisper 用の形式は OpenAI から公開されていないので、初回の起動時に `scripts/setup_models.sh` が公式のモデルを取得し、この PC の中で変換する。第三者が変換したモデルは使わない。

- GPU の有無にかかわらず、GPU 用（`models/whisper-large-v3-turbo`、約 0.8 GB）と CPU 用（`models/whisper-small`、約 0.25 GB）の両方を準備する。
- 取得する版はスクリプトに固定している。各モデルの `SOURCE.json` に、取得元、版、各ファイルの SHA-256 が記録される。
- 変換用のツール（CPU 用の PyTorch など）は使い捨ての環境に入れ、終わったら一時ファイルごと消す。準備の間だけ、約 4 GB の空きが必要になる。WSL の仮想ディスクは、ファイルを消しても自動では縮まないことがある。
- 準備済みなら何もしない。単独で実行することもできる（`scripts/setup_models.sh`）。
- サーバーはインターネットからモデルを取得しない。モデルがなければ、準備を案内して終了する。

以前の版（2026-10-21 まで）は、第三者が変換したモデルを `models/models--…` に自動で取得していた。今は使わないので、次のコマンドで削除してよい。

```bash
rm -rf models/models--* models/.locks models/CACHEDIR.TAG models/blobs
```

### Docker で起動する（PowerShell）

リポジトリのフォルダで次のどちらかを実行する。

```powershell
scripts\start.ps1           # GPU を使う
scripts\start.ps1 -Cpu      # GPU を使わない
```

スクリプトの実行が許可されていない場合は、同じ内容を直接実行する。

```powershell
docker compose -f docker/compose.yaml up --build
# GPU を使わない場合
docker compose -f docker/compose.yaml -f docker/compose.cpu.yaml up --build
```

コンテナの中でも `http://localhost:8765/` で開ける（公開先はこの PC の localhost だけ）。結果は `output/`、モデルは `models/` に保存される。初回の起動時は、コンテナの中で `scripts/setup_models.sh` が動いてモデルを準備する。

## 記録のしかた

1. 会議に Teams / Zoom のアプリで参加する（会議には Edge で参加しない）。
2. Edge で `http://localhost:8765/` を開く。初回はマイクの利用を「許可」する。
3. 「マイク」がヘッドセットのマイクになっているか確かめる。「スピーカー（相手の声の取得元）」に、ヘッドセットの名前が出ているかも確かめる。スピーカーはこのページでは選べない。違っていたら、初回の準備の 2 と 3 を見直す（Windows の既定の再生デバイスと Teams / Zoom のスピーカーを同じにする）。
4. 「記録を開始」を押す。画面共有の選択画面が開くので、**「画面全体」** を選び、**「システム オーディオも共有する」をオン** にして「共有」を押す。画面の映像は使わない。
5. 画面上部が「● 記録中」になり、自分・相手の音量メーターが動けば記録できている。確定した発話は順に表示される。
6. 会議が終わったら「記録を停止」を押す。表示された保存先に結果が残る。

> [!IMPORTANT]
> Teams / Zoom のミュートとは連動しない。このアプリは Edge からマイクを直接開いているので、会議ツールでミュートにしている間に話したことも、「自分」として文字起こしされ、録音にも残る。

記録中に相手の発話がないまま 2 分たつと（記録の開始時と、デバイスを抜き差しした後）、取得ページに警告が出る。自分も相手も話さないまま 2 分たったときも警告が出る。スピーカーの設定のずれや、記録の止め忘れに気づくためのもので、記録は止まらない。警告は「閉じる」で消せる。時間は `--silence-warning-sec`（秒、0 で無効）で変えられる。

記録中は Edge のタブを背景にしてよい。閉じると記録が止まるので、閉じないこと。サーバーとの接続が切れても、ページは自動で再接続し、切れている間の音声（最大 60 秒）を送り直す。

## 出力ファイル

記録ごとに `output/<開始日時>/` が作られる。

| ファイル | 内容 |
|---|---|
| `transcript.md` | 読むための文字起こし。`- 00:01:23 **相手**: 本文` の形で開始時刻順に並ぶ |
| `transcript.jsonl` | 1 発話 1 行の JSON（`start`, `end`, `speaker`, `text`, `wall_start`） |
| `meta.json` | 開始・終了時刻、使ったデバイス名、モデル、欠落した秒数など |
| `me.wav`, `remote.wav` | 自分・相手の録音（16 kHz モノラル）。既定で作られる。2 つ合わせて 1 時間あたり約 230 MB。保存しない場合は、取得ページの設定で「音声を WAV で保存する」を外すか、`--no-save-audio` で起動する |

発話が確定するたびに追記するので、途中で異常終了しても、それまでの結果は残る。サーバーを再起動した場合、記録は新しいディレクトリに続きとして保存される（`meta.json` の `continued_from` で前の記録とつながる）。

## うまく動かないとき

| 症状 | 確かめること |
|---|---|
| Edge で `http://localhost:8765/` が開けない | WSL でサーバーが起動しているか。WSL で `curl http://127.0.0.1:8765/` が成功するか。成功するのに Edge から開けない場合は、WSL の localhost 転送（`.wslconfig` の `localhostForwarding`）の設定を確かめる |
| マイクの一覧に名前が出ない | Edge のアドレスバー左のアイコンから、マイクを「許可」にしたか |
| 「音声が共有されませんでした」と出る | 共有の選択画面で「画面全体」を選び、「システム オーディオも共有する」をオンにしたか |
| 相手のメーターが動かない、「相手の音声が届いていません」と出る | Teams / Zoom のスピーカーと、Windows の既定の再生デバイス（取得ページの「スピーカー」）が同じか。Windows の「サウンド」の「既定の通信デバイス」や、音量ミキサーのアプリごとの出力先で、会議ツールだけ別のデバイスになっていないか |
| 文字起こしが遅い・警告が出る | GPU が使えず CPU で動いている可能性がある。起動時の「モデル:」の表示が `cuda` かを確かめる |
| `uv: command not found` | uv を入れたあと、端末を開き直したか |
| Docker で `could not select device driver "" with capabilities: [[gpu]]` | Docker から GPU を使えない。`-Cpu`（または `compose.cpu.yaml`）で起動するか、Docker Desktop / NVIDIA Container Toolkit を用意する |

## 開発

```bash
cd server && uv run pytest                 # サーバーの単体テスト
MTGLOG_GPU_TESTS=1 uv run pytest           # 実モデルを使う結合テスト（GPU、モデル準備済み）
uv run --with playwright python tests/e2e_browser.py   # ヘッドレス Chromium での通し確認（libasound2 が必要）
uv run --with playwright python tests/e2e_silence.py   # 無音の警告の表示の確認（同上）
cd web && npm test                         # 取得ページの単体テスト
```

`spike/` は本番 PC での検証（T0.2）に使う検証用ページで、本体とは別に起動する（[spike/README.md](spike/README.md)）。
