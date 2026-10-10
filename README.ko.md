# Ollama Translator

[![CI](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml/badge.svg)](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [Français](README.fr.md) | [Español](README.es.md) | [Deutsch](README.de.md) | **한국어**

책 한 권을 통째로 번역하는 로컬 파이프라인입니다. 중단된 지점부터 이어서 실행할 수
있으며, 영어→중국어와 중국어→영어 문학 번역에 맞춰 다듬어져 있습니다. EPUB, RTF,
텍스트, Markdown, HTML, Word(.docx), 텍스트 PDF 파일을 입력으로 받아 번역본을 원본과
같은 형식으로 돌려주며, EPUB, PDF, Word, HTML, Markdown, 텍스트로도 내보낼 수 있습니다. 자막 파일
(.srt, .vtt, .ass)도 타이밍을 그대로 둔 채 큐 단위로 번역합니다. 모든 처리는 로컬
Ollama 모델만으로 이루어집니다. 클라우드 API나 MCP 서버가 필요 없고, LangGraph나
AutoGen 같은 에이전트 프레임워크도 쓰지 않습니다.

## 어떤 문제를 해결하나

책 한 권을 번역할 때 생기는 실패는 모델을 한 번 호출할 때와 양상이 전혀 다릅니다.
대형 언어 모델로 장별 번역을 그대로 돌리면 보통 다음과 같은 문제를 만납니다.

- **용어 흔들림**: 같은 인명이나 지명이 3장과 20장에서 서로 다르게 번역됩니다.
- **구조 손상**: `<em>`, `<i>` 같은 인라인 태그가 사라져 EPUB의 조판이 깨집니다.
- **수량 왜곡**: 숫자, 단위, 거리, 시간이 슬그머니 바뀌고, 사람 눈으로는 찾기
  어렵습니다.
- **고칠수록 나빠짐**: 모델이 원래 맞던 문장을 "교정"해 더 나쁜 번역문으로
  바꿔 놓습니다.
- **중단되면 처음부터**: 40장에서 오류가 나면 처음부터 다시 돌려야 합니다.

이 프로젝트는 프롬프트를 더 길게 쓰는 데 기대지 않고, 위 문제를 각각 독립된
단계로 나누어 처리하며 단계마다 체크포인트를 남깁니다.

## 동작 방식

```text
decompile
  -> extract_glossary -> resolve_glossary -> approve_glossary
  -> build_story_context (optional)
  -> preprocess -> translate
  -> audit_translation -> audit_consistency -> repair_translation
  -> reprose_translation
  -> review_repaired -> repair_review -> validate_repaired
  -> translate_title -> compile -> validate_epub
```

모든 단계는 산출물을 `runs/<job-id>/` 아래의 작업 공간에 체크포인트로 남깁니다.
`resume`을 실행하면 이미 완료되어 검증까지 통과한 작업은 건너뛰므로, 도중에 멈춘
작업은 처음이 아니라 영향을 받은 가장 앞 단계부터 이어집니다.

## 예시: 《이상한 나라의 앨리스》

저장소에 포함된 프로젝트 구텐베르크 샘플 책으로 영어→중국어 간체 번역 과정을
처음부터 끝까지 돌려 볼 수 있습니다. 아래 과정은 모두 로컬 브라우저 대시보드에서
진행합니다.

```powershell
book-agent ui          # http://127.0.0.1:8765/
```

> 대시보드 화면이 아직 바뀌고 있어 스크린샷은 생략했습니다. 화면이 안정되면 다시
> 찍어 넣을 예정입니다.

**1. 설정과 시작.** 작업 페이지에는 모든 작업이 한 페이지에 10개씩 나옵니다.
*새 작업 추가*에서는 원본 책(알려진 파일에서 고르거나, 찾아보거나, 끌어다 놓기)과
설정 파일 이름만 정하면 되고, 작업 ID는 기본값으로 설정 파일 이름을 따릅니다.
[`configs/demo-alice.yaml`](configs/demo-alice.yaml)처럼 이미 있는 설정은 그대로
쓰고, 새 이름을 주면 `config.example.yaml`을 바탕으로 만듭니다. 작업을 열면
**설정** 탭이 먼저 나타납니다. *옵션*에는 자주 바꾸는 설정(번역 스타일, 역할별
모델과 설치 여부 ✓/✗, 용어집 승인 방식, 품질 검사, 출력)이 모여 있고, *전체 설정*은
모든 설정 항목을 타입, 허용 범위, 기본값, 초기화 버튼과 함께 보여 주며, *YAML*에서는
파일을 직접 편집합니다. 세 화면은 항상 서로 동기화됩니다. *검증*은 설정을 저장하고
검사한 뒤 시험 실행을 하고, 필요한 모델이 모두 설치되어 있는지 Ollama에 확인합니다.
작업 헤더의 *번역 시작*은 검증을 통과한 뒤 설정을 바꾸지 않은 동안에만 누를 수
있습니다. 실행 중에는 헤더에 *일시 중지*(진행 중인 LLM 호출이 끝난 뒤 멈춤)와
*중지*(즉시 종료)가 나타나고, 어느 쪽이든 *이어서 실행*으로 마지막 체크포인트부터
계속할 수 있습니다. 예시 설정은 로컬에 설치된 모델만 사용하며, 문체 다듬기와 EPUB
정리를 켜고 용어집 승인을 모델에 맡깁니다. 터미널에서 같은 작업을 돌리려면
`.\scripts\demo-alice.ps1`을 실행합니다(`-Resume`을 붙이면 이어서 실행).

**2. 용어집 승인.** `workflow.llm_glossary_review: true`이면 근거가 있고 신뢰도가
높으며 충돌이 없는 항목은 바로 승인되고, 나머지는 LLM이 검토합니다. 용어집
페이지에는 승인된 용어와 함께 LLM이 어떤 항목을 왜 검토했고 무엇을 바꿨는지가
표시됩니다. 사람이 승인하도록 설정했다면 작업이 여기서 일시 중지되고 같은 페이지가
편집기로 바뀝니다. 번역어, 분류, 메모, 별칭을 고치거나 항목을 거부할 수 있고,
주의가 필요한 항목(일반 단어, 번역어 중복, 낮은 신뢰도, 근거 없음)만 골라 보거나
용어별 원문 예문을 확인할 수 있습니다. 직접 고친 버전을 승인해도 되고, 그 버전이나
손대지 않은 초안을 LLM 검토에 넘겨도 됩니다. 어느 쪽이든 파이프라인은 이어서
진행됩니다.

**3. 번역, 검수, 교정.** 진행 상황 페이지는 대시보드에서 시작했든 터미널에서
시작했든 작업을 실시간으로 따라갑니다. 단계별 상태, 계획된 LLM 태스크 수, 현재 단위,
호출 횟수, 출력 토큰, 소요 시간과 함께 진행 중인 LLM 호출과 세션 로그를 보여
줍니다. 이 책은 컴파일 직전에 일시 중지되었고, 파이프라인이 스스로 확인하지 못한
세그먼트 세 개가 남았습니다.

**4. 최종 검토.** 최종 검토 페이지(`book-agent review-ui .\runs\demo-alice-en-zh`로
바로 열 수 있음)에서 검토 대기열을 처리합니다. 세그먼트마다 원문과 앞뒤 문맥,
지적 사항(누르면 인용된 부분이 강조됨), 파이프라인이 만든 이전 버전들, 그리고
실시간 차이 비교가 붙은 편집기가 표시됩니다. 편집기는 `resolve-review`와 같은
결정적 검사를 수행합니다. 결정을 적용하면 초안이 승인되고, 이어서 EPUB을 컴파일하고
검증합니다.

**5. 책 전체를 읽고 수정하기.** **본문** 탭은 모든 장을 원문과 번역문이 나란히
놓인 행으로 보여 줍니다. 어떤 세그먼트든 그 자리에서 수정할 수 있으며, 사유를
적어야 하고 같은 결정적 검사와 차이 비교를 거칩니다. 최종 검토에서 내린 결정을
포함해 모든 변경은 하나의 수정 로그에 이벤트로 기록되고 다시 실행해도 지워지지
않으므로, 되돌리거나 이력을 살펴볼 수 있습니다. 다시 실행한 결과가 수정한
세그먼트의 번역문을 바꾸면 그 세그먼트는 충돌이 되며, 어느 쪽을 남길지는 직접
정합니다. *다시 컴파일*을 누르면 수정을 반영한 책이 몇 초 만에 다시 만들어집니다.
**⤓ XLIFF 내보내기**는 책을 CAT 도구로 넘기고, **⤒ XLIFF 가져오기**는 번역가가
돌려준 파일을 미리 보기로 확인한 뒤에만 기록합니다.

## 설계 원칙

- **검증 가능한 구조는 모델이 아니라 코드가 다룬다**: EPUB 구조, 인라인 마커, 각종
  식별자는 모두 결정적 코드로 처리하고 모델에 맡기지 않습니다.
- **확신 없는 교정보다는 보류**: 검증을 통과하지 못한 교정은 버리며, 기존의 더 나은
  번역문을 덮어쓰지 못합니다. 해당 세그먼트는 사람의 검토로 넘어갑니다.
- **재시도에는 상한이 있다**: 검증 결과가 더 이상 수렴하지 않으면 같은 실패를 두고
  모델을 계속 호출하지 않고 멈춥니다.
- **범용 처리만 한다**: 실패 유형은 모두 일반 규칙으로 처리하며, 특정 책이나 특정
  구절을 위한 패치를 코드에 넣지 않습니다.
- **컨텍스트는 필요한 만큼만 할당**: 설정의 131K는 상한이지 매번 할당하는 크기가
  아닙니다. 일반 요청은 16K, 국소 교정과 교정 검증은 8K에서 시작해 필요할 때만
  16K / 32K / 64K / 131K로 늘어나므로 GPU 메모리 사용량이 요청 크기에 비례합니다.

## 빠른 시작

### 1. Ollama와 모델 준비

로컬에서 Ollama가 실행 중인지 확인하고, 설정에서 쓰는 모델을 받아 둡니다.

```powershell
ollama pull qwen3.8:latest
ollama pull gemma4:31b
```

모델을 호출하는 첫 단계가 시작되기 전에 CLI가 모델 이름과 설정된 컨텍스트 용량을
먼저 확인합니다.

### 2. 설치

```powershell
python -m pip install -e .
book-agent --version
```

requirements 파일로 설치할 수도 있습니다(의존성 선언의 기준은 `pyproject.toml`).

```powershell
python -m pip install -r requirements.txt
python -m book_agent --version
```

Python 3.11 이상과 `ollama>=0.6.2`가 필요합니다.

### 3. 설정 복사와 확인

```powershell
copy config.example.yaml my-book.yaml
book-agent config --file .\my-book.yaml
```

`config.example.yaml`은 기본값을 나열한 파일이 아니라 보수적으로 잡은 운영용 출발
설정이며, 일부 값은 코드 기본값과 일부러 다르게 되어 있습니다. 모든 키는 생략할 수
있고, 생략하면 `book_agent/config.py`의 기본값이 적용됩니다.

### 4. 시험 실행 후 본 실행

시험 실행은 파일을 쓰지 않고 Ollama에도 접속하지 않습니다.

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --dry-run
```

문제가 없으면 실제로 실행합니다.

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --job-id "my-book-en-zh"
```

명령이 작업 공간 경로를 출력하니 적어 두십시오. 이후의 모든 명령은 이 디렉터리를
대상으로 합니다. RTF를 비롯한 다른 입력 형식도 같은 명령과 같은 파이프라인을
사용합니다.

### 5. 상태 확인과 이어서 실행

```powershell
book-agent status .\runs\my-book-en-zh
book-agent resume .\runs\my-book-en-zh --plain
```

`--plain`은 로그에 남기기 좋은 형식으로 출력할 뿐 파이프라인 동작은 바꾸지
않습니다.

## 언어 쌍

영어→중국어 간체(`en-zh`)와 그 반대(`zh-en`)는 가장 세밀하게 튜닝된 쌍으로, 모든
검사와 용어집, 스타일 시트, 문체 다듬기가 동작합니다. 프랑스어(`fr`), 일본어(`ja`),
스페인어(`es`), 독일어(`de`), 한국어(`ko`)는 프로필이 마련되어 있어 구두점 관례,
호칭, 수사, 프롬프트 예시가 반영되어 있으며, 어떤 언어와 짝지어도 됩니다. 그 밖의
언어는 일반 등급으로 받아들이고, 해당 언어에서 지원할 수 없는 검사는 영어나 중국어
규칙으로 돌리지 않고 건너뜁니다. 언어 쌍은 다음 두 가지 방식으로 적습니다.

```yaml
translation:
  direction: en>ja          # or: source_language: en / target_language: ja
```

대시보드에서는 작업의 설정 탭에 있는 *언어* 항목이나 시리즈를 만들 때 두 언어를
고릅니다. 영어·중국어 이외의 쌍에서 번역 품질은 모델에 따라 달라집니다
([docs/GENERIC_LANGUAGES.md](docs/GENERIC_LANGUAGES.md), 영문).

## 모델 역할 분담

기본 분담은 보수적이며, 단계마다 모델을 따로 지정할 수 있습니다.

| 작업 | 기본 모델 |
|---|---|
| 용어집 후보 추출 | `qwen3.8:27b` |
| 용어 정리와 검토 | `qwen3.8:latest` |
| 초벌 번역과 국소 교정 | `qwen3.8:latest` |
| 의미 검수 | `gemma4:31b` |
| 수량 검증과 판정 | `gemma4:26b`, 필요하면 `gemma4:31b`로 상향 |
| 교정 비교와 제한적 검증 | `gemma4:26b` |
| 문체 다듬기 제안 | `qwen3.8:latest` |
| 문체 다듬기 검증 | `gemma4:31b` |

구조화된 JSON을 받는 검수·검증 호출은 기본적으로 thinking을 끕니다. 품질이
나아진다는 것을 실제로 확인한 경우가 아니면 켜지 않는 편이 좋습니다.

## 검토와 이어서 실행

기본값은 `require_glossary_review: true`로, 용어집 초안을 만든 뒤 일시 중지하고
사람의 확인을 기다립니다.

```powershell
book-agent approve "D:\runs\my-job" --glossary "D:\reviews\glossary.txt" --resume
```

스키마 제약이 걸린 LLM 검토로 대신하고 곧바로 계속할 수도 있습니다.

```powershell
book-agent approve "D:\runs\my-job" --llm-glossary --resume
```

LLM 검토자는 항목을 고치거나 삭제할 수는 있지만 없던 영어 용어를 새로 만들 수는
없으며, 프롬프트/모델 해시, 시도 횟수, 검토 방식, 산출물이 기록됩니다.

### 브라우저 대시보드

`book-agent ui`는 로컬 주소에서만 접속할 수 있는 대시보드를 띄웁니다(`--runs`,
`--configs`, `--port`, `--no-browser` 옵션). 위 명령들과 같은 게이트를 다룹니다.

인터페이스는 영어, 중국어 간체, 일본어, 프랑스어, 스페인어, 독일어, 한국어로
제공됩니다. 파이프라인에 프로필이 있는 언어들입니다. 헤더 오른쪽 메뉴에서 고르며,
선택은 브라우저에 저장되고 기본값은 영어입니다. 인터페이스 언어는 작업의 번역
방향과 무관합니다([대시보드 현지화](docs/LOCALIZATION.md), 영문).

- **작업**: 작업을 번역 방향(예: `EN → ZH`)과 함께 나열하고 새 작업을 만듭니다.
  완료된 작업에는 목록과 작업 헤더 양쪽에 *다운로드* 버튼이 있고, 그 옆에 형식
  메뉴가 있습니다. 메뉴는 작업이 돌려주는 형식(설정의 `output.format`, 기본값은
  원본 파일의 형식)에서 시작합니다. 책은 EPUB, PDF, Word, HTML, Markdown, 텍스트로,
  자막 작업은 SRT, WebVTT, ASS로 받을 수 있으며, 파일 본래의 형식이 아닌 자막
  형식을 고르면 옮겨지지 않는 내용을 대시보드가 알려 줍니다. 시작된 작업의 설정은
  *잠금 해제 후 편집*을 누르기 전까지 읽기 전용입니다(실행 중에는 해제할 수 없음).
  해제하면 각 변경이 파이프라인에 미치는 영향이 표시되고, 처음 영향을 받는 단계부터
  다시 실행하면서 저장합니다. 작업의
  **설정** 탭에서 설정을 편집하고 검증하고 작업을 시작합니다. **시리즈**는 버전이
  관리되는 시리즈 용어집을 공유하는 작업들을 묶습니다. 작업 헤더에서 실행을 일시
  중지(진행 중인 LLM 호출이 끝난 뒤)하거나 중지하거나 이어서 실행할 수 있습니다.
  터미널에서 시작한 작업은 `book-agent pause <workspace>`로 같은 방식으로 일시
  중지합니다.
- **진행 상황**: `state.sqlite3`와 세션 로그를 바탕으로 `--runs` 아래의 모든 작업을
  따라갑니다. 실패했거나 일시 중지된 단계에는 *이어서 실행*(끝난 작업은 유지)과
  *다시 실행*이, 완료된 단계에는 *여기서부터 다시 실행*이 있습니다. 다시 실행
  (`retry --stage X --resume`)하기 전에는 어떤 단계가 다시 수행되고 무엇이
  사라지는지 먼저 알려 줍니다. *최근 변경* 열은 각 단계에 마지막으로 일어난 일
  (시작, 완료, 실패, 중지, 일시 중지, 검토 대기, 초기화)과 그 시각을 보여 줍니다.
- **용어집**: 일시 중지된 용어집을 편집하고 승인하거나 LLM 검토에 넘깁니다
  (`approve --glossary` / `--llm-glossary`에 해당).
- **본문**: 책을 장별로, 원문과 번역문이 나란히 놓인 행으로 보여 주며, 지적된
  세그먼트, 검토 대기열에 있는 세그먼트, 수정된 세그먼트, 충돌이 있는 세그먼트만
  골라 볼 수 있습니다. `validate_repaired`가 끝나면 어떤 세그먼트든 그 자리에서
  수정할 수 있습니다. 저장하려면 사유가 필요하고 결정적 검사를 통과해야 합니다
  (구조나 마커 손상, 빈 번역문, 미번역, 중복 텍스트, 구두점 오류는 저장이 막히고,
  그 밖의 지적 사항은 예외 사유를 적어야 함). 수정은
  `edits/segment-edits.jsonl`에 기록됩니다. 어느 단계에도 속하지 않는 추가 전용
  로그라서 다시 실행해도 남습니다. 컴파일할 때 검증된 초안 위에 수정이 적용되며,
  수정이 책보다 최신이면 *다시 컴파일*로 책을 다시 만듭니다. 다시 실행한 결과가
  수정한 세그먼트의 번역문을 바꾸면 그 세그먼트는 충돌이 되고, 자신의 수정을
  유지할지 새 번역문을 받아들일지 정하기 전에는 컴파일할 수 없습니다. 수정마다
  이력이 있고 되돌릴 수 있습니다. **⤓ XLIFF 내보내기**는 책을 XLIFF 2.1로
  내려받고, **⤒ XLIFF 가져오기**는 번역된 파일을 세그먼트별로 미리 보여 준 뒤
  (단위 ID가 같고 원문이 바뀌지 않은 경우에만 일치) 확인한 것만 하나의 사유로
  수정으로 기록합니다([XLIFF 가져오기](docs/XLIFF_IMPORT.md), 영문).
- **최종 검토**: 사람의 검토 대기열을 `resolve-review`와 같은 검증으로 처리합니다.
  세그먼트를 그대로 수락하려면 미리 준비된 사유를 고르거나 직접 적어야 합니다.
  결정은 본문 탭의 수정과 같은 수정 로그에 기록되며, 검토 대기열에 있는 세그먼트를
  본문 탭에서 수정해도 처리된 것으로 봅니다. 결정하지 않은 세그먼트 수가
  `workflow.compile_max_unresolved_review_segments` 이내로 줄면, 결정한 것들을
  적용하고 최종 초안을 승인할지 묻습니다. `book-agent review-ui <workspace>`로
  이 페이지를 바로 열 수 있습니다.

대시보드에서 예기치 않은 오류가 나면 빈 화면 대신 오류 메시지, 기술 정보,
*다시 불러오기* 버튼이 담긴 배너가 표시됩니다.

수정 내역은 명령줄에서 확인하거나 XLIFF 2.1로 내보낼 수 있습니다.

```powershell
book-agent edits "D:\runs\my-job"
book-agent edits "D:\runs\my-job" --export xliff --output my-job.xlf
```

파이프라인의 `audit_consistency` 단계(기본으로 켜져 있고 모델을 호출하지 않음)는
책 전체의 일관성을 검사합니다. 책에서 반복되는 문장과 대사는 어디서나 똑같이
번역되어야 하고, 구두점은 그 책 자체의 관례를 따라야 합니다. 어긋난 곳은 교정
단계로 넘어가고, 교정하지 못한 것은 검토 대기열에 들어갑니다. 또한
`consistency.style_sheet.enabled: true`로 설정하면 용어집을 추출할 때 **도서 스타일
시트**(반복되는 표현과 인물별 메모)도 함께 추출합니다. 스타일 시트는 용어집과 같은
게이트에서 사람이 검토하며(용어집 탭의 "스타일 시트" 영역, 또는
`approve --style FILE`), 용어집 자체를 LLM이 검토하는 경우에도 마찬가지입니다.
반복되는 표현은 책 전체에서 똑같이 번역됩니다. 인물 메모는 참고용일 뿐이고, 대명사와
호칭(你/您)은 원문의 표현과 장면에 따라 정해집니다.
`consistency.story_context.enabled: true`로 설정하면 선택 단계인
`build_story_context`가 추가됩니다. 장마다 짧은 요약을 만들어(장당 약 6초) 번역할
때 각 청크에 "지금까지의 줄거리"를 참고 정보로만 전달하며, 요약 자체를 번역하지는
않습니다.
자세한 내용은 [책 단위 일관성](docs/BOOK_CONSISTENCY.md)(영문)을 참고하십시오.

대시보드에서 시작한 작업은 일반 CLI를 자식 프로세스로 실행하므로 로그와
체크포인트가 터미널에서 실행한 것과 똑같고, 대시보드를 닫아도 작업은 계속됩니다.

대시보드 프런트엔드는 `frontend/`에 있는 React + TypeScript 앱입니다. 빌드 결과물이
`book_agent/web/static/`에 커밋되어 있어 패키지를 설치할 때 Node.js가 필요 없습니다.
인터페이스를 고치려면(Node 24):

```powershell
cd frontend
npm ci
npm run dev      # hot-reloading UI; proxies /api to a running `book-agent ui`
npm test         # helper unit tests and component tests (jsdom + Testing Library)
npm run build    # type-check and rebuild book_agent/web/static; commit the result
```

## 종료 코드

| 종료 코드 | 의미 |
|---:|---|
| `0` | 명령이 성공했거나 워크플로가 정상 종료됨 |
| `1` | 검증, 설정, 모델, 단계 또는 실행 실패 |
| `2` | 워크플로가 검토 게이트에서 일시 중지됨 |
| `130` | 사용자가 취소했거나 모델 생성이 협조적으로 중단됨 |

## 테스트

테스트 스위트는 오프라인으로 동작하도록 만들어져 있어 로컬 모델 없이 실행할 수
있습니다.

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

## 추가 문서

전체 명령 참조, 설정 항목별 설명, 운영 세부 사항은 현재 영어로 관리합니다.

- [English README](README.md)
- [운영 가이드](docs/OPERATIONS.md)
- [아키텍처 설계](docs/DESIGN.md)
- [책 단위 일관성 제안서(영문)](docs/BOOK_CONSISTENCY.md)
- [대시보드 현지화(영문)](docs/LOCALIZATION.md)
- [대시보드 단계 제어: 이어서 실행, 다시 실행, 조기 승인(영문)](docs/STAGE_CONTROL.md)
- [전문 검토와 수동 수정 추적(영문)](docs/FULL_TEXT_REVIEW.md)
- [XLIFF 가져오기(영문)](docs/XLIFF_IMPORT.md)
- [대시보드의 시리즈 용어집(영문)](docs/SERIES_GLOSSARY_UI.md)
- [텍스트, Markdown, HTML, Word 도서(영문)](docs/FORMAT_SUPPORT.md)
- [작업 계획](docs/PLAN.md)
- [추론 프레임워크 벤치마크 계획(미실행)](docs/FRAMEWORK_BENCHMARK_PLAN.md)

## 라이선스

코드와 문서는 [MIT 라이선스](LICENSE)로 배포됩니다. `sample/` 디렉터리의 EPUB은
프로젝트 구텐베르크에서 가져온 것으로, 파일에 포함된 자체 조건을 그대로 따르며 MIT
허가 범위에 들어가지 않습니다. 자세한 내용은 [sample/README.md](sample/README.md)를
참고하십시오. Ollama 모델은 이 저장소에서 배포하지 않으며 각 모델의 라이선스를
따릅니다.
