# 근거 출처 (References)

이 가이드의 각 원칙이 근거로 삼은 공식 문서 목록.
(전체 최종 확인일: 2026-07-14)

## 직접 확인한 공식 문서

- **OpenAI Codex — Custom instructions with AGENTS.md**
  https://learn.chatgpt.com/docs/agent-configuration/agents-md
  - Codex는 작업 전 `AGENTS.md`를 읽고, Git 루트에서 현재 디렉터리까지 지시 파일을 조합한다.
  - 더 가까운 디렉터리의 지시가 더 나중에 병합되어 우선한다.
  - 기본 합산 크기 한도가 있으므로 핵심 지시를 간결하게 유지해야 한다.

- **OpenAI Codex — Agent approvals & security**
  https://learn.chatgpt.com/docs/agent-approvals-security
  - 샌드박스는 기술적으로 가능한 작업 범위를, 승인 정책은 AI가 언제 멈추고 확인해야 하는지를 결정한다.
  - 지시 문서는 이런 기술적 권한 제한을 대체하지 않는다.

- **Anthropic — Reduce hallucinations**
  https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations
  - "모른다"고 말할 권한을 명시적으로 부여하면 거짓 정보가 크게 줄어든다.
  - 사실 주장에는 인용·출처를 붙이고, 근거를 찾지 못한 주장은 철회하게 한다.
  - 제공된 문서에 기반해 답하게 하고 일반 지식 사용을 제한한다.
  - 이 기법들은 환각을 줄일 뿐 완전히 제거하지 못하므로 중요 정보는 사람이 검증한다.

- **Anthropic — Prompting best practices**
  https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
  - 명확하고 직접적으로: AI를 맥락 없는 신입 직원으로 간주하고 쓴다.
  - 골든 룰: 맥락 없는 동료가 프롬프트를 읽고 따라 할 수 있으면 AI도 할 수 있다.
  - 규칙에 이유(왜)를 함께 적으면 AI가 일반화한다.
  - 예시(few-shot) 3~5개가 형식·톤 제어에 가장 확실하다.
  - 하지 말라는 것보다 해야 할 것을 명시한다.
  - 순서가 중요한 절차는 번호 목록으로 쓴다.

- **Anthropic — Prompt engineering overview**
  https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview
  - 프롬프트 개선 전에 성공 기준과 검증 방법을 먼저 정의한다.

- **Claude Code — How Claude remembers your project (CLAUDE.md / memory)**
  https://code.claude.com/docs/en/memory
  - 지시 파일은 200줄 이하 목표. 길수록 컨텍스트 낭비와 준수율 하락.
  - 검증 가능한 수준의 구체성 ("스페이스 2칸" vs "잘 정리해라").
  - 모순 규칙이 있으면 AI가 임의 선택 → 주기적 검토·삭제 필요.
  - 지시 파일은 맥락이지 강제 장치가 아니다. 강제는 hook/permissions로.
  - AGENTS.md를 쓰는 저장소는 CLAUDE.md에서 `@AGENTS.md` import 또는 심볼릭 링크로 단일 원본 유지.

- **AGENTS.md — 코딩 에이전트용 개방형 가이드 파일**
  https://agents.md/
  - "AGENTS.md is just standard Markdown" — 표준 마크다운이며 헤딩을 자유롭게 쓴다.
  - OpenAI Codex, Google Jules, Cursor, Aider, GitHub Copilot Coding Agent, Devin,
    Windsurf, VS Code, Zed 등이 지원. 6만 개 이상의 오픈소스 프로젝트가 사용 중.
  - 권장 내용: 프로젝트 개요, 셋업/빌드/테스트 명령, 코드 스타일, 보안 고려사항, 배포/PR 규칙.

- **llms.txt — LLM 친화적 문서 제안**
  https://llmstxt.org/
  - "LLM이 가장 널리, 쉽게 이해하는 포맷은 마크다운"이라는 전제의 표준 제안.
  - 작성 지침: 간결하고 명확한 언어, 짧고 유익한 설명, 모호한 용어와 설명 없는
    전문용어 회피, 여러 LLM으로 내용이 제대로 이해되는지 테스트.
