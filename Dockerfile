# 使用するPythonの軽量イメージ
FROM python:3.11-slim

# uvの最新バイナリをコピー
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 作業ディレクトリの設定
WORKDIR /app

# バイトコードの生成を有効化
ENV UV_COMPILE_BYTECODE=1

# 依存関係定義ファイルを先にコピーしてレイヤーキャッシュを活用
COPY pyproject.toml uv.lock ./

# 依存関係をインストール (開発用パッケージは除外、プロジェクト本体のインストールはスキップ)
RUN uv sync --frozen --no-dev --no-install-project

# アプリケーションのコードをコピー
COPY main.py README.md ./
COPY assets/ ./assets/

# ポート番号の公開
EXPOSE 8550

# コンテナ起動時に.venvのPythonを使用してFletアプリを実行
CMD ["/app/.venv/bin/python", "main.py"]
