import os
import re
import requests
from bs4 import BeautifulSoup
from pydub import AudioSegment
from urllib.parse import urljoin


def get_category_name(category_url):
    """カテゴリURLから番組名を抽出する（例: hajimetotalking）"""
    match = re.search(r"/category/([^/]+)/?", category_url)
    if not match:
        raise ValueError(f"カテゴリ名を抽出できません: {category_url}")
    return match.group(1)


def get_episode_links(category_url):
    """カテゴリページから全エピソードのURLを取得し、古い順（番号昇順）にソートして返す"""
    category_name = get_category_name(category_url)
    response = requests.get(category_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    episode_links = []
    pattern = re.compile(rf"/{re.escape(category_name)}\d+/?$")
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if pattern.search(href):
            full_url = urljoin(category_url, href)
            if full_url not in episode_links:
                episode_links.append(full_url)

    # 番号でソート（古い順＝番号が小さい順）
    episode_links.sort(key=lambda url: int(re.search(r"(\d+)/?$", url).group(1)))
    return episode_links


def extract_episode_info(episode_url):
    """個別エピソードページから番組名・番号・MP3パスを抽出する"""
    response = requests.get(episode_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    # og:url から番組名を抽出（例: hajimetotalking0001）
    og_url_tag = soup.find("meta", property="og:url")
    if not og_url_tag:
        raise ValueError(f"og:url が見つかりません: {episode_url}")

    og_url = og_url_tag.get("content", "")
    # og:url の末尾パス部分を番組名として抽出（例: hajimetotalking0001）
    name_match = re.search(r"/([a-zA-Z0-9_-]+\d+)/?$", og_url)
    if not name_match:
        raise ValueError(f"番組名を抽出できません: {og_url}")
    episode_name = name_match.group(1)

    # audioFile クラス内の audio タグから MP3 パスを取得（CMを除外）
    audio_file_ul = soup.find("ul", class_="audioFile")
    if audio_file_ul:
        audio_tag = audio_file_ul.find("audio", src=True)
    else:
        # フォールバック: cmSource 以外の audio タグを探す
        cm_sources = soup.find("ul", class_="cmSource")
        cm_audios = set()
        if cm_sources:
            for a in cm_sources.find_all("audio"):
                cm_audios.add(a.get("src", ""))
        audio_tag = None
        for a in soup.find_all("audio", src=True):
            if a.get("src", "") not in cm_audios:
                audio_tag = a
                break

    if not audio_tag:
        raise ValueError(f"audio タグが見つかりません: {episode_url}")

    mp3_path = audio_tag["src"]

    # 番号を抽出（HHMMSS部分、例: 074827）
    number_match = re.search(r"_(\d{6})_\d+\.mp3", mp3_path)
    if not number_match:
        raise ValueError(f"番号を抽出できません: {mp3_path}")
    episode_number = number_match.group(1)

    # 完全なMP3 URLを構築
    mp3_url = urljoin(episode_url, mp3_path)

    return {
        "name": episode_name,
        "number": episode_number,
        "mp3_url": mp3_url,
        "mp3_path": mp3_path,
    }


def download_mp3(mp3_url, save_path):
    """MP3ファイルをダウンロードする"""
    response = requests.get(mp3_url, stream=True)
    response.raise_for_status()
    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return save_path


def convert_to_wav(mp3_path, wav_path):
    """MP3をWAVに変換する"""
    audio = AudioSegment.from_mp3(mp3_path)
    audio.export(wav_path, format="wav")
    return wav_path


def process_episode(episode_url, output_dir, formats=None, callback=None):
    """1つのエピソードを処理する（抽出→ダウンロード→変換→リネーム）
    formats: 出力形式のリスト（例: ["wav"], ["mp3"], ["wav", "mp3"]）
    """
    if formats is None:
        formats = ["wav"]

    info = extract_episode_info(episode_url)
    base_name = f"{info['name']}{info['number']}"
    mp3_temp_path = os.path.join(output_dir, "temp_dl.mp3")

    if callback:
        callback("downloading", info["name"], info["mp3_url"])

    download_mp3(info["mp3_url"], mp3_temp_path)

    output_filenames = []

    if "wav" in formats:
        wav_filename = f"{base_name}.wav"
        wav_path = os.path.join(output_dir, wav_filename)
        if callback:
            callback("converting", info["name"], wav_filename)
        convert_to_wav(mp3_temp_path, wav_path)
        output_filenames.append(wav_filename)

    if "mp3" in formats:
        mp3_filename = f"{base_name}.mp3"
        mp3_path = os.path.join(output_dir, mp3_filename)
        if callback:
            callback("saving", info["name"], mp3_filename)
        # ダウンロード済みMP3をリネームコピー
        import shutil
        shutil.copy2(mp3_temp_path, mp3_path)
        output_filenames.append(mp3_filename)

    # 一時MP3ファイルを削除
    os.remove(mp3_temp_path)

    return {
        "filenames": output_filenames,
        "filename": output_filenames[0] if output_filenames else "",
        "episode_name": info["name"],
        "number": info["number"],
        "source_url": info["mp3_url"],
    }


def process_all_episodes(category_url, output_dir, progress_callback=None):
    """全エピソードを古い順に処理する"""
    os.makedirs(output_dir, exist_ok=True)

    episode_links = get_episode_links(category_url)
    total = len(episode_links)
    results = []

    for i, episode_url in enumerate(episode_links):
        if progress_callback:
            progress_callback(i, total, episode_url, "processing")

        try:
            result = process_episode(episode_url, output_dir)
            result["status"] = "success"
            results.append(result)
        except Exception as e:
            results.append({
                "episode_url": episode_url,
                "status": "error",
                "error": str(e),
            })

        if progress_callback:
            progress_callback(i + 1, total, episode_url, "done")

    return results
