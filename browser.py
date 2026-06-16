# AI Agentic CLI Browser — Copyright (C) 2026 Thomas Moon
# Licensed under the GNU Affero General Public License v3.0 (see LICENSE).
"""browser.py — Playwright 기반 브라우저 자동화 래퍼.

agent 가 결정한 추상 action(navigate/click/fill_form/download/scroll)을 실제
브라우저 조작으로 변환한다.

백엔드 두 가지를 지원한다:
  1) **Lightpanda** (권장, 기본): Zig로 작성된 초경량 헤드리스 브라우저로 CDP
     (Chrome DevTools Protocol)를 노출한다. Chrome 대비 메모리 ~16배 적고
     ~9배 빠르며, 자료를 더 빠르게 찾는 데 유리하다.
     먼저 서버를 띄워 두어야 한다:
         lightpanda serve --host 127.0.0.1 --port 9222
     그 뒤 ``cdp_url="ws://127.0.0.1:9222"`` 로 ``connect_over_cdp`` 접속한다.
  2) **로컬 Chromium** (cdp_url 미지정 시 폴백): Playwright가 내장 Chromium을
     직접 띄운다. JS 호환성이 가장 넓지만 무겁다.

보안 설계: 자격증명(아이디/비밀번호)은 절대 이 모듈이나 로그에 평문으로 남기지
않는다. ``fill_form`` 의 값에 ``{{username}}`` / ``{{password}}`` 같은
플레이스홀더가 오면, 생성 시 주입된 :class:`CredentialStore` 에서 실제 값으로
치환해 폼에만 입력한다. 치환된 실제 값은 반환 메시지에도 노출하지 않는다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.sync_api import (
    Browser as PlaywrightBrowser,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

# fill_form 값에서 치환할 플레이스홀더 패턴 ({{key}})
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

_DEFAULT_TIMEOUT_MS = 20_000


class BrowserError(Exception):
    """브라우저 조작 중 발생한 (사용자에게 보여줄) 오류."""


class CredentialStore:
    """세션 동안만 메모리에 자격증명을 보관하는 보관소.

    값은 디스크에 절대 기록되지 않으며, 프로세스 종료 시 사라진다. Claude 에는
    실제 값이 아니라 ``{{key}}`` 플레이스홀더만 노출된다.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def has(self, key: str) -> bool:
        return key in self._store

    def resolve(self, value: str) -> str:
        """문자열 안의 ``{{key}}`` 플레이스홀더를 실제 값으로 치환."""
        def _sub(match: re.Match) -> str:
            key = match.group(1)
            return self._store.get(key, match.group(0))

        return _PLACEHOLDER_RE.sub(_sub, value)

    @staticmethod
    def contains_placeholder(value: str) -> bool:
        return bool(_PLACEHOLDER_RE.search(value))


class Browser:
    """Playwright 동기 API를 감싼 고수준 브라우저 컨트롤러."""

    def __init__(
        self,
        credentials: CredentialStore,
        headless: bool = True,
        download_dir: str = "./downloads",
        cdp_url: str | None = None,
    ) -> None:
        self._credentials = credentials
        self._headless = headless
        self._download_dir = Path(download_dir)
        self._download_dir.mkdir(parents=True, exist_ok=True)
        self._cdp_url = (cdp_url or "").strip() or None

        self._playwright = None
        self._browser: PlaywrightBrowser | None = None
        self._page: Page | None = None

    @property
    def backend(self) -> str:
        """현재 사용 중인 백엔드 이름 (표시용)."""
        return f"Lightpanda (CDP {self._cdp_url})" if self._cdp_url else "로컬 Chromium"

    # --- 수명주기 ---------------------------------------------------------

    def start(self) -> None:
        """Playwright 와 브라우저를 기동한다 (CDP 접속 또는 로컬 실행)."""
        try:
            self._playwright = sync_playwright().start()
            if self._cdp_url:
                self._start_cdp()
            else:
                self._start_local()
        except BrowserError:
            raise
        except PlaywrightError as exc:
            hint = (
                "Lightpanda 서버에 연결하지 못했습니다. 서버가 실행 중인지 확인하세요.\n"
                "  실행: lightpanda serve --host 127.0.0.1 --port 9222\n"
                if self._cdp_url
                else "브라우저를 시작하지 못했습니다. Playwright 브라우저가 설치돼 있나요?\n"
                "  해결: 터미널에서 `playwright install` 을 실행하세요.\n"
            )
            raise BrowserError(hint + f"  원본 오류: {exc}") from exc

    def _start_cdp(self) -> None:
        """Lightpanda 등 CDP 서버에 접속한다."""
        self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_url)
        # CDP 접속 시 컨텍스트/페이지가 이미 존재할 수 있으므로 재사용
        context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else self._browser.new_context(accept_downloads=True)
        )
        try:
            context.set_default_timeout(_DEFAULT_TIMEOUT_MS)
        except PlaywrightError:
            pass  # 일부 CDP 구현은 미지원
        # Lightpanda 가 CDP 접속 시 미리 만들어 두는 초기 페이지(about:blank)는
        # 재사용하면 goto() 가 domcontentloaded 에 도달하지 못하고 멈춘다(타임아웃).
        # 따라서 그 초기 페이지는 쓰지 않고 항상 새 페이지를 연다.
        self._page = context.new_page()

    def _start_local(self) -> None:
        """Playwright 내장 Chromium을 직접 띄운다 (폴백)."""
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        context = self._browser.new_context(accept_downloads=True)
        context.set_default_timeout(_DEFAULT_TIMEOUT_MS)
        self._page = context.new_page()

    def close(self) -> None:
        """리소스 정리 (예외는 무시)."""
        for closer in (
            lambda: self._browser and self._browser.close(),
            lambda: self._playwright and self._playwright.stop(),
        ):
            try:
                closer()
            except Exception:
                pass

    def __enter__(self) -> "Browser":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- 내부 헬퍼 --------------------------------------------------------

    @property
    def page(self) -> Page:
        if self._page is None:
            raise BrowserError("브라우저가 아직 시작되지 않았습니다 (start() 호출 필요).")
        return self._page

    @property
    def current_url(self) -> str:
        return self.page.url

    def content(self) -> tuple[str, str, str]:
        """현재 페이지의 (html, url, title) 을 반환한다."""
        page = self.page
        try:
            return page.content(), page.url, page.title()
        except PlaywrightError as exc:
            raise BrowserError(f"페이지 내용을 읽지 못했습니다: {exc}") from exc

    def _wait_dom_ready(self, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> None:
        """DOM 이 준비될(readyState=interactive/complete) 때까지 대기한다.

        Lightpanda 같은 일부 CDP 백엔드는 ``domcontentloaded``/``load``
        라이프사이클 이벤트를 누락하는 경우가 있어, Playwright 의
        ``wait_until="domcontentloaded"`` 가 콘텐츠가 다 와도 시간 초과로
        끝나곤 한다. 그래서 이벤트 대신 ``document.readyState`` 를 직접
        확인해 준비 여부를 판단한다. 시간 초과 시 PlaywrightTimeoutError 전파.
        """
        self.page.wait_for_function(
            "document.readyState === 'interactive' "
            "|| document.readyState === 'complete'",
            timeout=timeout_ms,
        )

    def _wait_settled(self) -> None:
        """네트워크가 잠잠해질 때까지 잠깐 대기 (실패해도 치명적이지 않음)."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=8_000)
        except PlaywrightTimeoutError:
            pass

    # --- action 구현 ------------------------------------------------------

    def navigate(self, url: str) -> str:
        """지정 URL로 이동한다."""
        try:
            # 네비게이션 시작은 commit 으로 기다린다. domcontentloaded/load
            # 이벤트는 Lightpanda 등에서 누락될 수 있어 신뢰하지 않고,
            # DOM 준비는 readyState 로 직접 확인한다(_wait_dom_ready).
            self.page.goto(url, wait_until="commit")
            self._wait_dom_ready()
            self._wait_settled()
            return f"'{url}' 로 이동했습니다. (현재: {self.page.url})"
        except PlaywrightTimeoutError:
            raise BrowserError(f"'{url}' 로딩이 시간 초과되었습니다.")
        except PlaywrightError as exc:
            raise BrowserError(f"'{url}' 이동 실패: {exc}") from exc

    def click(self, target: str) -> str:
        """요소를 클릭한다. target 은 CSS selector 또는 링크 텍스트.

        먼저 selector 로 시도하고, 실패하면 보이는 텍스트로 시도한다.
        """
        page = self.page
        last_error: Exception | None = None

        # 1) CSS selector 로 시도
        try:
            page.click(target, timeout=8_000)
            self._wait_settled()
            return f"요소를 클릭했습니다: {target} (현재: {page.url})"
        except PlaywrightError as exc:
            last_error = exc

        # 2) 보이는 텍스트(get_by_text)로 시도
        try:
            page.get_by_text(target, exact=False).first.click(timeout=8_000)
            self._wait_settled()
            return f"'{target}' 텍스트 요소를 클릭했습니다. (현재: {page.url})"
        except PlaywrightError as exc:
            last_error = exc

        raise BrowserError(f"클릭 대상을 찾지 못했습니다: {target!r} ({last_error})")

    def fill_form(self, fields: list[dict], submit: bool = False) -> str:
        """폼 필드들을 채운다. fields = [{selector, value}, ...].

        value 의 ``{{key}}`` 플레이스홀더는 자격증명 보관소 값으로 치환된다.
        비밀번호 등 민감 값이 입력됐는지 여부만 반환 메시지에 남기고, 실제
        값은 절대 노출하지 않는다.
        """
        page = self.page
        filled_report: list[str] = []
        for spec in fields:
            selector = (spec.get("selector") or "").strip()
            raw_value = spec.get("value", "")
            if not selector:
                continue

            had_secret = CredentialStore.contains_placeholder(raw_value)
            value = self._credentials.resolve(raw_value)

            try:
                page.fill(selector, value, timeout=8_000)
            except PlaywrightError as exc:
                raise BrowserError(f"필드 입력 실패 ({selector}): {exc}") from exc

            # 로그/프롬프트에는 마스킹된 정보만 남긴다
            shown = "******(자격증명)" if had_secret else (value[:40] if value else "(빈 값)")
            filled_report.append(f"{selector} <- {shown}")

        message = "폼 입력 완료: " + "; ".join(filled_report) if filled_report else "입력한 필드가 없습니다."

        if submit:
            try:
                # Enter 키 제출이 가장 범용적; 마지막 필드에서 Enter
                if fields:
                    page.press(fields[-1]["selector"], "Enter")
                else:
                    page.keyboard.press("Enter")
                self._wait_settled()
                message += f" / 폼을 제출했습니다. (현재: {page.url})"
            except PlaywrightError as exc:
                raise BrowserError(f"폼 제출 실패: {exc}") from exc

        return message

    def scroll(self, direction: str = "down") -> str:
        """페이지를 스크롤해 지연 로딩 콘텐츠를 불러온다."""
        page = self.page
        delta = -1200 if direction == "up" else 1200
        try:
            page.mouse.wheel(0, delta)
            self._wait_settled()
            return f"{direction} 방향으로 스크롤했습니다."
        except PlaywrightError as exc:
            raise BrowserError(f"스크롤 실패: {exc}") from exc

    def download(self, target: str) -> str:
        """파일을 다운로드해 download_dir 에 저장하고 저장 경로를 반환한다.

        target 이 http(s) URL 이면 직접 요청해 받고, 아니면 해당 selector/링크를
        클릭해 발생하는 다운로드를 가로챈다.
        """
        page = self.page

        # 1) URL 직접 다운로드
        if target.startswith(("http://", "https://")):
            try:
                response = page.request.get(target)
                if not response.ok:
                    raise BrowserError(
                        f"다운로드 실패 (HTTP {response.status}): {target}"
                    )
                filename = self._filename_from_url(target)
                dest = self._unique_path(filename)
                dest.write_bytes(response.body())
                return f"파일을 저장했습니다: {dest.resolve()}"
            except PlaywrightError as exc:
                raise BrowserError(f"다운로드 실패: {exc}") from exc

        # 2) 클릭으로 트리거되는 다운로드
        try:
            with page.expect_download(timeout=30_000) as download_info:
                try:
                    page.click(target, timeout=8_000)
                except PlaywrightError:
                    page.get_by_text(target, exact=False).first.click(timeout=8_000)
            download = download_info.value
            dest = self._unique_path(download.suggested_filename or "download.bin")
            download.save_as(str(dest))
            return f"파일을 저장했습니다: {dest.resolve()}"
        except PlaywrightTimeoutError:
            raise BrowserError(
                f"다운로드가 시작되지 않았습니다 (대상: {target!r}). "
                "링크가 실제 다운로드를 유발하는지 확인하세요."
            )
        except PlaywrightError as exc:
            raise BrowserError(f"다운로드 실패: {exc}") from exc

    # --- 파일명 유틸 ------------------------------------------------------

    @staticmethod
    def _filename_from_url(url: str) -> str:
        name = os.path.basename(url.split("?")[0].rstrip("/")) or "download.bin"
        return name

    def _unique_path(self, filename: str) -> Path:
        """중복 파일명 충돌을 피해 고유 경로를 만든다."""
        # 경로 조작 방지: 디렉터리 성분 제거
        safe = os.path.basename(filename) or "download.bin"
        dest = self._download_dir / safe
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = self._download_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        return dest
