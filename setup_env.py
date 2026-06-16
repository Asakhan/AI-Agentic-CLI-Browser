# AI Agentic CLI Browser — Copyright (C) 2026 Thomas Moon
# Licensed under the GNU Affero General Public License v3.0 (see LICENSE).
"""setup_env.py — 설치 단계 대화형 환경 설정 스크립트.

실행하면 ANTHROPIC_API_KEY 를 물어보고 .env 파일을 생성한다. 모델/다운로드
경로/헤드리스 여부 등도 선택적으로 설정할 수 있다.

사용법:  python setup_env.py
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

ENV_PATH = Path(".env")


def _confirm_overwrite() -> bool:
    if not ENV_PATH.exists():
        return True
    answer = input(".env 파일이 이미 존재합니다. 덮어쓸까요? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def main() -> None:
    print("=" * 60)
    print(" AI Agentic CLI Browser — 환경 설정")
    print("=" * 60)

    if not _confirm_overwrite():
        print("취소했습니다. 기존 .env 를 유지합니다.")
        return

    # API 키는 가려 입력받는다 (화면 노출 방지)
    api_key = getpass.getpass("Anthropic API 키를 입력하세요 (입력 숨김): ").strip()
    if not api_key:
        print("API 키가 비어 있습니다. 설정을 중단합니다.", file=sys.stderr)
        sys.exit(1)
    if not api_key.startswith("sk-ant-"):
        print("경고: 일반적인 Anthropic 키는 'sk-ant-' 로 시작합니다. 입력값을 다시 확인하세요.")

    model = input("모델 [claude-sonnet-4-6]: ").strip() or "claude-sonnet-4-6"
    download_dir = input("다운로드 디렉터리 [./downloads]: ").strip() or "./downloads"
    headless_in = input("헤드리스(브라우저 창 숨김) 모드 사용? [Y/n]: ").strip().lower()
    headless = "false" if headless_in in ("n", "no") else "true"

    print(
        "\nLightpanda(경량·고속 CDP 브라우저)를 쓰면 메모리·속도가 크게 개선됩니다."
        "\n  먼저: lightpanda serve --host 127.0.0.1 --port 9222"
    )
    cdp_in = input(
        "Lightpanda CDP 주소 (Enter=로컬 Chromium 사용) [ws://127.0.0.1:9222]?: "
    ).strip()
    if cdp_in.lower() in ("y", "yes"):
        cdp_in = "ws://127.0.0.1:9222"

    lines = [
        "# AI Agentic CLI Browser — setup_env.py 로 생성됨",
        f"ANTHROPIC_API_KEY={api_key}",
        f"AI_BROWSER_MODEL={model}",
        f"AI_BROWSER_DOWNLOAD_DIR={download_dir}",
        f"AI_BROWSER_HEADLESS={headless}",
        "AI_BROWSER_MAX_STEPS=25",
    ]
    if cdp_in:
        lines.append(f"AI_BROWSER_CDP_URL={cdp_in}")
    else:
        lines.append("# AI_BROWSER_CDP_URL=ws://127.0.0.1:9222")
    lines.append("")
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")

    # 키가 평문으로 들어가므로 소유자만 읽도록 권한 축소 (가능한 OS에서)
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass

    print(f"\n✅ {ENV_PATH.resolve()} 파일을 생성했습니다.")
    print("   (.env 는 .gitignore 에 포함되어 커밋되지 않습니다.)")
    print("\n다음 단계:")
    if cdp_in:
        print(f"   1) lightpanda serve --host 127.0.0.1 --port 9222   # CDP 서버 먼저 실행")
        print("   2) python main.py       # 실행")
    else:
        print("   1) playwright install   # 로컬 Chromium 바이너리 설치 (최초 1회)")
        print("   2) python main.py       # 실행")


if __name__ == "__main__":
    main()
