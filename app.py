import json
import errno
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

MAX_MESSAGE_BYTES = 64 * 1024
MAX_PATH_LENGTH = 8192
HOST_VERSION = "1.0.1"
# About 15 seconds in total. Only Windows sharing/lock violations are retried.
LOCK_RETRY_DELAYS = (0.25, 0.5, 1, 1.5, 2, 2, 2, 2, 2, 2)


class IncompleteMoveError(Exception):
    def __init__(self, message, code, copied_path, copy_complete):
        super().__init__(message)
        self.code = code
        self.copied_path = str(copied_path)
        self.copy_complete = copy_complete


def retry_locked(operation):
    for delay in (*LOCK_RETRY_DELAYS, None):
        try:
            return operation()
        except OSError as error:
            if getattr(error, "winerror", None) not in (32, 33) or delay is None:
                raise
            time.sleep(delay)


def is_cross_device(error):
    return getattr(error, "winerror", None) == 17 or error.errno == errno.EXDEV


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
    if source.parent.resolve() == folder.resolve():
        return str(source)

    # shutil.move catches a failed rename (including WinError 32), copies the
    # file and then unlinks the source. That can leave TWO files even on C:.
    # A locked rename must NEVER turn into a copy. Copy only on EXDEV/Win17.
    for _ in range(1000):
        destination = create_unique_path(folder / source.name)

        def rename():
            # Windows os.rename refuses to overwrite an existing file. The
            # existence check also handles ordinary collisions in Linux tests.
            if destination.exists():
                raise FileExistsError(str(destination))
            os.rename(str(source), str(destination))

        try:
            retry_locked(rename)
            return str(destination)
        except FileExistsError:
            continue
        except OSError as error:
            if is_cross_device(error):
                return copy_across_devices(source, folder)
            if getattr(error, "winerror", None) in (32, 33):
                raise PermissionError(
                    "ファイルが使用中のため移動できませんでした。ロック解除を約15秒待ちましたが、"
                    "元ファイルは残しています。アプリのプレビュー等を閉じて確認してください。"
                ) from error
            raise
    raise RuntimeError("空いている移動先ファイル名を確保できませんでした")


def copy_across_devices(source, folder):
    # Opening the source may itself be locked. Do not reserve a destination
    # until the source is readable. 'xb' prevents overwriting someone else's file.
    with retry_locked(lambda: source.open("rb")) as input_file:
        for _ in range(1000):
            destination = create_unique_path(folder / source.name)
            try:
                output_file = destination.open("xb")
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("空いている移動先ファイル名を確保できませんでした")
        try:
            with output_file:
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                output_file.flush()
                os.fsync(output_file.fileno())
            shutil.copystat(str(source), str(destination))
        except Exception as error:
            # Preserve the source and expose the incomplete copy. Never retry
            # by creating more destination copies or silently delete user data.
            raise IncompleteMoveError(
                "別ドライブへのコピーを完了できませんでした。元ファイルを残しています。"
                "移動先には途中のコピーが残っている可能性があります。",
                "COPY_INCOMPLETE", destination, False
            ) from error
    try:
        retry_locked(lambda: source.unlink())
    except OSError as error:
        raise IncompleteMoveError(
            "別ドライブへのコピーは完了しましたが、元ファイルを削除できないため移動は未完了です。"
            "元とコピーの両方を残しています。ロックが続く場合は使用中のアプリを確認してください。",
            "SOURCE_DELETE_FAILED", destination, True
        ) from error
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
            "hostVersion": HOST_VERSION,
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
        except IncompleteMoveError as error:
            response = {"success": False, "error": str(error), "code": error.code,
                        "copiedPath": error.copied_path, "copyComplete": error.copy_complete}
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
