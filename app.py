import os
import zipfile
import shutil
import threading
import tempfile
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory

from scraper import get_episode_links, process_episode

app = Flask(__name__)

# 処理状態を保持
processing_state = {
    "running": False,
    "current": 0,
    "total": 0,
    "current_episode": "",
    "status_message": "",
    "results": [],
    "completed": False,
    "error": None,
    "output_dir": None,
}


def reset_state():
    # 前回の一時ディレクトリを削除
    old_dir = processing_state.get("output_dir")
    if old_dir and os.path.exists(old_dir):
        shutil.rmtree(old_dir, ignore_errors=True)

    processing_state["running"] = False
    processing_state["current"] = 0
    processing_state["total"] = 0
    processing_state["current_episode"] = ""
    processing_state["status_message"] = ""
    processing_state["results"] = []
    processing_state["completed"] = False
    processing_state["error"] = None
    processing_state["output_dir"] = None


def background_process(category_url, output_dir, formats):
    """バックグラウンドで全エピソードを処理する"""
    try:
        os.makedirs(output_dir, exist_ok=True)

        episode_links = get_episode_links(category_url)
        processing_state["total"] = len(episode_links)

        for i, episode_url in enumerate(episode_links):
            processing_state["current"] = i + 1
            processing_state["current_episode"] = episode_url
            processing_state["status_message"] = f"処理中: {i + 1}/{len(episode_links)}"

            try:
                result = process_episode(episode_url, output_dir, formats=formats)
                result["status"] = "success"
                processing_state["results"].append(result)
            except Exception as e:
                processing_state["results"].append({
                    "episode_url": episode_url,
                    "status": "error",
                    "error": str(e),
                })

        processing_state["completed"] = True
        processing_state["status_message"] = "全エピソードの処理が完了しました"
    except Exception as e:
        processing_state["error"] = str(e)
        processing_state["status_message"] = f"エラー: {str(e)}"
    finally:
        processing_state["running"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    if processing_state["running"]:
        return jsonify({"error": "処理が既に実行中です"}), 400

    data = request.get_json()
    category_url = data.get("url", "").strip()
    if not category_url:
        return jsonify({"error": "URLを入力してください"}), 400

    formats = data.get("formats", ["wav"])
    if not formats:
        return jsonify({"error": "出力形式を1つ以上選択してください"}), 400

    reset_state()
    processing_state["running"] = True
    processing_state["status_message"] = "エピソード一覧を取得中..."

    # 一時ディレクトリを作成
    output_dir = tempfile.mkdtemp(prefix="iap1_web_")
    processing_state["output_dir"] = output_dir

    thread = threading.Thread(target=background_process, args=(category_url, output_dir, formats))
    thread.daemon = True
    thread.start()

    return jsonify({"message": "処理を開始しました"})


@app.route("/status")
def status():
    return jsonify(processing_state)


@app.route("/download/<filename>")
def download_file(filename):
    dl_dir = processing_state.get("output_dir")
    if not dl_dir:
        return jsonify({"error": "ファイルが見つかりません"}), 404
    file_path = os.path.join(dl_dir, filename)
    if not os.path.exists(file_path):
        return jsonify({"error": "ファイルが見つかりません"}), 404
    return send_from_directory(dl_dir, filename, as_attachment=True)


@app.route("/download-all")
def download_all():
    dl_dir = processing_state.get("output_dir")
    if not dl_dir:
        return jsonify({"error": "ファイルがありません"}), 404

    success_results = [r for r in processing_state["results"] if r.get("status") == "success"]
    if not success_results:
        return jsonify({"error": "ダウンロード可能なファイルがありません"}), 404

    zip_path = os.path.join(dl_dir, "all_episodes.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for result in success_results:
            filenames = result.get("filenames", [result.get("filename")])
            for fname in filenames:
                file_path = os.path.join(dl_dir, fname)
                if os.path.exists(file_path):
                    zf.write(file_path, fname)

    return send_file(zip_path, as_attachment=True, download_name="all_episodes.zip")


if __name__ == "__main__":
    app.run(debug=True, port=5002)
