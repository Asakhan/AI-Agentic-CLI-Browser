# AI Agentic CLI Browser — Copyright (C) 2026 Thomas Moon
# Licensed under the GNU Affero General Public License v3.0 (see LICENSE).
"""page_extractor.py — HTML 정제·압축 모듈 (토큰 최소화에 최적화).

원본 HTML은 토큰이 너무 많아 그대로 LLM에 넘기면 비효율적이다. 이 모듈은
불필요한 태그(script/style/svg 등)를 제거하고, Claude가 "다음 행동"을 판단하는 데
꼭 필요한 정보(본문 텍스트 / 클릭 가능한 링크 / 입력 폼)만 추려 **공격적으로**
압축한다.

토큰 절약 전략:
  - 본문 텍스트 예산을 작게 잡고(_MAX_TEXT_CHARS) 잘라낸다.
  - 링크는 사용자 목표(goal)와의 키워드 겹침으로 점수를 매겨, 관련 높은 것만
    소수(_MAX_LINKS)로 추린다. 목표 달성에 무관한 수십 개 링크를 LLM에 안 보낸다.
  - 구조 태그(nav/header)는 링크 추출에는 남기되 본문에선 제외한다.

핵심 산출물은 :class:`ExtractedPage` 객체이며, ``to_prompt()`` 로 LLM 친화적인
텍스트 표현을 만든다. 링크와 폼에는 인덱스를 붙여 Claude가 간단히 지목할 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urldefrag

from bs4 import BeautifulSoup

# LLM에 넘기지 않을(=의미 없는) 노이즈 태그들.
# 주의: nav/header/footer/aside 는 링크가 많으므로 여기서 제거하지 않는다
#       (본문 추출 단계에서 main 컨테이너 우선으로 자연히 배제됨).
_DROP_TAGS = (
    "script", "style", "svg", "noscript", "iframe", "canvas",
    "template", "link", "meta", "head",
)

# 본문으로 간주할 후보 컨테이너 (readability 류 단순 휴리스틱)
_MAIN_CANDIDATES = ("main", "article", '[role="main"]', "#content", "#main")

# 프롬프트로 넘길 한도 (토큰 최소화: 기본값을 공격적으로 낮춤)
_MAX_TEXT_CHARS = 2500
_MAX_LINKS = 25
_MAX_FORMS = 5

# 불용어: 링크 점수 계산 시 무시할 흔한 단어
_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "find",
    "에서", "의", "을", "를", "이", "가", "에", "는", "은", "찾아", "찾기", "알려줘",
}


@dataclass
class Link:
    """클릭/이동 가능한 링크 한 개."""

    index: int
    text: str
    href: str  # 절대 URL


@dataclass
class FormField:
    """폼 입력 필드 한 개."""

    name: str
    field_type: str  # text, password, email, search, checkbox, ...
    label: str = ""


@dataclass
class Form:
    """입력 폼 한 개 (검색창·로그인 폼 등)."""

    index: int
    action: str
    method: str
    fields: list[FormField] = field(default_factory=list)


@dataclass
class ExtractedPage:
    """정제된 페이지 표현. agent 가 이 객체를 프롬프트 텍스트로 변환해 사용한다."""

    url: str
    title: str
    text: str
    links: list[Link] = field(default_factory=list)
    forms: list[Form] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Claude에 넘길 압축 텍스트 표현 생성."""
        parts: list[str] = []
        parts.append(f"현재 URL: {self.url}")
        parts.append(f"페이지 제목: {self.title or '(없음)'}")
        parts.append("\n=== 본문 텍스트 ===")
        parts.append(self.text or "(추출된 본문 없음)")

        if self.links:
            parts.append("\n=== 링크 목록 (link #인덱스 로 지목 가능) ===")
            for link in self.links:
                parts.append(f"[{link.index}] {link.text!r} -> {link.href}")

        if self.forms:
            parts.append("\n=== 폼 목록 (form #인덱스) ===")
            for form in self.forms:
                field_desc = ", ".join(
                    f"{f.name}({f.field_type}{', ' + f.label if f.label else ''})"
                    for f in form.fields
                ) or "(필드 없음)"
                parts.append(
                    f"[{form.index}] method={form.method} action={form.action}\n"
                    f"     필드: {field_desc}"
                )

        return "\n".join(parts)


def _clean_text(text: str) -> str:
    """공백·개행을 정규화해 토큰 낭비를 줄인다."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _extract_main_text(soup: BeautifulSoup) -> str:
    """본문 후보 컨테이너를 찾고, 없으면 body 전체에서 텍스트를 추출한다."""
    container = None
    for selector in _MAIN_CANDIDATES:
        found = soup.select_one(selector)
        if found and found.get_text(strip=True):
            container = found
            break
    if container is None:
        container = soup.body or soup

    text = _clean_text(container.get_text(separator="\n"))
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS] + "\n…(본문이 길어 일부 생략됨. 필요하면 scroll/read_more 사용)"
    return text


def _goal_terms(goal: str) -> set[str]:
    """목표 문자열을 점수 계산용 키워드 집합으로 변환."""
    terms = re.findall(r"[\w가-힣]+", (goal or "").lower())
    return {t for t in terms if len(t) >= 2 and t not in _STOPWORDS}


def _score_link(link_text: str, href: str, terms: set[str]) -> int:
    """링크가 목표와 얼마나 관련 있는지 점수화 (키워드 겹침 수)."""
    if not terms:
        return 0
    haystack = (link_text + " " + href).lower()
    return sum(1 for t in terms if t in haystack)


def _extract_links(soup: BeautifulSoup, base_url: str, goal: str = "") -> list[Link]:
    """앵커 태그에서 의미 있는 링크를 추출하되, 목표 관련도가 높은 순으로 추린다.

    목표(goal)가 주어지면 키워드 겹침 점수로 정렬해 상위 _MAX_LINKS 개만 남긴다.
    이렇게 하면 수십~수백 개 링크 대신 "목표 달성에 쓸모 있는" 소수만 LLM에
    전달되어 토큰을 크게 절약하고, 모델이 더 빠르게 올바른 링크를 고른다.
    """
    terms = _goal_terms(goal)
    candidates: list[tuple[int, int, Link]] = []  # (점수, 등장순서, Link)
    seen: set[tuple[str, str]] = set()
    order = 0
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        # 상대경로 -> 절대경로, 프래그먼트(#...) 제거로 중복 완화
        abs_url = urldefrag(urljoin(base_url, href))[0]
        text = _clean_text(anchor.get_text()) or anchor.get("aria-label", "") or abs_url
        text = text[:100]
        key = (text, abs_url)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((_score_link(text, abs_url, terms), order, Link(0, text, abs_url)))
        order += 1

    # 점수 내림차순, 동점이면 원래 등장 순서 유지
    candidates.sort(key=lambda c: (-c[0], c[1]))
    selected = candidates[:_MAX_LINKS]
    # 잘린 뒤에는 다시 등장 순서대로 보여줘 가독성 확보
    selected.sort(key=lambda c: c[1])

    return [Link(index=i, text=c[2].text, href=c[2].href) for i, c in enumerate(selected)]


def _label_for(soup: BeautifulSoup, element) -> str:
    """input 요소에 연결된 <label> 텍스트 또는 placeholder를 찾는다."""
    placeholder = element.get("placeholder")
    if placeholder:
        return placeholder.strip()[:60]
    elem_id = element.get("id")
    if elem_id:
        label = soup.find("label", attrs={"for": elem_id})
        if label:
            return _clean_text(label.get_text())[:60]
    return element.get("aria-label", "").strip()[:60]


def _extract_forms(soup: BeautifulSoup, base_url: str) -> list[Form]:
    """폼과 그 입력 필드를 추출한다 (검색·로그인 등)."""
    forms: list[Form] = []
    for form_el in soup.find_all("form"):
        action = urljoin(base_url, (form_el.get("action") or "").strip())
        method = (form_el.get("method") or "get").lower()
        fields: list[FormField] = []
        for el in form_el.find_all(("input", "textarea", "select")):
            field_type = el.get("type", el.name)  # textarea/select 는 태그명 사용
            if field_type in ("hidden", "submit", "button", "image", "reset"):
                continue
            name = el.get("name") or el.get("id") or ""
            if not name:
                continue
            fields.append(
                FormField(name=name, field_type=field_type, label=_label_for(soup, el))
            )
        if fields:
            forms.append(
                Form(index=len(forms), action=action, method=method, fields=fields)
            )
        if len(forms) >= _MAX_FORMS:
            break
    return forms


def extract(
    html: str, current_url: str, title: str = "", goal: str = ""
) -> ExtractedPage:
    """원본 HTML을 정제된 :class:`ExtractedPage` 로 변환한다.

    Args:
        html: 페이지의 전체 HTML 문자열.
        current_url: 상대 링크를 절대 URL로 만들기 위한 기준 URL.
        title: 페이지 제목 (Playwright 에서 얻은 값).
        goal: 사용자 목표. 주어지면 링크를 관련도 순으로 추려 토큰을 아낀다.

    Returns:
        텍스트·링크·폼이 추려진 :class:`ExtractedPage`.
    """
    soup = BeautifulSoup(html or "", "lxml")

    # 링크는 nav/header 등에도 있으므로, 노이즈 태그 제거 "전"에 먼저 추출한다.
    links = _extract_links(soup, current_url, goal)
    forms = _extract_forms(soup, current_url)

    # 본문 텍스트용으로만 노이즈 태그 제거 (토큰 절약 + 노이즈 감소)
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    page_title = title or (soup.title.get_text(strip=True) if soup.title else "")

    return ExtractedPage(
        url=current_url,
        title=page_title,
        text=_extract_main_text(soup),
        links=links,
        forms=forms,
    )
