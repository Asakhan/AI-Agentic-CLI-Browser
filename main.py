# AI Agentic CLI Browser — Copyright (C) 2026 Thomas Moon
# Licensed under the GNU Affero General Public License v3.0 (see LICENSE).
"""main.py — AI Agentic CLI Browser 진입점.

실행 흐름:
  1. .env 에서 설정 로드 (ANTHROPIC_API_KEY 필수)
  2. 접속할 URL 입력받기
  3. "이 사이트에서 무엇을 찾고 싶은지"(목표) 입력받기
  4. agentic loop: 페이지 정제 → Claude가 action 결정 → 실행 → 반복
     - 최대 스텝 수 제한 / 방문 URL 중복 방지
     - ask_user 시 getpass 로 비밀번호를 가려 입력받아 메모리에만 보관

사용법:  python main.py
"""

from __future__ import annotations

import getpass
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

import anthropic

from agent import Agent, AgentAction
from browser import Browser, BrowserError, CredentialStore
from page_extractor import extract

console = Console()


def _load_config() -> dict:
    """.env 와 환경 변수에서 설정을 읽고 검증한다."""
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        console.print(
            Panel(
                "[bold red]ANTHROPIC_API_KEY 가 설정되지 않았습니다.[/]\n\n"
                "다음 중 하나로 해결하세요:\n"
                "  1) [cyan]python setup_env.py[/] 실행 (대화형으로 .env 생성)\n"
                "  2) .env 파일에 ANTHROPIC_API_KEY=... 직접 추가\n"
                "  3) 환경 변수로 export ANTHROPIC_API_KEY=...",
                title="설정 오류",
                border_style="red",
            )
        )
        sys.exit(1)

    def _bool(name: str, default: bool) -> bool:
        val = os.getenv(name)
        if val is None:
            return default
        return val.strip().lower() in ("1", "true", "yes", "on")

    return {
        "api_key": api_key,
        "model": os.getenv("AI_BROWSER_MODEL", "claude-sonnet-4-6").strip(),
        "download_dir": os.getenv("AI_BROWSER_DOWNLOAD_DIR", "./downloads").strip(),
        "headless": _bool("AI_BROWSER_HEADLESS", True),
        "max_steps": int(os.getenv("AI_BROWSER_MAX_STEPS", "25")),
        # Lightpanda 등 CDP 엔드포인트. 지정 시 해당 서버에 접속(권장, 경량·고속).
        # 미지정 시 로컬 Chromium 폴백.
        "cdp_url": os.getenv("AI_BROWSER_CDP_URL", "").strip() or None,
    }


def _normalize_url(url: str) -> str:
    """스킴이 없으면 https:// 를 붙인다."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _handle_ask_user(action: AgentAction, credentials: CredentialStore) -> str:
    """ask_user action 처리: 사용자에게 입력을 받고 자격증명은 보관소에 저장.

    Claude 에는 실제 값이 아니라 어떤 키가 저장됐는지만 알린다.
    """
    question = action.input.get("question", "추가 정보가 필요합니다.")
    console.print(Panel(question, title="🙋 에이전트의 질문", border_style="yellow"))

    secret_keys = action.input.get("secret_keys") or []
    if secret_keys:
        stored: list[str] = []
        for key in secret_keys:
            is_password = "pass" in key.lower() or "pwd" in key.lower()
            if is_password:
                value = getpass.getpass(f"  {key} (입력 숨김): ")
            else:
                value = Prompt.ask(f"  {key}")
            credentials.set(key, value)
            stored.append(key)
        # Claude 에는 평문 없이 플레이스홀더 키만 전달
        placeholders = ", ".join(f"{{{{{k}}}}}" for k in stored)
        return (
            f"사용자가 자격증명을 입력했고 안전하게 보관했습니다. "
            f"이후 fill_form 의 value 에 다음 플레이스홀더를 사용하세요: {placeholders}"
        )

    # 일반 질문
    answer = Prompt.ask("  답변")
    return f"사용자 답변: {answer}"


def _execute_action(
    action: AgentAction,
    browser: Browser,
    credentials: CredentialStore,
) -> str:
    """결정된 action을 실제로 실행하고 결과 문자열을 반환한다."""
    name, params = action.name, action.input

    if name == "navigate":
        return browser.navigate(params["url"])
    if name == "click":
        return browser.click(params["target"])
    if name == "fill_form":
        return browser.fill_form(params.get("fields", []), params.get("submit", False))
    if name == "download":
        return browser.download(params["target"])
    if name == "scroll":
        return browser.scroll(params.get("direction", "down"))
    if name == "ask_user":
        return _handle_ask_user(action, credentials)
    # finish 는 루프에서 별도 처리
    return f"알 수 없는 action: {name}"


def _print_action(step: int, action: AgentAction) -> None:
    """현재 무슨 행동을 하는지 간결히 출력."""
    detail = {
        "navigate": lambda p: p.get("url", ""),
        "click": lambda p: p.get("target", ""),
        "fill_form": lambda p: f"{len(p.get('fields', []))}개 필드, submit={p.get('submit', False)}",
        "download": lambda p: p.get("target", ""),
        "scroll": lambda p: p.get("direction", "down"),
        "ask_user": lambda p: p.get("question", ""),
        "finish": lambda p: "",
    }.get(action.name, lambda p: "")(action.input)

    reason = f"  ({action.reason})" if action.reason else ""
    console.print(
        f"[bold cyan]스텝 {step}[/] · [bold]{action.name}[/] [dim]{detail}[/]{reason}"
    )


def run() -> None:
    """대화형 세션 실행."""
    config = _load_config()

    console.print(
        Panel(
            "[bold]AI Agentic CLI Browser[/]\n"
            "AI(Claude)가 대신 웹사이트를 탐색해 원하는 자료를 찾아주고 파일을 받아줍니다.",
            border_style="green",
        )
    )

    # 1) URL 입력
    start_url = _normalize_url(Prompt.ask("[bold]접속할 웹사이트 URL[/]"))
    # 2) 목표 입력
    goal = Prompt.ask("[bold]이 사이트에서 무엇을 찾고 싶으신가요?[/]")

    credentials = CredentialStore()
    visited: set[str] = set()
    history: list[str] = []

    try:
        with Browser(
            credentials=credentials,
            headless=config["headless"],
            download_dir=config["download_dir"],
            cdp_url=config["cdp_url"],
        ) as browser:
            agent = Agent(api_key=config["api_key"], model=config["model"])

            console.print(f"[dim]백엔드: {browser.backend}[/]")
            console.print(f"\n[dim]'{start_url}' 로 이동 중…[/]")
            console.print(browser.navigate(start_url))

            # 3) agentic loop
            for step in range(1, config["max_steps"] + 1):
                # 현재 페이지 정제
                try:
                    html, url, title = browser.content()
                except BrowserError as exc:
                    console.print(f"[red]{exc}[/]")
                    break
                # goal을 넘겨 링크를 관련도 순으로 추려 토큰을 아낀다.
                page = extract(html, url, title, goal=goal)
                visited.add(url)

                # Claude에 다음 action 요청
                try:
                    action = agent.decide(goal, page.to_prompt(), history)
                except anthropic.APIError as exc:
                    console.print(
                        Panel(
                            f"Claude API 호출 실패: {exc}\n"
                            "네트워크/요금/키 권한을 확인하세요.",
                            title="API 오류",
                            border_style="red",
                        )
                    )
                    break

                _print_action(step, action)

                # finish 처리
                if action.name == "finish":
                    summary = action.input.get("summary", "(요약 없음)")
                    console.print(
                        Panel(summary, title="✅ 완료", border_style="green")
                    )
                    return

                # 무한 루프 방지: navigate 가 이미 방문한 URL이면 경고를 히스토리에 남김
                if action.name == "navigate" and action.input.get("url") in visited:
                    history.append(
                        f"[경고] 이미 방문한 URL로 다시 이동하려 함: {action.input.get('url')}"
                    )

                # action 실행
                try:
                    result = _execute_action(action, browser, credentials)
                    console.print(f"  [green]→[/] {result}")
                    history.append(f"{action.name}: {result}")
                except BrowserError as exc:
                    console.print(f"  [red]→ 오류:[/] {exc}")
                    history.append(f"{action.name} 실패: {exc}")

            else:
                # for 루프가 break 없이 끝난 경우 (최대 스텝 도달)
                console.print(
                    Panel(
                        f"최대 스텝({config['max_steps']})에 도달해 탐색을 중단했습니다.",
                        title="⏹ 중단",
                        border_style="yellow",
                    )
                )

    except BrowserError as exc:
        console.print(Panel(str(exc), title="브라우저 오류", border_style="red"))
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]사용자가 중단했습니다.[/]")
    finally:
        # 자격증명은 프로세스 종료와 함께 메모리에서 사라짐 (디스크 저장 없음)
        pass


if __name__ == "__main__":
    run()
