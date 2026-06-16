# AI Agentic CLI Browser — Copyright (C) 2026 Thomas Moon
# Licensed under the GNU Affero General Public License v3.0 (see LICENSE).
"""agent.py — Claude 호출 및 다음 action 결정 로직.

agentic loop의 "두뇌"에 해당한다. 현재 페이지의 정제된 상태와 사용자의 목표를
Claude에 전달하고, 미리 정의된 **tool(action) 세트** 중 하나를 골라 호출하게 한다.
``tool_choice={"type": "any"}`` 로 매 스텝 정확히 하나의 action을 강제하므로,
자유 형식 JSON 파싱보다 안정적이다.

반환값은 :class:`AgentAction` (name + input dict)이며, main 의 루프가 이를
browser 조작으로 실행한다.

보안: 페이지 상태에는 비밀번호가 포함되지 않으며, 자격증명도 프롬프트에
들어가지 않는다. Claude 는 ``fill_form`` 에서 ``{{username}}``/``{{password}}``
플레이스홀더만 사용하도록 안내받는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic

DEFAULT_MODEL = "claude-sonnet-4-6"

# Claude에 노출할 action(tool) 정의. 각 tool 의 input_schema 가 곧 action 인자다.
TOOLS: list[dict] = [
    {
        "name": "navigate",
        "description": "특정 URL로 직접 이동한다. 링크 목록의 절대 URL이나 알려진 주소로 이동할 때 사용.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "이동할 절대 URL"},
                "reason": {"type": "string", "description": "이 행동을 하는 이유(한국어, 한 문장)"},
            },
            "required": ["url", "reason"],
        },
    },
    {
        "name": "click",
        "description": "페이지의 요소를 클릭한다. CSS selector 또는 링크의 보이는 텍스트를 지정.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "CSS selector 또는 클릭할 링크/버튼의 텍스트",
                },
                "reason": {"type": "string", "description": "이 행동을 하는 이유(한국어, 한 문장)"},
            },
            "required": ["target", "reason"],
        },
    },
    {
        "name": "fill_form",
        "description": (
            "검색창·로그인 폼 등 입력 필드를 채운다. 필요하면 submit=true로 제출까지 한다. "
            "비밀번호/아이디는 실제 값 대신 반드시 플레이스홀더 {{username}}, {{password}} 를 "
            "value 에 사용한다(실제 자격증명은 시스템이 안전하게 치환함)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "description": "채울 필드 목록",
                    "items": {
                        "type": "object",
                        "properties": {
                            "selector": {"type": "string", "description": "필드의 CSS selector (예: input[name='q'])"},
                            "value": {"type": "string", "description": "입력 값. 자격증명은 {{username}}/{{password}} 사용"},
                        },
                        "required": ["selector", "value"],
                    },
                },
                "submit": {"type": "boolean", "description": "입력 후 폼을 제출할지 여부"},
                "reason": {"type": "string", "description": "이 행동을 하는 이유(한국어, 한 문장)"},
            },
            "required": ["fields", "reason"],
        },
    },
    {
        "name": "download",
        "description": "파일을 다운로드한다. 파일의 직접 URL 또는 다운로드를 유발하는 링크/버튼의 selector·텍스트를 지정.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "파일 URL 또는 다운로드 링크의 selector/텍스트"},
                "reason": {"type": "string", "description": "이 행동을 하는 이유(한국어, 한 문장)"},
            },
            "required": ["target", "reason"],
        },
    },
    {
        "name": "scroll",
        "description": "페이지를 스크롤해 추가 콘텐츠(지연 로딩)를 불러온다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["down", "up"], "description": "스크롤 방향"},
                "reason": {"type": "string", "description": "이 행동을 하는 이유(한국어, 한 문장)"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "탐색을 계속하려면 사용자에게 추가 정보가 필요할 때 질문한다. "
            "로그인 자격증명이 필요하면 secret 항목에 'username'/'password' 같은 키를 지정한다 "
            "(비밀번호는 터미널에서 가려진 입력으로 받고, 시스템이 안전하게 보관함)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "사용자에게 보여줄 질문(한국어)"},
                "secret_keys": {
                    "type": "array",
                    "description": "받아야 할 민감/일반 입력 키 목록. 예: ['username', 'password']",
                    "items": {"type": "string"},
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "finish",
        "description": "목표를 달성했거나 더 진행할 수 없을 때 호출한다. 결과를 요약한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "사용자에게 전달할 결과 요약(한국어)"},
            },
            "required": ["summary"],
        },
    },
]

_SYSTEM_PROMPT = """\
당신은 사용자를 대신해 웹을 탐색하는 자율 에이전트입니다. 목표 달성을 위해
매 턴 정확히 하나의 tool을 호출합니다. **최소 스텝으로 가장 빠르게** 목표에 도달하세요.

효율 규칙(중요):
- 페이지 본문·링크는 토큰 절약을 위해 잘려 있습니다. 보이는 정보로 즉시 판단하세요.
- 목표와 관련된 링크가 보이면 망설이지 말고 navigate 로 바로 이동합니다.
- 파일 다운로드가 목표면, 파일/다운로드 링크가 보이는 즉시 download 를 호출합니다.
  탐색용 scroll·click 을 남발하지 말고 직행하세요.
- 검색이 빠른 길이면 검색 폼을 fill_form + submit 로 한 번에 처리합니다.
- 같은 URL 재방문이나 무의미한 반복 금지. 진전이 없으면 다른 경로를 택합니다.
- 목표 달성/불가 시 즉시 finish 로 결과를 요약합니다.

보안 규칙:
- 로그인 폼은 먼저 ask_user(secret_keys=["username","password"])로 자격증명을 요청한 뒤,
  fill_form value 에 실제 값이 아닌 플레이스홀더 {{username}}, {{password}} 만 사용합니다.

reason 필드는 한국어 한 문장으로 짧게 적습니다.
"""


@dataclass
class AgentAction:
    """Claude가 선택한 하나의 action."""

    name: str
    input: dict
    tool_use_id: str
    reason: str = ""


class Agent:
    """Claude API를 호출해 다음 action을 결정하는 에이전트."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def decide(self, goal: str, page_prompt: str, history: list[str]) -> AgentAction:
        """현재 페이지 상태와 목표를 바탕으로 다음 action을 결정한다.

        Args:
            goal: 사용자가 입력한 자연어 목표.
            page_prompt: page_extractor 가 만든 정제된 페이지 텍스트.
            history: 지금까지 수행한 action 요약 리스트 (최근 것 위주).

        Returns:
            선택된 :class:`AgentAction`.

        Raises:
            anthropic.APIError 계열: API 호출 실패 시.
            RuntimeError: 응답에 tool 호출이 없을 때.
        """
        # 히스토리는 최근 8개만, 각 항목도 짧게 잘라 토큰을 아낀다.
        history_text = (
            "\n".join(f"- {h[:160]}" for h in history[-8:]) or "(아직 없음)"
        )
        user_content = (
            f"[목표]\n{goal}\n\n"
            f"[최근 행동]\n{history_text}\n\n"
            f"[현재 페이지]\n{page_prompt}\n\n"
            "목표 달성을 위한 다음 행동 하나를 tool로 호출하세요."
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=768,  # 토큰 절약: action 1개에 충분한 최소치
            # tools+system은 매 스텝 동일하므로 prompt caching으로 재사용해
            # 고정 prefix 토큰 비용을 ~90% 절감한다 (마지막 system 블록에 breakpoint).
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOLS,
            tool_choice={"type": "any"},  # 매 스텝 반드시 하나의 action 강제
            messages=[{"role": "user", "content": user_content}],
        )

        for block in response.content:
            if block.type == "tool_use":
                tool_input = dict(block.input)
                return AgentAction(
                    name=block.name,
                    input=tool_input,
                    tool_use_id=block.id,
                    reason=str(tool_input.get("reason", "")),
                )

        # tool_choice=any 를 줬으므로 정상적으로는 도달하지 않음
        raise RuntimeError("Claude가 tool을 호출하지 않았습니다. 응답: " + str(response.stop_reason))
