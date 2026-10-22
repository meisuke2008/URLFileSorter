import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

MAX_MESSAGE_BYTES = 64 * 1024
MAX_PATH_LENGTH = 8192


def read_message():
    raw_length = sys.stdin.buffer.read(4)

    if len(raw_length) == 0:
        return None

    if len(raw_length) != 4:
        raise RuntimeError("メッセージ長を読み取れませんでした")

    message_length = struct.unpack("<I", raw_length)[0]
    if not 0 < message_length <= MAX_MESSAGE_BYTES:
        raise ValueError("メッセージサイズが上限を超えているか、空です")
    message_bytes = sys.stdin.buffer.read(message_length)

    if len(message_bytes) != message_length:
        raise RuntimeError("メッセージ本体を読み取れませんでした")

    return json.loads(message_bytes.decode("utf-8"))


def send_message(message):
    encoded = json.dumps(
        message,
        ensure_ascii=False
    ).encode("utf-8")

    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def create_unique_path(destination):
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    parent = destination.parent
    number = 1

    while True:
        candidate = parent / f"{stem} ({number}){suffix}"

        if not candidate.exists():
            return candidate

        number += 1


def validated_path(value):
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_LENGTH or "\x00" in value:
        raise ValueError("不正なファイルパスです")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("絶対パスを指定してください")
    return path


def move_file(source_path, destination_folder):
    # Do not assume Downloads or drive C:; Chrome and the user choose paths.
    source = validated_path(source_path)
    folder = validated_path(destination_folder)

    if not source.exists():
        raise FileNotFoundError(f"移動元ファイルがありません: {source}")

    if not source.is_file():
        raise ValueError(f"移動元がファイルではありません: {source}")

    folder.mkdir(parents=True, exist_ok=True)

    destination = create_unique_path(folder / source.name)
    # Chrome or a PDF viewer may briefly keep a completed download open on
    # Windows. Retry only sharing/lock violations; never mask other failures.
    for delay in (0.15, 0.25, 0.4, 0.6, 0.8, None):
        try:
            shutil.move(str(source), str(destination))
            break
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (32, 33):
                raise
            # A cross-volume copy may have left a partial destination. Do not
            # overwrite it or silently create a duplicate on the next attempt.
            if destination.exists():
                raise
            if delay is None:
                raise PermissionError(
                    f"ファイルを使用中のため移動できません。PDFビューアーなどで閉じてから再試行してください: {source}"
                ) from error
            time.sleep(delay)

    return str(destination)


def existing_file(path):
    file_path = validated_path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"ファイルが見つかりません: {file_path}")
    return file_path.resolve()


def handle_message(message):
    if not isinstance(message, dict):
        raise ValueError("メッセージはJSONオブジェクトで指定してください")
    action = message.get("action")

    if action == "connection_test":
        return {
            "success": True,
            "message": "Pythonとの通信に成功しました"
        }

    if action == "move_file":
        moved_path = move_file(
            message["sourcePath"],
            message["destination"]
        )

        return {
            "success": True,
            "message": "ファイルを移動しました",
            "movedPath": moved_path
        }

    if action == "open_file":
        file_path = existing_file(message["path"])
        os.startfile(str(file_path))
        return {"success": True}

    if action == "show_in_folder":
        file_path = existing_file(message["path"])
        subprocess.Popen(["explorer.exe", "/select,", str(file_path)])
        return {"success": True}

    return {
        "success": False,
        "error": "未対応の操作です"
    }


def main():
    # sendNativeMessage closes stdin after one request; connectNative keeps it
    # open. Support both without restarting the host for every popup action.
    while True:
        message = read_message()
        if message is None:
            return
        try:
            response = handle_message(message)
        except Exception as error:
            response = {"success": False, "error": str(error)}
        send_message(response)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        send_message({
            "success": False,
            "error": str(error)
        })
