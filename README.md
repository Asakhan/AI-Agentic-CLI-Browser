# AI Agentic CLI Browser

AI(Claude)가 사용자를 대신해 웹사이트를 탐색하면서 원하는 자료를 찾아 알려주고,
필요한 파일을 다운로드해주는 **CLI 기반 에이전트 브라우저**입니다.

AI가 페이지를 읽고 → 다음에 클릭/이동할 곳을 스스로 판단하고 → 목표를 찾을 때까지
반복 탐색하는 *agentic loop* 구조입니다.

## 주요 특징

- 🔎 **자율 탐색**: 목표를 자연어로 주면 AI가 알아서 링크 이동·검색·스크롤을 수행
- 🧰 **action 세트**: `navigate` / `click` / `fill_form`+`submit` / `download` / `scroll` / `ask_user` / `finish`
- ⚡ **초경량·고속 백엔드 [Lightpanda](https://github.com/lightpanda-io/browser)**: Zig로 작성된 헤드리스
  브라우저에 CDP로 접속(권장). Chrome 대비 메모리 **~16배 적고 ~9배 빠름** → 자료를 더 빨리 찾음.
  (Lightpanda 미사용 시 Playwright 내장 Chromium으로 자동 폴백)
- 🪶 **토큰 최소화 설계**:
  - 페이지를 공격적으로 압축(본문 2,500자·링크 25개 상한)
  - 링크를 **목표 키워드 관련도 순**으로 정렬·선별 → 쓸모없는 링크를 LLM에 안 보냄
  - **prompt caching** 으로 고정 prefix(도구+시스템)를 매 스텝 재사용(고정부 ~90% 절감)
  - `max_tokens`·히스토리 길이 최소화, 최소 스텝으로 직행하도록 유도
- 🔐 **자격증명 비저장**: 아이디/비밀번호는 메모리에만 보관, 디스크·로그·LLM 프롬프트에 평문 노출 안 함
- 💾 **파일 다운로드**: 지정 디렉터리(기본 `./downloads`)에 저장하고 경로를 알려줌
- ♻️ **안전장치**: 최대 스텝 수 제한, 방문 URL 중복 감지

## 동작 흐름

1. 실행하면 **접속할 URL** 을 입력받습니다.
2. 다음으로 **"무엇을 찾고 싶은지(목표)"** 를 자연어로 입력받습니다.
3. AI가 매 스텝마다:
   - 현재 페이지(텍스트·링크·폼)를 정제해 Claude에 전달
   - Claude가 다음 action 하나를 tool 호출로 결정
   - 시스템이 그 action을 브라우저에서 실행하고 결과를 다시 전달
   - 목표 달성 시 `finish` 로 결과를 요약
4. 로그인이 필요하면 AI가 `ask_user` 로 자격증명을 요청합니다. 비밀번호는 터미널에서
   가려진 입력(`getpass`)으로 받아 해당 세션 폼에만 사용합니다.

---

## 산출물(파일 구조)

```
.
├── main.py            # CLI 진입점, agentic loop
├── agent.py           # Claude 호출 + action 결정 로직 (tool 정의)
├── browser.py         # Playwright 래퍼 (navigate/click/fill/download/scroll)
├── page_extractor.py  # HTML 정제·압축 (텍스트+링크+폼)
├── setup_env.py       # 설치 시 API 키 입력 → .env 생성
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 설치 & 실행 가이드

각 OS 공통 순서는 다음과 같습니다.

1. Python 3.10+ 설치 확인
2. 가상환경 생성·활성화
3. 의존성 설치 (`pip install -r requirements.txt`)
4. 브라우저 바이너리 설치 (`playwright install`)
5. API 키 입력 (`python setup_env.py`)
6. 실행 (`python main.py`)

### 1) Python 설치 확인

Python **3.10 이상**이 필요합니다.

| OS | 확인 명령 |
|----|-----------|
| Windows | `python --version` |
| macOS | `python3 --version` |
| Linux | `python3 --version` |

설치가 안 되어 있다면 [python.org](https://www.python.org/downloads/) 또는 패키지 매니저
(`brew install python` / `sudo apt install python3 python3-venv`)로 설치하세요.

### 2) 가상환경 생성·활성화

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
> 실행 정책 오류가 나면: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

**Windows (cmd)**
```bat
python -m venv .venv
.\.venv\Scripts\activate.bat
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3) 의존성 설치

```bash
pip install -r requirements.txt
```

### 4) 브라우저 바이너리 설치 (Playwright)

```bash
playwright install
```
> Playwright는 자동화를 위해 자체 Chromium을 내려받습니다(최초 1회).
>
> **Linux**에서 시스템 라이브러리가 없다는 오류가 나면:
> ```bash
> playwright install-deps        # 또는 sudo playwright install-deps
> ```

### 4-b) (권장) Lightpanda 경량 백엔드 설치·실행

[Lightpanda](https://github.com/lightpanda-io/browser)는 Zig로 작성된 초경량 헤드리스
브라우저로 CDP를 노출합니다. Chrome 대비 메모리 ~16배 적고 ~9배 빨라, 더 적은 자원으로
더 빠르게 탐색할 수 있습니다.

**설치 (택1)**
```bash
# Homebrew (nightly)
brew install lightpanda-io/browser/lightpanda

# 또는 Linux x86_64 바이너리 직접 다운로드
curl -L -o lightpanda https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux
chmod a+x ./lightpanda

# 또는 Docker
docker run -d --name lightpanda -p 127.0.0.1:9222:9222 lightpanda/browser:nightly
```

**서버 실행 (별도 터미널에서 계속 띄워 둠)**
```bash
lightpanda serve --obey-robots --host 127.0.0.1 --port 9222
```
그런 다음 `.env` 에 `AI_BROWSER_CDP_URL=ws://127.0.0.1:9222` 를 설정하면 이 백엔드에
접속합니다. (`setup_env.py` 실행 시 물어봅니다.)

> Lightpanda는 베타이며 일부 사이트/웹 API에서 오류가 날 수 있습니다. 그럴 때는
> `AI_BROWSER_CDP_URL` 을 비우면 로컬 Chromium(Playwright)으로 자동 폴백합니다.

### 5) API 키 입력 → `.env` 생성

대화형 설정 스크립트를 실행하세요.
```bash
python setup_env.py
```
- Anthropic API 키를 가려진 입력으로 받아 `.env` 를 생성합니다.
- 모델/다운로드 경로/헤드리스 여부도 함께 설정할 수 있습니다.
- 수동으로 하려면 `.env.example` 을 복사해 `.env` 로 만든 뒤 키를 채워도 됩니다.
  ```bash
  cp .env.example .env   # Windows: copy .env.example .env
  ```

API 키는 [console.anthropic.com](https://console.anthropic.com/) 에서 발급합니다.

### 6) 실행

```bash
python main.py
```
실행하면 URL과 목표를 차례로 물어봅니다.

#### 사용 예시
```text
접속할 웹사이트 URL: news.ycombinator.com
이 사이트에서 무엇을 찾고 싶으신가요?: 오늘 1위 글의 제목과 링크를 알려줘

스텝 1 · navigate https://news.ycombinator.com  (1위 글을 찾기 위해 메인으로 이동)
  → 'https://news.ycombinator.com' 로 이동했습니다.
스텝 2 · finish  (목표 달성)
✅ 완료
1위 글: "..." (https://...)
```

---

## 환경 변수 (.env)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | (필수) | Anthropic API 키 |
| `AI_BROWSER_MODEL` | `claude-sonnet-4-6` | 사용할 Claude 모델 |
| `AI_BROWSER_DOWNLOAD_DIR` | `./downloads` | 다운로드 저장 폴더 |
| `AI_BROWSER_HEADLESS` | `true` | 브라우저 창 숨김 여부 (로컬 Chromium에서만 의미, `false`면 창이 보임) |
| `AI_BROWSER_MAX_STEPS` | `25` | 최대 탐색 스텝 수 |
| `AI_BROWSER_CDP_URL` | (없음) | Lightpanda 등 CDP 엔드포인트. 지정 시 접속(권장). 미지정 시 로컬 Chromium |

---

## 보안 / 주의사항

- 🔐 **로그인 정보는 저장되지 않습니다.** 아이디/비밀번호는 프로세스 메모리에만
  존재하며 종료 시 사라집니다. 디스크·로그·LLM 프롬프트에 평문으로 남기지 않습니다
  (Claude에는 `{{password}}` 같은 플레이스홀더만 전달).
- 🗝 **API 키**는 `.env` 로 관리하며 `.gitignore` 에 포함되어 커밋되지 않습니다.
  키나 `downloads/` 를 실수로 커밋하지 않도록 주의하세요.
- 📁 **다운로드 위치**는 기본 `./downloads/` 이며 저장 후 전체 경로를 출력합니다.
- ⚠️ 본인이 접근 권한을 가진 사이트에서만, 각 사이트의 이용약관·robots 정책을
  준수해 사용하세요. 자동화가 차단되거나 캡차가 나오는 사이트가 있을 수 있습니다.

---

## 자주 발생하는 오류

| 증상 | 원인 / 해결 |
|------|-------------|
| `ANTHROPIC_API_KEY 가 설정되지 않았습니다` | `python setup_env.py` 실행 또는 `.env` 에 키 추가 |
| `브라우저를 시작하지 못했습니다 … playwright install` | `playwright install` (Linux는 `playwright install-deps`도) |
| `Claude API 호출 실패` | 키 유효성·요금 한도·네트워크 확인 |
| `Lightpanda 서버에 연결하지 못했습니다` | `lightpanda serve …` 가 실행 중인지, `AI_BROWSER_CDP_URL` 주소가 맞는지 확인 |
| Lightpanda에서 특정 사이트 오류/크래시 | 베타 한계. `AI_BROWSER_CDP_URL` 을 비워 로컬 Chromium으로 폴백 |
| 다운로드가 시작되지 않음 | 링크가 실제 파일 다운로드를 유발하는지 확인 (목표를 더 구체적으로) |
| 캡차/봇 차단 | 헤드풀 모드(`AI_BROWSER_HEADLESS=false`)로 직접 확인 권장 |

---

## 라이선스

이 프로젝트는 **GNU Affero General Public License v3.0 (AGPL-3.0)** 으로 배포됩니다.
전문은 [`LICENSE`](./LICENSE) 파일을 참조하세요.

AGPL-3.0의 핵심: 수정본을 네트워크 서비스로 제공하는 경우에도 해당 소스 코드를
이용자에게 제공해야 합니다. 본 프로젝트는 동일하게 AGPL-3.0인
[Lightpanda](https://github.com/lightpanda-io/browser)와의 연동을 전제로 하며,
라이선스 정합성을 위해 AGPL-3.0을 채택했습니다.
